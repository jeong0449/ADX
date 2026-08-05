#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''ADX Web Editor 260804e.

List/load/play/edit/save ADT/ADP files in one approved directory.
MID/MIDI files are listed and played without display or editing.

Requires beside this script (or in --directory):
  adx-player-win.py
  slot_map_definitions.json
  accent_levels.json
'''
from __future__ import annotations
import argparse, hashlib, importlib.util, io, json, re, secrets, shutil, subprocess, tempfile, threading, webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlparse

VERSION="260804e"; HOST="127.0.0.1"; DEFAULT_PORT=8130
DEFAULT_FS=Path(r"C:\Tools\FluidSynth\bin\fluidsynth.exe")
DEFAULT_SF=Path(r"C:\SoundFonts\GeneralUser-GS.sf2")
SUPPORTED={".adt",".adp",".mid",".midi"}; SYMBOLS=".-xo"; MAX_BODY=4*1024*1024

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ADX Web Editor</title><style>
:root{--bg:#f3f5f7;--p:#fff;--ink:#17202a;--muted:#68737f;--line:#d7dde3;--a:#6d28d9;--w:#fecaca;--m:#ef4444;--s:#991b1b}
@media(prefers-color-scheme:dark){:root{--bg:#11151a;--p:#1a2027;--ink:#e6edf3;--muted:#9da9b5;--line:#303843;--a:#a78bfa;--w:#5f2525;--m:#c24141;--s:#ff8a8a}}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;font-family:system-ui;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;z-index:9;display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 16px;background:var(--p);border-bottom:1px solid var(--line)}
h1{font-size:22px;margin:0}.sub,.status{font-size:13px;color:var(--muted)}button,input{font:inherit}button{padding:7px 10px;border:1px solid var(--line);border-radius:7px;background:var(--p);color:var(--ink);font-weight:700;cursor:pointer}button.primary{background:var(--a);border-color:var(--a);color:#fff}button:disabled{opacity:.45}
.actions{display:flex;align-items:center;gap:7px;flex-wrap:wrap}main{display:grid;grid-template-columns:285px minmax(0,1fr);min-height:calc(100vh - 62px)}
aside{padding:12px;background:var(--p);border-right:1px solid var(--line);overflow:auto}.file-list{margin-top:9px;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.file{display:grid;grid-template-columns:1fr auto;gap:7px;padding:9px 10px;border-bottom:1px solid var(--line);cursor:pointer}.file:last-child{border:0}.file:hover,.file.sel{background:var(--bg)}
.name{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kind{font-size:12px;color:var(--muted)}
.work{padding:16px;min-width:0;overflow:auto}.empty{min-height:50vh;display:grid;place-items:center;color:var(--muted);text-align:center}.meta{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin-bottom:12px}.meta label{display:grid;gap:4px;font-size:13px;color:var(--muted)}.meta input{width:110px;padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--p);color:var(--ink)}
.badge{padding:6px 9px;border:1px solid var(--line);border-radius:999px;font-size:13px;color:var(--muted)}
.wrap{overflow:auto;border:1px solid var(--line);border-radius:10px;background:var(--p);padding:10px}.grid{display:grid;gap:1px;width:max-content;min-width:100%}.head,.label,.cell{min-height:29px}.head,.label{display:flex;align-items:center;background:var(--p)}.head{justify-content:center;font-size:12px;color:var(--muted);border-bottom:1px solid var(--line)}.beat{border-left:2px solid #9ca3af!important}.label{position:sticky;left:0;z-index:3;width:220px;padding:0 10px;border-right:1px solid var(--line);font-family:Consolas,monospace;font-size:14px;font-weight:600}.cell{width:34px;border:0;border-radius:2px;background:var(--bg);padding:0}.a1{background:var(--w)}.a2{background:var(--m)}.a3{background:var(--s)}.cell span{font-weight:900;color:var(--ink)}
.mid{padding:24px;border:1px solid var(--line);border-radius:10px;background:var(--p)}
.progress-box{margin:16px 0 14px}.progress-track{height:10px;border-radius:999px;background:var(--bg);border:1px solid var(--line);overflow:hidden}
.progress-fill{height:100%;width:0;background:var(--a);transition:width .08s linear}
.progress-time{display:flex;justify-content:space-between;margin-top:6px;font-size:13px;color:var(--muted)}.error{color:#dc2626!important;font-weight:700}@media(max-width:800px){main{grid-template-columns:1fr}aside{max-height:220px;border-right:0;border-bottom:1px solid var(--line)}}
</style></head><body>
<header><div><h1>ADX Web Editor</h1><div class="sub">ADT/ADP edit & play · MIDI play only · 260804e</div></div>
<div class="actions"><label>BPM <input id="bpm" type="number" min="20" max="400" value="120" style="width:72px"></label><button id="play" class="primary" disabled>▶ Play</button><button id="stop">■ Stop</button><button id="save" disabled>Save ADT</button><span id="status" class="status">Starting…</span></div></header>
<main><aside><button id="refresh">Refresh</button><div id="files" class="file-list"><div class="file">Loading…</div></div></aside>
<section class="work"><div id="empty" class="empty">Select an ADT, ADP, or MIDI file.<br>MIDI is played without pattern display.</div>
<div id="editor" hidden><div class="meta"><label>NAME<input id="pname"></label><label>TIME_SIG<input id="ts"></label><label>SUBDIV<input id="sub" readonly></label><label>LENGTH<input id="len" readonly></label><span id="fmt" class="badge"></span><span id="map" class="badge"></span></div><div class="wrap"><div id="grid" class="grid"></div></div><div class="sub" style="margin-top:9px">Click: Rest → Weak → Medium → Strong → Rest</div></div>
<div id="midi" class="mid" hidden><h2 id="midiname"></h2><p class="sub">Standard MIDI file. Display and editing are disabled.</p>
<div class="progress-box"><div class="progress-track"><div id="progressfill" class="progress-fill"></div></div>
<div class="progress-time"><span id="elapsed">0:00</span><span id="duration">0:00</span></div></div>
<button id="midiplay" class="primary">▶ Play MIDI</button></div>
</section></main><script>
(()=>{const $=x=>document.getElementById(x),sym=['.','-','x','o'];let cur=null,model=null,dirty=false,progressTimer=null,progressStart=0,progressDuration=0;
function fmtTime(v){v=Math.max(0,Math.round(Number(v)||0));return Math.floor(v/60)+':'+String(v%60).padStart(2,'0')}
function resetProgress(){if(progressTimer){clearInterval(progressTimer);progressTimer=null}$('progressfill').style.width='0%';$('elapsed').textContent='0:00';$('duration').textContent=fmtTime(progressDuration)}
function startProgress(seconds){progressDuration=Math.max(0,Number(seconds)||0);resetProgress();$('duration').textContent=fmtTime(progressDuration);if(!progressDuration)return;progressStart=performance.now();progressTimer=setInterval(()=>{const elapsed=(performance.now()-progressStart)/1000,ratio=Math.min(1,elapsed/progressDuration);$('progressfill').style.width=(ratio*100).toFixed(2)+'%';$('elapsed').textContent=fmtTime(Math.min(elapsed,progressDuration));if(ratio>=1){clearInterval(progressTimer);progressTimer=null}},80)}
function st(t,e=false){$('status').textContent=t;$('status').classList.toggle('error',e)}
async function req(u,o){const r=await fetch(u,o),ct=r.headers.get('content-type')||'',d=ct.includes('json')?await r.json():await r.text();if(!r.ok)throw Error(typeof d==='string'?d:(d.error||r.status));return d}
async function files(){try{const d=await req('/api/files',{cache:'no-store'}),b=$('files');b.innerHTML='';for(const f of d.files){const r=document.createElement('div');r.className='file';r.dataset.id=f.id;r.innerHTML='<span class="name"></span><span class="kind"></span>';r.children[0].textContent=f.name;r.children[1].textContent=f.kind;r.onclick=()=>load(f.id);b.appendChild(r)}if(!d.files.length)b.innerHTML='<div class="file">No files</div>';st('Ready')}catch(e){st(e.message,true)}}
function select(id){document.querySelectorAll('.file').forEach(x=>x.classList.toggle('sel',x.dataset.id===id))}
async function load(id){if(dirty&&!confirm('Discard unsaved changes?'))return;try{const d=await req('/api/load?id='+encodeURIComponent(id),{cache:'no-store'});cur=d.file;model=d.pattern||null;dirty=false;select(id);$('empty').hidden=true;if(d.kind==='midi'){$('editor').hidden=true;$('midi').hidden=false;$('midiname').textContent=cur.name;progressDuration=Number(d.duration_seconds)||0;resetProgress()}else{$('midi').hidden=true;$('editor').hidden=false;render()}document.querySelector('.work').scrollTop=0;$('play').disabled=false;$('save').disabled=d.kind==='midi';st('Loaded '+cur.name)}catch(e){st(e.message,true)}}
function cpb(s){return s==='16'?4:s==='8T'?3:6}
function render(){$('pname').value=model.name;$('ts').value=model.time_sig||'4/4';$('sub').value=model.grid_type;$('len').value=model.length;$('fmt').textContent=model.source_format;$('map').textContent='SLOT_MAP '+model.slot_map_name;
const g=$('grid'),n=cpb(model.grid_type);g.innerHTML='';g.style.gridTemplateColumns=`220px repeat(${model.length},34px)`;let c=document.createElement('div');c.className='label';c.textContent='Instrument';g.appendChild(c);
for(let i=0;i<model.length;i++){let h=document.createElement('div');h.className='head'+(i%n===0?' beat':'');h.textContent=i%n===0?Math.floor(i/n)+1:(model.grid_type==='16'?['','e','&','a'][i%n]:i%n+1);g.appendChild(h)}
for(let s=model.slots-1;s>=0;s--){let l=document.createElement('div');l.className='label';l.textContent=`${model.slot_abbr[s]}  ${model.slot_full_names[s]}  ${model.slot_notes[s]}`;g.appendChild(l);for(let i=0;i<model.length;i++){let v=model.steps[i][s],b=document.createElement('button');b.className='cell a'+v+(i%n===0?' beat':'');b.innerHTML='<span>'+sym[v]+'</span>';b.onclick=()=>{model.steps[i][s]=(v+1)%4;dirty=true;render();st('Modified')};g.appendChild(b)}}}
function payload(){model.name=$('pname').value.trim();model.time_sig=$('ts').value.trim();return{id:cur.id,pattern:model,bpm:Number($('bpm').value)||120}}
async function play(){try{const p=cur.kind==='midi'?{id:cur.id}:payload(),d=await req('/api/play',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(cur.kind==='midi')startProgress(d.duration_seconds);st('Playing '+d.name)}catch(e){st(e.message,true)}}
async function stop(){try{await req('/api/stop',{method:'POST'});resetProgress();st('Stopped')}catch(e){st(e.message,true)}}
async function save(){try{const d=await req('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload())});dirty=false;st('Saved '+d.name);await files()}catch(e){st(e.message,true)}}
$('refresh').onclick=files;$('play').onclick=play;$('midiplay').onclick=play;$('stop').onclick=stop;$('save').onclick=save;$('pname').oninput=()=>dirty=true;$('ts').oninput=()=>dirty=true;files()})();
</script></body></html>'''

