#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local PatternLab playback service using FluidSynth and an SF2 SoundFont.

Version: 260804a
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_NAME = "play_server.py"
VERSION = "260804a"
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


def make_handler(player: PlayerState, directory: Path):
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

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/stop":
                player.stop()
                self._send_text(200, "Stopped")
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
    handler = make_handler(player, directory)
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
