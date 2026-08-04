#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local PatternLab playback service using FluidSynth and an SF2 SoundFont.

Version: 260804c
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from mido import MidiFile
except ImportError:
    MidiFile = None

SCRIPT_NAME = "play_server.py"
VERSION = "260804c"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"

HOST = "127.0.0.1"
DEFAULT_PORT = 8123
MAX_MIDI_BYTES = 16 * 1024 * 1024
DEFAULT_FLUIDSYNTH = Path(r"C:\Tools\FluidSynth\bin\fluidsynth.exe")
DEFAULT_SOUNDFONT = Path(r"C:\SoundFonts\GeneralUser-GS.sf2")


class PlayerState:
    def __init__(self, fluidsynth: Path, soundfont: Path, audio_driver: str) -> None:
        self.fluidsynth = fluidsynth
        self.soundfont = soundfont
        self.audio_driver = audio_driver
        self.lock = threading.RLock()
        self.process: subprocess.Popen[bytes] | None = None
        self.temp_midi: Path | None = None

    def _delete_temp(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def stop(self) -> None:
        with self.lock:
            process = self.process
            midi_path = self.temp_midi
            self.process = None
            self.temp_midi = None

        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._delete_temp(midi_path)

    def _cleanup_after_exit(self, process: subprocess.Popen[bytes], midi_path: Path) -> None:
        process.wait()
        with self.lock:
            if self.process is process:
                self.process = None
                self.temp_midi = None
        self._delete_temp(midi_path)

    def play(self, midi_bytes: bytes) -> None:
        self.stop()
        with tempfile.NamedTemporaryFile(prefix="adx_compare_", suffix=".mid", delete=False) as fp:
            fp.write(midi_bytes)
            midi_path = Path(fp.name)

        command = [
            str(self.fluidsynth),
            "-a", self.audio_driver,
            "-ni",
            str(self.soundfont),
            str(midi_path),
        ]
        try:
            process = subprocess.Popen(command)
        except Exception:
            self._delete_temp(midi_path)
            raise

        with self.lock:
            self.process = process
            self.temp_midi = midi_path

        threading.Thread(
            target=self._cleanup_after_exit,
            args=(process, midi_path),
            daemon=True,
        ).start()

    def play_path(self, midi_path: Path) -> None:
        self.stop()
        command = [
            str(self.fluidsynth),
            "-a", self.audio_driver,
            "-ni",
            str(self.soundfont),
            str(midi_path),
        ]
        process = subprocess.Popen(command)
        with self.lock:
            self.process = process
            self.temp_midi = None

        def clear_after_exit() -> None:
            process.wait()
            with self.lock:
                if self.process is process:
                    self.process = None

        threading.Thread(target=clear_after_exit, daemon=True).start()


def midi_duration_seconds(path: Path) -> float | None:
    if MidiFile is None:
        return None
    try:
        return max(0.0, float(MidiFile(path).length))
    except Exception:
        return None


class MidiLibrary:
    """Expose only approved MIDI metadata and opaque per-server IDs."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.secret = secrets.token_bytes(32)
        self.lock = threading.RLock()
        self.by_id: dict[str, Path] = {}

    def _opaque_id(self, path: Path) -> str:
        # The browser never receives the filesystem path. The random per-run
        # secret also prevents a filename from being guessed from its ID.
        stat = path.stat()
        material = f"{path.name}\0{stat.st_size}\0{stat.st_mtime_ns}".encode("utf-8", "surrogatepass")
        digest = hashlib.blake2s(material, key=self.secret, digest_size=12).hexdigest()
        return f"midi-{digest}"

    def refresh(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        mapping: dict[str, Path] = {}
        for path in sorted(self.directory.iterdir(), key=lambda p: p.name.casefold()):
            # First version deliberately exposes only regular, non-symlink MIDI
            # files located directly in the configured directory.
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".mid", ".midi"}:
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(self.directory)
            except ValueError:
                continue
            file_id = self._opaque_id(resolved)
            mapping[file_id] = resolved
            stat = resolved.stat()
            rows.append({
                "id": file_id,
                "name": resolved.name,
                "size": stat.st_size,
                "duration_seconds": midi_duration_seconds(resolved),
            })
        with self.lock:
            self.by_id = mapping
        return rows

    def resolve(self, file_id: str) -> Path:
        if not isinstance(file_id, str) or not file_id.startswith("midi-"):
            raise ValueError("invalid MIDI file ID")
        with self.lock:
            path = self.by_id.get(file_id)
        if path is None:
            # Refresh once so files added after server startup can be played.
            self.refresh()
            with self.lock:
                path = self.by_id.get(file_id)
        if path is None:
            raise ValueError("unknown or expired MIDI file ID")
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".mid", ".midi"}:
            raise ValueError("MIDI file is no longer available")
        resolved = path.resolve()
        try:
            resolved.relative_to(self.directory)
        except ValueError as exc:
            raise ValueError("MIDI file is outside the allowed directory") from exc
        return resolved


def make_handler(player: PlayerState, directory: Path, library: MidiLibrary):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def _send_text(self, status: int, message: str) -> None:
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, value: object) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def list_directory(self, path):
            # SimpleHTTPRequestHandler normally exposes a directory index.
            # PatternLab never needs that capability.
            self.send_error(403, "Directory listing is disabled")
            return None

        def send_head(self):
            # The generated report is self-contained. Serve HTML only; do not
            # turn the playback service into a general local-file web server.
            request_path = urlparse(self.path).path
            if request_path.endswith("/"):
                return self.list_directory(str(directory))
            suffix = Path(request_path).suffix.lower()
            if suffix not in {".html", ".htm"}:
                self.send_error(403, "Only PatternLab HTML reports are served")
                return None
            return super().send_head()

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/midi-files":
                self._send_json(200, {"files": library.refresh()})
                return
            super().do_GET()

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/stop":
                player.stop()
                self._send_text(200, "Stopped")
                return

            if path == "/play-file":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 65536:
                        raise ValueError("invalid request size")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    file_id = payload.get("id") if isinstance(payload, dict) else None
                    if not isinstance(file_id, str) or not file_id:
                        raise ValueError("missing MIDI file ID")
                    midi_path = library.resolve(file_id)
                    player.play_path(midi_path)
                    self._send_json(200, {
                        "status": "playing",
                        "id": file_id,
                        "name": midi_path.name,
                        "duration_seconds": midi_duration_seconds(midi_path),
                    })
                except (ValueError, json.JSONDecodeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                except Exception as exc:
                    self._send_json(500, {"error": f"Playback failed: {exc}"})
                return

            if path != "/play":
                self._send_text(404, "Not found")
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_text(400, "Invalid Content-Length")
                return
            if not 1 <= length <= MAX_MIDI_BYTES:
                self._send_text(400, "Invalid MIDI data size")
                return

            midi_bytes = self.rfile.read(length)
            if len(midi_bytes) != length:
                self._send_text(400, "Incomplete MIDI data")
                return
            if not midi_bytes.startswith(b"MThd"):
                self._send_text(400, "Not a Standard MIDI File")
                return

            try:
                player.play(midi_bytes)
            except Exception as exc:
                self._send_text(500, f"Playback failed: {exc}")
                return
            self._send_text(200, "Playing with FluidSynth")

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"[PatternLab] {self.address_string()} - {fmt % args}")

    return Handler


def existing_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file not found: {path}")
    return path


def resolve_fluidsynth(explicit: Path | None, parser: argparse.ArgumentParser) -> tuple[Path, str]:
    """Resolve FluidSynth with priority: CLI override, PATH, embedded default."""
    if explicit is not None:
        return explicit, "command-line override"

    found = shutil.which("fluidsynth.exe") or shutil.which("fluidsynth")
    if found:
        path = Path(found).resolve()
        if path.is_file():
            return path, "PATH"

    fallback = DEFAULT_FLUIDSYNTH.expanduser()
    if fallback.is_file():
        return fallback.resolve(), "embedded default"

    parser.error(
        "FluidSynth was not found. Supply --fluidsynth PATH, add fluidsynth.exe "
        f"to PATH, or install it at the embedded default:\n  {DEFAULT_FLUIDSYNTH}"
    )
    raise AssertionError("unreachable")


def resolve_soundfont(explicit: Path | None, parser: argparse.ArgumentParser) -> tuple[Path, str]:
    """Resolve SoundFont with priority: CLI override, embedded default."""
    if explicit is not None:
        return explicit, "command-line override"

    fallback = DEFAULT_SOUNDFONT.expanduser()
    if fallback.is_file():
        return fallback.resolve(), "embedded default"

    parser.error(
        "SoundFont was not found. Supply --sf2 PATH or place it at the embedded default:\n"
        f"  {DEFAULT_SOUNDFONT}"
    )
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve PatternLab reports and play posted MIDI through FluidSynth.")
    parser.add_argument(
        "--fluidsynth",
        type=existing_file,
        default=None,
        help=(
            "override path to fluidsynth.exe; when omitted, search PATH first, "
            f"then use {DEFAULT_FLUIDSYNTH}"
        ),
    )
    parser.add_argument(
        "--sf2",
        type=existing_file,
        default=None,
        help=f"override SoundFont path; default: {DEFAULT_SOUNDFONT}",
    )
    parser.add_argument("--directory", type=Path, default=Path.cwd(), help="folder containing PatternLab HTML reports")
    parser.add_argument("--report", help="report filename to open automatically, e.g. ALLSTARS_PatternLab.html")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--audio-driver", default="dsound", help="FluidSynth audio driver (default: dsound)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser automatically")
    args = parser.parse_args()

    fluidsynth, fluidsynth_source = resolve_fluidsynth(args.fluidsynth, parser)
    soundfont, soundfont_source = resolve_soundfont(args.sf2, parser)

    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        parser.error(f"directory not found: {directory}")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be 1..65535")

    player = PlayerState(fluidsynth, soundfont, args.audio_driver)
    library = MidiLibrary(directory)
    library.refresh()
    handler = make_handler(player, directory, library)
    server = ThreadingHTTPServer((HOST, args.port), handler)
    base_url = f"http://{HOST}:{args.port}/"
    open_url = base_url
    if args.report:
        # Accept Windows-style forms such as:
        #   ALLSTARS_PatternLab.html
        #   .\ALLSTARS_PatternLab.html
        #   reports\ALLSTARS_PatternLab.html
        #
        # Resolve the filesystem path first, then derive a clean URL path
        # relative to the served directory. This prevents ".\" from being
        # copied literally into the browser URL.
        raw_report = str(args.report).strip()
        normalized_report = raw_report.replace("\\", "/")
        while normalized_report.startswith("./"):
            normalized_report = normalized_report[2:]
        normalized_report = normalized_report.lstrip("/")

        report_path = (directory / Path(normalized_report)).resolve()
        try:
            relative_report = report_path.relative_to(directory)
        except ValueError:
            parser.error(f"--report must be inside --directory: {report_path}")

        if not report_path.is_file():
            parser.error(f"report not found in --directory: {report_path}")

        from urllib.parse import quote
        report_url_path = quote(relative_report.as_posix(), safe="/")
        open_url = base_url + report_url_path

    print(f"PatternLab FluidSynth service ({VERSION_TEXT})")
    print(f"  URL        : {base_url}")
    print(f"  Directory  : {directory}")
    print(f"  FluidSynth : {fluidsynth} ({fluidsynth_source})")
    print(f"  SoundFont  : {soundfont} ({soundfont_source})")
    print("  Stop server: Ctrl+C")

    if not args.no_browser:
        webbrowser.open(open_url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        player.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