def load_engine(path:Path)->ModuleType:
    spec=importlib.util.spec_from_file_location("adx_player_engine",path)
    if not spec or not spec.loader: raise RuntimeError("Cannot load engine")
    mod=importlib.util.module_from_spec(spec)
    import sys; sys.modules[spec.name]=mod; spec.loader.exec_module(mod); return mod

def find_file(explicit:Path|None,name:str,script_dir:Path,directory:Path)->Path:
    for p in ([explicit] if explicit else [])+[directory/name,script_dir/name]:
        if p and Path(p).expanduser().resolve().is_file(): return Path(p).expanduser().resolve()
    raise FileNotFoundError(f"{name} not found")

def resolve_fs(p:Path|None):
    if p:return p.resolve(),"command-line"
    f=shutil.which("fluidsynth.exe") or shutil.which("fluidsynth")
    if f:return Path(f).resolve(),"PATH"
    if DEFAULT_FS.is_file():return DEFAULT_FS.resolve(),"embedded default"
    raise FileNotFoundError("FluidSynth not found; use --fluidsynth")

def resolve_sf(p:Path|None):
    if p:return p.resolve(),"command-line"
    if DEFAULT_SF.is_file():return DEFAULT_SF.resolve(),"embedded default"
    raise FileNotFoundError("SoundFont not found; use --sf2")

class Player:
    def __init__(self,fs,sf,driver):self.fs=fs;self.sf=sf;self.driver=driver;self.proc=None;self.tmp=None;self.lock=threading.RLock()
    def stop(self):
        with self.lock:p,t=self.proc,self.tmp;self.proc=None;self.tmp=None
        if p and p.poll() is None:
            p.terminate()
            try:p.wait(2)
            except subprocess.TimeoutExpired:p.kill();p.wait(2)
        if t:
            try:t.unlink(missing_ok=True)
            except OSError:pass
    def start(self,path,tmp=False):
        self.stop();p=subprocess.Popen([str(self.fs),"-a",self.driver,"-ni",str(self.sf),str(path)])
        with self.lock:self.proc=p;self.tmp=path if tmp else None
        def done():
            p.wait()
            with self.lock:
                t=self.tmp if self.proc is p else None
                if self.proc is p:self.proc=None;self.tmp=None
            if t:
                try:t.unlink(missing_ok=True)
                except OSError:pass
        threading.Thread(target=done,daemon=True).start()
    def play_bytes(self,b):
        with tempfile.NamedTemporaryFile(prefix="adx_web_",suffix=".mid",delete=False) as f:f.write(b);p=Path(f.name)
        self.start(p,True)

class Library:
    def __init__(self,d):self.d=d.resolve();self.secret=secrets.token_bytes(32);self.map={}
    def refresh(self):
        rows=[];m={}
        for p in sorted(self.d.iterdir(),key=lambda x:x.name.casefold()):
            if p.is_symlink() or not p.is_file() or p.suffix.lower() not in SUPPORTED:continue
            q=p.resolve()
            try:q.relative_to(self.d)
            except ValueError:continue
            s=q.stat();raw=f"{q.name}\0{s.st_size}\0{s.st_mtime_ns}".encode("utf-8","surrogatepass");i="file-"+hashlib.blake2s(raw,key=self.secret,digest_size=12).hexdigest();m[i]=q
            kind="MIDI" if q.suffix.lower() in {".mid",".midi"} else q.suffix[1:].upper();rows.append({"id":i,"name":q.name,"kind":kind})
        self.map=m;return rows
    def resolve(self,i):
        p=self.map.get(i)
        if not p:self.refresh();p=self.map.get(i)
        if not p or p.is_symlink() or not p.is_file():raise ValueError("Unknown file")
        p=p.resolve();p.relative_to(self.d);return p


def parse_adt_v22(path, engine):
    """Read legacy ADT v2.2/v2.2a and normalize it to the current Pattern model."""
    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not raw_lines or not raw_lines[0].strip().lower().startswith("; adt v2.2"):
        raise ValueError("Not an ADT v2.2 file")

    metadata = {}
    slot_rows = {}
    data_lines = []
    data_started = False
    for line_no, raw in enumerate(raw_lines[1:], start=2):
        stripped = raw.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if not data_started and "=" in stripped:
            key, value = stripped.split("=", 1)
            key = key.strip().upper()
            value = value.strip()
            if key.startswith("SLOT") and key[4:].isdigit():
                slot_rows[int(key[4:])] = value
            else:
                metadata[key] = value
            continue
        data_started = True
        compact = "".join(ch for ch in stripped if not ch.isspace())
        if any(ch.lower() not in {".", "-", "x", "o", "^"} for ch in compact):
            raise ValueError(f"{path.name}:{line_no}: invalid legacy pattern data")
        data_lines.append(compact)

    try:
        length = int(metadata["LENGTH"])
        slots = int(metadata.get("SLOTS", len(slot_rows)))
    except (KeyError, ValueError) as exc:
        raise ValueError("Legacy ADT requires integer LENGTH and SLOTS") from exc
    grid = metadata.get("GRID", metadata.get("SUBDIV", "16")).upper()
    if grid not in {"16", "8T", "16T"}:
        raise ValueError(f"Unsupported legacy GRID: {grid}")
    if sorted(slot_rows) != list(range(slots)):
        raise ValueError("Legacy SLOT definitions must be contiguous from SLOT0")
    if len(data_lines) != length:
        raise ValueError(f"Legacy ADT data has {len(data_lines)} rows; LENGTH={length}")
    if any(len(row) != slots for row in data_lines):
        raise ValueError("Legacy ADT data row width does not match SLOTS")

    slot_abbr, slot_notes, slot_names = [], [], []
    for index in range(slots):
        value = slot_rows[index]
        match = re.fullmatch(r"\s*([^@,\s]+)\s*@\s*([0-9]{1,3})\s*(?:,\s*(.+?)\s*)?", value)
        if not match:
            raise ValueError(f"Invalid legacy SLOT{index}: {value!r}")
        note = int(match.group(2))
        if not 0 <= note <= 127:
            raise ValueError(f"Legacy SLOT{index} MIDI note outside 0..127")
        abbr = match.group(1).upper()
        slot_abbr.append(abbr)
        slot_notes.append(note)
        slot_names.append((match.group(3) or abbr).strip())

    def level(ch):
        c = ch.lower()
        return 0 if c == "." else 1 if c == "-" else 2 if c == "x" else 3

    return engine.Pattern(
        name=metadata.get("NAME", path.stem),
        source_format=raw_lines[0].lstrip(";").strip(),
        length=length,
        slots=slots,
        grid_type=grid,
        steps=[[level(ch) for ch in row] for row in data_lines],
        slot_notes=slot_notes,
        slot_abbr=slot_abbr,
        slot_full_names=slot_names,
        slot_map_name="INLINE",
        time_sig=metadata.get("TIME_SIG", "4/4"),
        tempo=int(metadata["TEMPO"]) if metadata.get("TEMPO", "").isdigit() else None,
        ppqn=240,
    )


def load_any_pattern(path, engine, byname, byid):
    if path.suffix.lower() == ".adt":
        first = path.read_text(encoding="utf-8-sig").splitlines()
        if first and first[0].strip().lower().startswith("; adt v2.2"):
            return parse_adt_v22(path, engine)
    return engine.load_pattern(path, byname, byid)


def midi_duration_seconds(path, engine):
    try:
        return max(0.0, float(engine.mido.MidiFile(path).length))
    except Exception:
        return None


def midi_bytes_duration(data, engine):
    try:
        return max(0.0, float(engine.mido.MidiFile(file=io.BytesIO(data)).length))
    except Exception:
        return None


def pjson(p):
    return {"name":p.name,"source_format":p.source_format,"length":int(p.length),"slots":int(p.slots),"grid_type":p.grid_type,"steps":[[int(v) for v in r] for r in p.steps],"slot_notes":[int(v) for v in p.slot_notes],"slot_abbr":list(p.slot_abbr),"slot_full_names":list(p.slot_full_names),"slot_map_name":p.slot_map_name,"time_sig":p.time_sig or "4/4","tempo":p.tempo,"ppqn":int(p.ppqn)}

def valid(d):
    if not isinstance(d,dict):raise ValueError("Missing pattern")
    length,slots=int(d["length"]),int(d["slots"]);steps=d["steps"]
    if not 1<=length<=255 or not 1<=slots<=16 or len(steps)!=length:raise ValueError("Invalid dimensions")
    norm=[]
    for r in steps:
        if len(r)!=slots:raise ValueError("Invalid row width")
        x=[int(v) for v in r]
        if any(v not in {0,1,2,3} for v in x):raise ValueError("Invalid accent")
        norm.append(x)
    name=str(d["name"]).strip()
    if not name or any(c in name for c in "\r\n\t"):raise ValueError("Invalid NAME")
    return {**d,"name":name,"length":length,"slots":slots,"steps":norm,"time_sig":str(d.get("time_sig") or "4/4"),"ppqn":int(d.get("ppqn") or 240)}

def make_pattern(e,d):
    d=valid(d)
    return e.Pattern(name=d["name"],source_format=str(d.get("source_format") or "Web Editor"),length=d["length"],slots=d["slots"],grid_type=str(d["grid_type"]),steps=d["steps"],slot_notes=[int(x) for x in d["slot_notes"]],slot_abbr=[str(x) for x in d["slot_abbr"]],slot_full_names=[str(x) for x in d["slot_full_names"]],slot_map_name=str(d["slot_map_name"]),time_sig=d["time_sig"],tempo=int(d["tempo"]) if d.get("tempo") else None,ppqn=d["ppqn"])

def midi_data(e,p,bpm,acc):
    m=e.pattern_to_midi(p,None,bpm,0.06,acc);b=io.BytesIO();m.save(file=b);return b.getvalue()

def adt(d):
    d=valid(d);sm=str(d["slot_map_name"]).upper()
    lines=["; ADT v2.3",f"; Saved by ADX Web Editor {VERSION}",f"NAME={d['name']}",f"TIME_SIG={d['time_sig']}",f"SUBDIV={d['grid_type']}",f"LENGTH={d['length']}",f"PPQN={d['ppqn']}","ORIENTATION=STEP",f"SLOT_MAP_ID={sm}"]
    if sm=="INLINE":
        for i in range(d["slots"]):lines.append(f"SLOT{i}={d['slot_abbr'][i]}@{int(d['slot_notes'][i])},{d['slot_full_names'][i]}")
    lines+=["","[DATA]"]+["".join(SYMBOLS[v] for v in r) for r in d["steps"]]
    return "\n".join(lines)+"\n"

def body(h):
    n=int(h.headers.get("Content-Length","0"))
    if not 1<=n<=MAX_BODY:raise ValueError("Invalid request size")
    return json.loads(h.rfile.read(n).decode())

def handler(engine,lib,player,byname,byid,acc):
    class H(BaseHTTPRequestHandler):
        def sendb(self,n,b,t):self.send_response(n);self.send_header("Content-Type",t);self.send_header("Content-Length",str(len(b)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(b)
        def sendj(self,n,x):self.sendb(n,json.dumps(x,ensure_ascii=False).encode(),"application/json; charset=utf-8")
        def do_GET(self):
            u=urlparse(self.path)
            try:
                if u.path=="/":return self.sendb(200,HTML.encode(),"text/html; charset=utf-8")
                if u.path=="/api/files":return self.sendj(200,{"files":lib.refresh()})
                if u.path=="/api/load":
                    i=parse_qs(u.query).get("id",[""])[0];p=lib.resolve(i)
                    if p.suffix.lower() in {".mid",".midi"}:return self.sendj(200,{"kind":"midi","file":{"id":i,"name":p.name,"kind":"midi"},"duration_seconds":midi_duration_seconds(p,engine)})
                    q=load_any_pattern(p,engine,byname,byid);return self.sendj(200,{"kind":"pattern","file":{"id":i,"name":p.name,"kind":p.suffix[1:].lower()},"pattern":pjson(q)})
                raise ValueError("Not found")
            except Exception as e:self.sendj(400,{"error":str(e)})
        def do_POST(self):
            try:
                if self.path=="/api/stop":player.stop();return self.sendj(200,{"status":"stopped"})
                x=body(self);p=lib.resolve(x.get("id"))
                if self.path=="/api/play":
                    if p.suffix.lower() in {".mid",".midi"}:
                        duration=midi_duration_seconds(p,engine);player.start(p)
                    else:
                        q=make_pattern(engine,x.get("pattern"));b=float(x.get("bpm") or q.tempo or 120)
                        if not 20<=b<=400:raise ValueError("BPM must be 20..400")
                        rendered=midi_data(engine,q,b,acc);duration=midi_bytes_duration(rendered,engine);player.play_bytes(rendered)
                    return self.sendj(200,{"status":"playing","name":p.name,"duration_seconds":duration})
                if self.path=="/api/save":
                    if p.suffix.lower() in {".mid",".midi"}:raise ValueError("MIDI cannot be edited")
                    d=valid(x.get("pattern"))
                    if p.suffix.lower()==".adt":
                        first=p.read_text(encoding="utf-8-sig").splitlines()
                        if first and first[0].strip().lower().startswith("; adt v2.2"):
                            target=p.with_name(p.stem+"_v23.ADT")
                        else:
                            target=p;stamp=datetime.now().strftime("%Y%m%d_%H%M%S");shutil.copy2(p,p.with_name(f"{p.stem}.backup_{stamp}{p.suffix}"))
                    else:target=p.with_name(p.stem+"_edited.ADT")
                    target.write_text(adt(d),encoding="utf-8");return self.sendj(200,{"status":"saved","name":target.name})
                raise ValueError("Not found")
            except Exception as e:self.sendj(400,{"error":str(e)})
        def log_message(self,f,*a):print("[ADX Web Editor] "+f%a)
    return H

def exfile(v):
    p=Path(v).expanduser().resolve()
    if not p.is_file():raise argparse.ArgumentTypeError(str(p))
    return p

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--directory",type=Path,default=Path.cwd());ap.add_argument("--engine",type=exfile);ap.add_argument("--slot-maps",type=exfile);ap.add_argument("--accent-levels",type=exfile);ap.add_argument("--fluidsynth",type=exfile);ap.add_argument("--sf2",type=exfile);ap.add_argument("--audio-driver",default="dsound");ap.add_argument("--port",type=int,default=DEFAULT_PORT);ap.add_argument("--no-browser",action="store_true");a=ap.parse_args()
    d=a.directory.expanduser().resolve();sd=Path(__file__).resolve().parent
    try:
        ep=find_file(a.engine,"adx-player-win.py",sd,d);sp=find_file(a.slot_maps,"slot_map_definitions.json",sd,d);xp=find_file(a.accent_levels,"accent_levels.json",sd,d);fs,fss=resolve_fs(a.fluidsynth);sf,sfs=resolve_sf(a.sf2);e=load_engine(ep);bn,bi=e.load_slot_maps(sp);acc=e.load_accent_velocities(xp)
    except Exception as z:ap.error(str(z))
    pl=Player(fs,sf,a.audio_driver);lib=Library(d);lib.refresh();sv=ThreadingHTTPServer((HOST,a.port),handler(e,lib,pl,bn,bi,acc));url=f"http://{HOST}:{a.port}/"
    print(f"ADX Web Editor {VERSION}\n  URL        : {url}\n  Directory  : {d}\n  Engine     : {ep}\n  FluidSynth : {fs} ({fss})\n  SoundFont  : {sf} ({sfs})\n  Stop server: Ctrl+C")
    if not a.no_browser:webbrowser.open(url)
    try:sv.serve_forever()
    except KeyboardInterrupt:print("\nStopping...")
    finally:pl.stop();sv.server_close()
    return 0
if __name__=="__main__":raise SystemExit(main())
