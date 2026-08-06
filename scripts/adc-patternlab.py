#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adc-patternlab.py 260806d

One MIDI -> self-contained interactive HTML/SVG whole-file drum matrix.
Click the SVG to toggle RAW GM notes and two-bar SLOT_MAP display.
Slot maps are loaded from canonical JSON; rhythm analysis uses adc_rhythm_analysis.
"""
from __future__ import annotations
import argparse, html, json, math, re, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple
from mido import Message, MetaMessage, MidiFile

from adc_rhythm_analysis import (
    SUPPORTED_RESOLUTIONS, analyze_event_rhythm, detect_flams,
)

SCRIPT_NAME="adc-patternlab.py"; VERSION="260806d"; VERSION_TEXT=f"{SCRIPT_NAME} {VERSION}"
GHOST_CANDIDATE_MAX_VELOCITY=30
if tuple(SUPPORTED_RESOLUTIONS) != ("16", "32", "8T", "16T"):
    raise RuntimeError(
        "Straight-32 capable adc_rhythm_analysis.py is required in the same directory "
        "(supported resolutions must be 16, 32, 8T, 16T)."
    )
GM={35:"Acoustic Bass Drum",36:"Bass Drum 1",37:"Side Stick",38:"Acoustic Snare",39:"Hand Clap",40:"Electric Snare",41:"Low Floor Tom",42:"Closed Hi-Hat",43:"High Floor Tom",44:"Pedal Hi-Hat",45:"Low Tom",46:"Open Hi-Hat",47:"Low-Mid Tom",48:"Hi-Mid Tom",49:"Crash Cymbal 1",50:"High Tom",51:"Ride Cymbal 1",52:"Chinese Cymbal",53:"Ride Bell",54:"Tambourine",55:"Splash Cymbal",56:"Cowbell",57:"Crash Cymbal 2",58:"Vibraslap",59:"Ride Cymbal 2",60:"Hi Bongo",61:"Low Bongo",62:"Mute Hi Conga",63:"Open Hi Conga",64:"Low Conga",65:"High Timbale",66:"Low Timbale",67:"High Agogo",68:"Low Agogo",69:"Cabasa",70:"Maracas",71:"Short Whistle",72:"Long Whistle",73:"Short Guiro",74:"Long Guiro",75:"Claves",76:"Hi Wood Block",77:"Low Wood Block",78:"Mute Cuica",79:"Open Cuica",80:"Mute Triangle",81:"Open Triangle"}
GENRES=(
    ("RCK","Rock"),("BNV","Bossa Nova"),("FNK","Funk"),("JZZ","Jazz"),
    ("BLU","Blues"),("POP","Pop"),("BAL","Ballad"),("LAT","Latin / Cha-cha-cha"),
    ("AFC","Afro-Cuban"),("SMB","Samba"),("WLZ","Waltz"),("SWG","Swing"),
    ("SHF","Shuffle"),("REG","Reggae"),("MTL","Metal"),("HHP","Hip-Hop"),("RAP","Rap"),
    ("RNB","R&B (Rhythm & Blues)"),("EDM","EDM / Dance"),("HSE","House"),
    ("TNO","Techno"),("DRM","Drums (default / fallback)"),
)

GENRE_MAP = [
    (re.compile(r'rock', re.I), 'RCK'),
    (re.compile(r'bossa|bossanova|bosa', re.I), 'BNV'),
    (re.compile(r'funk', re.I), 'FNK'),
    (re.compile(r'jazz', re.I), 'JZZ'),
    (re.compile(r'blues?', re.I), 'BLU'),
    (re.compile(r'pop', re.I), 'POP'),
    (re.compile(r'ballad|bal', re.I), 'BAL'),
    (re.compile(r'latin', re.I), 'LAT'),
    (re.compile(r'afrocub|afrocuba[n]?|afro[\s\-_]*cuba[n]?', re.I), 'AFC'),
    (re.compile(r'chacha|cha[\s\-_]*cha', re.I), 'LAT'),
    (re.compile(r'samba', re.I), 'SMB'),
    (re.compile(r'waltz|wlz', re.I), 'WLZ'),
    (re.compile(r'swing|swg', re.I), 'SWG'),
    (re.compile(r'shuffle|shf', re.I), 'SHF'),
    (re.compile(r'reggae', re.I), 'REG'),
    (re.compile(r'metal', re.I), 'MTL'),
    (re.compile(r'hip\s*-?\s*hop|hiphop|hhp', re.I), 'HHP'),
    (re.compile(r'(?<![a-z])rap', re.I), 'RAP'),
    (re.compile(r'r\s*&\s*b|randb|rnb', re.I), 'RNB'),
    (re.compile(r'edm|dance|dnc', re.I), 'EDM'),
    (re.compile(r'house|hse', re.I), 'HSE'),
    (re.compile(r'techno|tno', re.I), 'TNO'),
]

def infer_genre(filename: str) -> str:
    """Infer genre from filename using the same rules as the 2-bar save script."""
    stem=Path(filename).stem
    for rx,code in GENRE_MAP:
        if rx.search(stem):
            return code
    codes={code for code,_ in GENRES}
    for token in re.findall(r"[A-Z0-9]+",stem.upper()):
        if token in codes:
            return token
    return "DRM"


@dataclass(frozen=True)
class Slot: label:str; notes:Tuple[int,...]; representative:int
@dataclass(frozen=True)
class SMap:
    id:int; name:str; slots:Tuple[Slot,...]
    @property
    def accepted(self)->Set[int]:
        s=set()
        for x in self.slots:s.update(x.notes)
        return s

def load_slot_maps(path: Path) -> Tuple[SMap, ...]:
    """Load and validate the sole authoritative slot-map JSON definition."""
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"slot-map definition not found: {path}") from exc
    except (OSError,json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load slot-map definition {path}: {exc}") from exc
    if not isinstance(data,list) or not data:
        raise ValueError("slot-map JSON root must be a non-empty array")
    maps=[]; seen_ids=set(); seen_names=set()
    for row in data:
        if not isinstance(row,dict):raise ValueError("each slot map must be an object")
        mid=row.get("slot_map_id"); name=row.get("name"); slots_data=row.get("slots")
        if not isinstance(mid,int) or mid in seen_ids:raise ValueError(f"invalid or duplicate slot_map_id: {mid!r}")
        if not isinstance(name,str) or not name or name in seen_names:raise ValueError(f"invalid or duplicate slot-map name: {name!r}")
        if not isinstance(slots_data,list) or not 1<=len(slots_data)<=12:raise ValueError(f"{name}: slots must contain 1..12 entries")
        seen_ids.add(mid); seen_names.add(name); slots=[]; seen_slots=set()
        for item in slots_data:
            slot_no=item.get("slot"); label=item.get("abbrev"); allowed=item.get("midi_input_allowed"); rep=item.get("representative_midi")
            if not isinstance(slot_no,int) or slot_no in seen_slots:raise ValueError(f"{name}: invalid or duplicate slot number {slot_no!r}")
            if not isinstance(label,str) or not label:raise ValueError(f"{name} slot {slot_no}: missing abbrev")
            if not isinstance(allowed,list) or not allowed or any(not isinstance(n,int) for n in allowed):raise ValueError(f"{name} slot {slot_no}: invalid midi_input_allowed")
            if rep not in allowed:raise ValueError(f"{name} slot {slot_no}: representative_midi must be allowed")
            seen_slots.add(slot_no); slots.append((slot_no,Slot(label,tuple(allowed),int(rep))))
        expected=list(range(len(slots)))
        actual=sorted(seen_slots)
        if actual!=expected:raise ValueError(f"{name}: slot numbers must be contiguous 0..{len(slots)-1}")
        maps.append(SMap(mid,name,tuple(slot for _,slot in sorted(slots))))
    maps.sort(key=lambda m:m.id)
    return tuple(maps)

MAPS:Tuple[SMap,...]=()
ACCENT_LEVELS:dict={}


def load_accent_levels(path: Path) -> dict:
    """Load and validate 4-accent and 6-accent velocity quantization schemes."""
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"accent-level definition not found: {path}") from exc
    except (OSError,json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load accent-level definition {path}: {exc}") from exc
    schemes=data.get("schemes") if isinstance(data,dict) else None
    if not isinstance(schemes,dict):
        raise ValueError("accent-level JSON must contain an object named 'schemes'")
    for scheme_name, expected_count in (("4-accent",4),("6-accent",6)):
        scheme=schemes.get(scheme_name)
        levels=scheme.get("levels") if isinstance(scheme,dict) else None
        if not isinstance(levels,list) or len(levels)!=expected_count:
            raise ValueError(f"{scheme_name}: exactly {expected_count} levels are required, including rest")
        expected_min=0
        for index,level in enumerate(levels):
            if not isinstance(level,dict):
                raise ValueError(f"{scheme_name} level {index}: must be an object")
            if level.get("index")!=index:
                raise ValueError(f"{scheme_name} level {index}: index must equal its array position")
            lo=level.get("min_velocity"); hi=level.get("max_velocity"); rep=level.get("representative_velocity")
            if not all(isinstance(v,int) for v in (lo,hi,rep)):
                raise ValueError(f"{scheme_name} level {index}: velocity values must be integers")
            if lo!=expected_min or not 0<=lo<=hi<=127:
                raise ValueError(f"{scheme_name} level {index}: ranges must be contiguous and cover 0..127")
            if not lo<=rep<=hi:
                raise ValueError(f"{scheme_name} level {index}: representative_velocity must lie within its range")
            if index==0 and not (lo==hi==rep==0):
                raise ValueError(f"{scheme_name}: level 0 must be Rest with velocity 0")
            expected_min=hi+1
        if expected_min!=128:
            raise ValueError(f"{scheme_name}: ranges must end at velocity 127")
    return data


def accent_level(velocity: int, scheme_name: str):
    levels=ACCENT_LEVELS["schemes"][scheme_name]["levels"]
    value=max(0,min(127,int(velocity)))
    for level in levels:
        if level["min_velocity"] <= value <= level["max_velocity"]:
            return level
    raise ValueError(f"velocity {value} is not covered by {scheme_name}")



@dataclass
class Ev: tick:int; note:int; vel:int; dur:int=0
@dataclass
class Bar: no:int; start:int; end:int; num:int; den:int
@dataclass
class Block: no:int; bars:List[Bar]; start:int; end:int; events:List[Ev]; smap:SMap; unknown:List[int]; subdiv:dict; pattern_no:int=0; duplicate_of:Optional[int]=None; ending_hit:bool=False



def embedded_header_metadata(mid):
    """Return only tempo/time-signature metadata explicitly stored in the SMF.

    No 120 BPM or 4/4 fallback is reported here. Events duplicated across
    tracks at the same tick are collapsed for header-display purposes.
    """
    tempos=[]
    timesigs=[]
    for tr in mid.tracks:
        tick=0
        for m in tr:
            tick+=m.time
            if isinstance(m,MetaMessage) and m.type=="set_tempo":
                tempos.append((tick,int(m.tempo)))
            elif isinstance(m,MetaMessage) and m.type=="time_signature":
                timesigs.append((tick,int(m.numerator),int(m.denominator)))
    tempos=sorted(set(tempos))
    timesigs=sorted(set(timesigs))
    parts=[]
    if len(tempos)==1:
        bpm=60000000/tempos[0][1]
        bpm_text=str(int(round(bpm))) if abs(bpm-round(bpm))<0.005 else f"{bpm:.2f}".rstrip("0").rstrip(".")
        parts.append(f"{bpm_text} BPM")
    elif len(tempos)>1:
        parts.append(f"tempo changes ×{len(tempos)}")
    if len(timesigs)==1:
        _,num,den=timesigs[0]
        parts.append(f"{num}/{den}")
    elif len(timesigs)>1:
        parts.append(f"time-signature changes ×{len(timesigs)}")
    return parts

def tempo_at_tick(mid, target_tick):
    """Return the last set_tempo value active at target_tick, defaulting to 120 BPM."""
    tempos=[(0,500000)]
    for tr in mid.tracks:
        tick=0
        for msg in tr:
            tick+=msg.time
            if isinstance(msg,MetaMessage) and msg.type=="set_tempo":
                tempos.append((tick,int(msg.tempo)))
    active=500000
    for tick,tempo in sorted(tempos):
        if tick>target_tick:
            break
        active=tempo
    return active

def collect(mid):
    ev=[]; ts=[]; mx=0
    for tr in mid.tracks:
        t=0; active={}
        for m in tr:
            t+=m.time; mx=max(mx,t)
            if isinstance(m,MetaMessage) and m.type=="time_signature":
                ts.append((t,int(m.numerator),int(m.denominator)))
            elif isinstance(m,Message) and getattr(m,"channel",-1)==9:
                if m.type=="note_on" and m.velocity>0:
                    key=int(m.note); active.setdefault(key,[]).append((t,int(m.velocity)))
                elif m.type=="note_off" or (m.type=="note_on" and m.velocity==0):
                    key=int(m.note)
                    if active.get(key):
                        st,vel=active[key].pop(0); ev.append(Ev(st,key,vel,max(0,t-st)))
        for key,items in active.items():
            for st,vel in items:
                ev.append(Ev(st,key,vel,0))
    d={0:(4,4)}
    for t,n,q in ts:d[t]=(n,q)
    return sorted(ev,key=lambda x:(x.tick,x.note,x.vel,x.dur)),[(t,*v) for t,v in sorted(d.items())],max(mx,(ev[-1].tick+1 if ev else 1))

def make_bars(tpq,ts,mx):
    out=[]; t=0; i=0; no=1
    while t<mx:
        while i+1<len(ts) and ts[i+1][0]<=t:i+=1
        _,n,d=ts[i]; end=t+max(1,round(tpq*n*4/d))
        if i+1<len(ts) and t<ts[i+1][0]<end:end=ts[i+1][0]
        out.append(Bar(no,t,end,n,d)); t=end; no+=1
    return out

def choose(notes):
    """Choose the lowest-ID exact SLOT_MAP, or the nearest map with warning.

    If no map is a complete cover, every map participates in the comparison.
    The map covering the most distinct notes wins; ties prefer fewer unused
    accepted notes and finally the stable lower ID, so LEGACY (ID 0) remains
    the conservative default.
    """
    if not notes:
        return MAPS[0], []

    exact=[m for m in MAPS if notes <= m.accepted]
    if exact:
        m=min(exact,key=lambda z:z.id)
        return m, []

    def score(m):
        covered=len(notes & m.accepted)
        missing=len(notes - m.accepted)
        unused=len(m.accepted - notes)
        return (covered,-missing,-m.id,-unused)

    m=max(MAPS,key=score)
    return m,sorted(notes-m.accepted)

def _is_ending_hit_block(block_bars, events):
    if len(block_bars)!=1 or not events:
        return False
    first_tick=min(e.tick for e in events)
    onset_group=[e for e in events if e.tick==first_tick]
    tol=max(1,(block_bars[0].end-block_bars[0].start)//96)
    near_start=(first_tick-block_bars[0].start)<=tol
    return near_start and len(onset_group)==len(events)

def _pattern_signature(block):
    """Return a timing-only identity signature for one pattern block.

    Pattern identity depends only on each raw MIDI note's relative onset tick
    and note number. Velocity and note duration are deliberately ignored.
    """
    return tuple(sorted((e.tick-block.start,e.note) for e in block.events))

def skip_leading_empty_bars(bars, events):
    """Drop only leading bars without CH10 note-on events; preserve Bar.no."""
    first_nonempty=None
    for index,bar in enumerate(bars):
        if any(bar.start <= event.tick < bar.end for event in events):
            first_nonempty=index
            break
    if first_nonempty is None:
        return bars,0
    return bars[first_nonempty:],first_nonempty


def blocks(bars,ev,tpq,filename):
    out=[]
    for i in range(0,len(bars),2):
        bb=bars[i:i+2]; s,e=bb[0].start,bb[-1].end; ee=[x for x in ev if s<=x.tick<e]; m,u=choose({x.note for x in ee})
        rhythm=analyze_event_rhythm(ee,tpq,filename,loop_ticks=e-s,loop_start=s)
        sub=rhythm["subdivision"]; sub["tpq"]=tpq
        out.append(Block(len(out)+1,bb,s,e,ee,m,u,sub))
    if out and _is_ending_hit_block(out[-1].bars,out[-1].events):
        out[-1].ending_hit=True
    seen={}; next_pattern=1
    for b in out:
        if b.ending_hit:
            continue
        sig=_pattern_signature(b)
        if sig in seen:
            first=seen[sig]; b.pattern_no=first.pattern_no; b.duplicate_of=first.no
        else:
            b.pattern_no=next_pattern; seen[sig]=b; next_pattern+=1
    return out

def tx(x,y,s,cls="",anchor="start"):return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}">{html.escape(s)}</text>'
def slot_index(m,n):
    for i,s in enumerate(m.slots):
        if n in s.notes:return i
    return None

def velocity_level(velocity):
    """Map raw MIDI velocity to four display bands without changing note presence."""
    if velocity <= 31:return 0
    if velocity <= 63:return 1
    if velocity <= 95:return 2
    return 3


def adx_hit_level(velocity):
    """Map a present note through the authoritative 6-accent JSON scheme."""
    level=accent_level(velocity,"6-accent")
    if level["index"]==0:
        raise ValueError("a present MIDI note cannot map to Rest")
    return level["index"]-1,level["label"],level["symbol"]

def reference_card(b,x,y,w=430,h=470,path=None):
    bars=str(b.bars[0].no) if len(b.bars)==1 else f'{b.bars[0].no}–{b.bars[-1].no}'
    p=[f'<g class="block duplicate {"bad" if b.unknown else ""}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="bg"/>']
    p += [tx(x+16,y+28,f'B{b.no:03d}  bars {bars}',"title"),tx(x+w/2,y+105,f'Pattern #{b.pattern_no:03d}',"dup-pattern","middle"),tx(x+w/2,y+139,f'Same as B{b.duplicate_of:03d}',"dup-same","middle"),tx(x+w/2,y+169,f'ID {b.smap.id} {b.smap.name} · matrix omitted',"meta","middle"),tx(x+w/2,y+192,('MISSING NOTES: '+','.join(map(str,b.unknown))) if b.unknown else '',"warning","middle"),tx(x+16,y+248,'duplicate checked within this MIDI file only',"meta"),card_controls(path,b,x,y+264,w),'</g>']
    return ''.join(p)

def ending_card(b,x,y,w=430,h=470,path=None):
    notes=', '.join(f'{e.note}({e.vel})' for e in b.events) or '(none)'
    bar=str(b.bars[0].no)
    p=[f'<g class="block ending"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="bg"/>']
    p += [tx(x+16,y+28,f'B{b.no:03d}  bar {bar}',"title"),tx(x+w/2,y+100,'ENDING HIT',"ending-title","middle"),tx(x+w/2,y+134,'excluded from pattern catalog',"dup-same","middle"),tx(x+w/2,y+166,f'notes: {notes}',"meta","middle"),tx(x+16,y+248,'single onset group at the start of the final odd bar',"meta"),card_controls(path,b,x,y+264,w,disabled=True),'</g>']
    return ''.join(p)


def raw_grid_fit(block, subdiv: str) -> dict:
    """Measure RAW onset fit using the same thresholds as the HTML color logic.

    `aligned_percent` is the percentage of raw note onsets whose distance from
    the nearest grid line is at most 5% of one subdivision step.  Mean error is
    retained in ticks for the tooltip and tie-breaking.
    """
    cells_per_beat={"16":4,"32":8,"8T":3,"16T":6}[subdiv]
    duration=max(1,block.end-block.start)
    tpq=max(1,int(block.subdiv.get("tpq",1)))
    beats=duration/tpq
    cols=max(1,round(beats*cells_per_beat))
    step_ticks=duration/cols
    ratios=[]
    tick_errors=[]
    for event in block.events:
        offset=event.tick-block.start
        nearest=round(offset/step_ticks)*step_ticks
        error=abs(offset-nearest)
        ratios.append(error/step_ticks)
        tick_errors.append(error)
    count=len(ratios)
    aligned=sum(1 for ratio in ratios if ratio<=0.05)
    return {
        "subdiv":subdiv,
        "count":count,
        "aligned":aligned,
        "aligned_percent":(100.0*aligned/count) if count else 100.0,
        "mean_error_ticks":(sum(tick_errors)/count) if count else 0.0,
        "mean_error_ratio":(sum(ratios)/count) if count else 0.0,
    }


def raw_grid_fit_summary(block) -> dict:
    stats={subdiv:raw_grid_fit(block,subdiv) for subdiv in ("16","32","8T","16T")}
    # Prefer the highest aligned percentage; break ties with the smaller mean
    # normalized error, then the less finely divided grid.
    order={"16":0,"8T":1,"32":2,"16T":3}
    best=max(
        stats,
        key=lambda key:(
            stats[key]["aligned_percent"],
            -stats[key]["mean_error_ratio"],
            -order[key],
        ),
    )
    return {"best":best,"stats":stats}

def card(b,x,y,w=430,h=470,path=None):
    beats=max(1.0,(b.end-b.start)/max(1,b.subdiv.get("tpq",1)))
    detected=b.subdiv.get("subdivision","unknown")
    initial_subdiv={
        "straight-16":"16","straight-32":"32","triplet-8":"8T","triplet-8T":"8T",
        "triplet-16":"16T","triplet-16T":"16T",
    }.get(detected,"16")
    subdivision_cells={"16":4,"32":8,"8T":3,"16T":6}
    hh,fh,lw=58,28,96; plot_h=260; gx,gy=x+lw,y+hh; gw,gh=w-lw-8,plot_h-hh-fh
    raw=sorted({e.note for e in b.events},reverse=True) or [36]
    slots=list(range(len(b.smap.slots)-1,-1,-1)); p=[]
    p.append(f'<g class="block pattern-card {"bad" if b.unknown else ""}" data-block="{b.no}" data-duration-ticks="{max(1,b.end-b.start)}">')
    p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="bg"/>')
    bars=str(b.bars[0].no) if len(b.bars)==1 else f'{b.bars[0].no}–{b.bars[-1].no}'
    meters=[f'{z.num}/{z.den}' for z in b.bars]; meter=meters[0] if len(set(meters))==1 else '→'.join(meters)
    initial_cells=subdivision_cells[initial_subdiv]
    p += [
        tx(x+10,y+18,f'B{b.no:03d}  bars {bars} · Pattern #{b.pattern_no:03d}',"title"),
        f'<text x="{x+10:.1f}" y="{y+36:.1f}" class="meta grid-summary" data-prefix="{html.escape(meter)} · {len(b.events)} hits · ">{html.escape(meter)} · {len(b.events)} hits · {initial_cells} cells/beat</text>',
        tx(x+w-10,y+18,f'ID {b.smap.id} {b.smap.name}',"sid","end"),
        tx(x+w-10,y+36,f'{ {"triplet-8T":"triplet-8","triplet-16T":"triplet-16"}.get(b.subdiv["subdivision"],b.subdiv["subdivision"]) } · {b.subdiv["confidence"]}',"meta","end")]
    if b.unknown:p.append(tx(x+w/2,y+52,'MISSING NOTES: '+','.join(map(str,b.unknown)),"warning","middle"))

    for subdiv,cells_per_beat in subdivision_cells.items():
        cols=max(1,round(beats*cells_per_beat)); active=" active" if subdiv==initial_subdiv else ""
        p.append(f'<g class="subdiv-layer grid-layer subdiv-{subdiv}{active}" data-subdiv="{subdiv}">')
        for c in range(cols+1):
            xx=gx+c*gw/cols; cl="guide major" if c%cells_per_beat==0 else "guide"
            p.append(f'<line x1="{xx:.2f}" y1="{gy}" x2="{xx:.2f}" y2="{gy+gh}" class="{cl}"/>')
        p.append('</g>')
    for bar in b.bars:
        frac=(bar.start-b.start)/max(1,b.end-b.start); xx=gx+frac*gw
        p.append(f'<line x1="{xx:.2f}" y1="{gy-4}" x2="{xx:.2f}" y2="{gy+gh}" class="barline"/>')
    p.append(f'<line x1="{gx+gw:.2f}" y1="{gy-4}" x2="{gx+gw:.2f}" y2="{gy+gh}" class="barline"/>')

    flam_analysis=detect_flams(b.events,b.subdiv.get("tpq",1),loop_ticks=b.end-b.start,loop_start=b.start,selected_resolution=b.subdiv.get("resolution"))
    excluded_grace_ids={id(b.events[int(item["grace_index"])]) for item in flam_analysis["flams"] if item.get("remove_from_subdivision") and "grace_index" in item}
    pair_role={}; pair_delta={}; pair_confidence={}; grace_remove={}; pair_grid_preserved={}
    for item in flam_analysis["flams"]:
        grace=b.events[item["grace_index"]]; main=b.events[item["main_index"]]; delta=item["gap_ticks"]
        pair_role[id(grace)]="grace"; pair_role[id(main)]="main"
        pair_delta[id(grace)]=pair_delta[id(main)]=delta
        pair_confidence[id(grace)]=pair_confidence[id(main)]=item["confidence"]
        grace_remove[id(grace)]=bool(item.get("remove_from_subdivision"))
        pair_grid_preserved[id(grace)]=pair_grid_preserved[id(main)]=bool(item.get("grid_preserved"))
    flam_threshold=flam_analysis["settings"].get("flam_max_gap_ticks",0)

    p.append('<g class="raw">'); rh=gh/len(raw); rmap={n:i for i,n in enumerate(raw)}
    for i,n in enumerate(raw):
        yy=gy+i*rh; row_class="row unknown-row" if n in b.unknown else "row"; p += [tx(x+8,yy+rh*.7,f'{n} {GM.get(n,"non-GM")}',row_class),f'<line x1="{gx}" y1="{yy+rh:.2f}" x2="{gx+gw}" y2="{yy+rh:.2f}" class="rguide"/>']
    grace_offset=min(10.0,max(5.0,rh*.22)); duration=max(1,b.end-b.start)
    for e in b.events:
        frac=(e.tick-b.start)/duration; cx=gx+max(0.0,min(1.0,frac))*gw
        base_cy=gy+(rmap[e.note]+.5)*rh; rr=2+2.2*e.vel/127; role=pair_role.get(id(e)); cy=base_cy-grace_offset if role=="grace" else base_cy
        classes=["hit","rawhit","raw-event","deviation-aligned"]
        if e.note in b.unknown:classes.append("unknown")
        ghost_candidate=e.vel<=GHOST_CANDIDATE_MAX_VELOCITY
        orn_reasons=[]
        if ghost_candidate:
            classes.append("ghost")
            orn_reasons.append(f"ghost candidate: velocity {e.vel} <= {GHOST_CANDIDATE_MAX_VELOCITY}")
        if role=="grace":
            if pair_grid_preserved.get(id(e),False):
                role="grid-hit"
            else:
                classes.append("flamgrace")
                remove_text="yes" if grace_remove.get(id(e),False) else "no"
                orn_reasons.append(
                    f"flam grace: confidence {pair_confidence[id(e)]}, "
                    f"gap {pair_delta[id(e)]} ticks <= threshold {flam_threshold}; "
                    f"remove from subdivision: {remove_text}"
                )
        if role=="main" and not pair_grid_preserved.get(id(e),False):classes.append("flammain")
        if orn_reasons:classes.append("ornnote")
        labels=[]
        if ghost_candidate:labels.append("ghost candidate")
        if pair_grid_preserved.get(id(e),False):
            labels.append(f"regular straight-32 grid hit; flam-like pair preserved, delta {pair_delta[id(e)]} ticks")
        elif role:
            labels.append(f"flam candidate ({role}, {pair_confidence[id(e)]}, delta {pair_delta[id(e)]} ticks, threshold {flam_threshold})")
        if orn_reasons:labels.append("ORN reason: "+" | ".join(orn_reasons))
        extra=("; "+"; ".join(labels)) if labels else ""
        actual_duration_width=max(0.0,e.dur/duration*gw)
        duration_x2=min(gx+gw,max(cx+2.0,cx+actual_duration_width))
        duration_classes=["rawduration","raw-event","deviation-aligned"]
        if orn_reasons:duration_classes.append("ornduration")
        event_offset=e.tick-b.start
        p.append(f'<line x1="{cx:.2f}" y1="{cy:.2f}" x2="{duration_x2:.2f}" y2="{cy:.2f}" class="{" ".join(duration_classes)}" data-tick-offset="{event_offset}"><title>note {e.note}, note-on {e.tick}, note-off {e.tick+e.dur}, duration {e.dur} ticks{extra}</title></line>')
        p.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{rr:.2f}" class="{" ".join(classes)}" data-tick-offset="{event_offset}"><title>note {e.note}, velocity {e.vel}, duration {e.dur}, tick {e.tick}{extra}</title></circle>')
    p.append('</g>')

    p.append('<g class="slot">'); sh=gh/len(slots); smap={s:i for i,s in enumerate(slots)}
    for i,si in enumerate(slots):
        yy=gy+i*sh; s=b.smap.slots[si]
        p += [tx(x+8,yy+sh*.7,f'{si:02d} {s.label} [{",".join(map(str,s.notes))}]',"row"),f'<line x1="{gx}" y1="{yy+sh:.2f}" x2="{gx+gw}" y2="{yy+sh:.2f}" class="rguide"/>']
    for subdiv,cells_per_beat in subdivision_cells.items():
        cols=max(1,round(beats*cells_per_beat)); active=" active" if subdiv==initial_subdiv else ""; cells={}
        for e in b.events:
            if id(e) in excluded_grace_ids:continue
            si=slot_index(b.smap,e.note)
            if si is None:continue
            c=max(0,min(cols-1,math.floor((e.tick-b.start)/duration*cols+0.5))); key=(si,c); prev=cells.get(key)
            if prev is None or e.vel>prev.vel:cells[key]=e
        cell_w=gw/cols
        p.append(f'<g class="subdiv-layer slot-cells subdiv-{subdiv}{active}" data-subdiv="{subdiv}">')
        for (si,c),e in sorted(cells.items(),key=lambda item:(smap[item[0][0]],item[0][1])):
            row=smap[si]; xx=gx+c*cell_w; yy=gy+row*sh
            vlevel=velocity_level(e.vel); hlevel,hlabel,hsymbol=adx_hit_level(e.vel)
            p.append(f'<rect x="{xx+.6:.2f}" y="{yy+.6:.2f}" width="{max(.5,cell_w-1.2):.2f}" height="{max(.5,sh-1.2):.2f}" rx="1.2" class="slotcell velocity{vlevel} hitstrength{hlevel}"><title>slot {si} {b.smap.slots[si].label}; raw {e.note}; velocity {e.vel} (band {vlevel}); ADX 6-accent {hsymbol} = {hlabel}; duration {e.dur}; resolution {subdiv}</title></rect>')
        p.append('</g>')
    p.append('</g>')
    foot='click SVG: RAW ↔ QUANTIZED' if not b.unknown else 'WARNING · nearest SLOT_MAP used · missing notes: '+','.join(map(str,b.unknown))
    p += [tx(x+10,y+251,foot,"meta"),card_controls(path,b,x,y+264,w),'</g>']; return ''.join(p)

def select_options(items, selected):
    return ''.join(
        f'<option value="{html.escape(value)}" {"selected" if value == selected else ""}>{html.escape(label)}</option>'
        for value, label in items
    )

SUBDIVISIONS = [
    ("16", "16"),
    ("32", "32"),
    ("8T", "8T"),
    ("16T", "16T"),
]

def card_controls(path, b, x, y, w=430, disabled=False):
    default_genre=infer_genre(path.name)
    fit=raw_grid_fit_summary(b)
    fit_stats=fit["stats"]
    fit_items=[]
    for key in ("16","32","8T","16T"):
        item=fit_stats[key]
        fit_items.append(
            f'<span class="fit-item" data-subdiv="{key}" '
            f'title="{key}: {item["aligned"]}/{item["count"]} aligned; '
            f'mean error {item["mean_error_ticks"]:.2f} ticks">'
            f'{key} {item["aligned_percent"]:.0f}%</span>'
        )
    fit_html=' · '.join(fit_items)
    fit_title=(
        "RAW note-on fit to the selected reference grid. "
        "Aligned means distance <= 5% of one grid step."
    )
    genre_options=select_options([(code, f"{code} - {name}") for code,name in GENRES], default_genre)
    detected=b.subdiv.get("subdivision", "unknown")
    display_detected={
        "straight-16":"16",
        "straight-32":"32",
        "triplet-8":"8T",
        "triplet-8T":"8T",
        "triplet-16":"16T",
        "triplet-16T":"16T",
    }.get(detected, "16")
    subdivision_options=select_options(SUBDIVISIONS, display_detected)
    export_checked=(not disabled and b.duplicate_of is None)
    orn_candidate=(
        any(e.vel<=GHOST_CANDIDATE_MAX_VELOCITY for e in b.events)
        or any(
            item.get("remove_from_subdivision")
            for item in detect_flams(
                b.events,b.subdiv.get("tpq",1),
                loop_ticks=b.end-b.start,loop_start=b.start,
                selected_resolution=b.subdiv.get("resolution"),
            )["flams"]
        )
    )
    dis=' disabled' if disabled else ''
    checked_export=' checked' if export_checked else ''
    checked_orn=' checked' if orn_candidate and not disabled else ''
    dup=b.duplicate_of or ""
    return f'''<foreignObject x="{x+10}" y="{y}" width="{w-20}" height="196" class="pattern-controls-wrap">
<div xmlns="http://www.w3.org/1999/xhtml" class="pattern-controls" data-block="{b.no}" data-pattern-no="{b.pattern_no}" data-start-bar="{b.bars[0].no}" data-end-bar="{b.bars[-1].no}" data-time-sig="{html.escape("→".join(f"{bar.num}/{bar.den}" for bar in b.bars) if len({(bar.num,bar.den) for bar in b.bars}) > 1 else f"{b.bars[0].num}/{b.bars[0].den}")}" data-slot-map="{html.escape(b.smap.name)}" data-duplicate-of="{dup}">
<div class="catalog-row">
<label><input class="export-check" type="checkbox"{checked_export}{dis}/> Export</label>
<label>Genre <select class="genre-select"{dis}>{genre_options}</select></label>
<label><input class="orn-check" type="checkbox"{checked_orn}{dis}/> ORN</label>
</div>
<label class="number-label">No. <input class="start-number" type="text" inputmode="numeric" maxlength="4" placeholder="start" aria-label="Starting pattern number"{dis}/><output class="name-preview" aria-live="polite"></output></label>
<div class="timing-fit" title="{html.escape(fit_title)}"><strong>Grid fit</strong> {fit_html}<span class="fit-best">Best {fit["best"]}</span></div>
<div class="playback-box">
<button class="play-compare" type="button"{dis}>▶ Play</button>
<div class="playback-settings">
<label>Resolution <select class="subdivision-select" title="analysis confidence {html.escape(str(b.subdiv.get("confidence", "")))}"{dis}>{subdivision_options}</select></label>
<label>Compare Mode <select class="compare-mode-select"{dis}><option value="raw">RAW only</option><option value="6">RAW → 6</option><option value="4">RAW → 4</option><option value="both" selected>RAW → 6 → 4</option></select></label>
</div>
<div class="play-stage" aria-live="polite">
<span class="stage-pill stage-raw" data-stage="raw">RAW</span>
<span class="stage-pill stage-6" data-stage="6">6</span>
<span class="stage-pill stage-4" data-stage="4">4</span>
</div>
<button class="compare-midi" type="button"{dis}>Download MIDI</button>
<div class="play-progress" aria-hidden="true"><span></span></div>
</div>
</div></foreignObject>'''

def render(path,mid,bars_,bb,skipped_leading_bars=0):
    cw,ch,gx,gy,mar,ncol=430,470,18,18,18,3; nrow=max(1,math.ceil(len(bb)/ncol)); sw=mar*2+ncol*cw+(ncol-1)*gx; sh=mar*2+nrow*ch+(nrow-1)*gy
    body=[]
    for i,b in enumerate(bb):
        x=mar+(i%ncol)*(cw+gx); y=mar+(i//ncol)*(ch+gy)
        body.append(ending_card(b,x,y,path=path) if b.ending_hit else reference_card(b,x,y,path=path) if b.duplicate_of is not None else card(b,x,y,path=path))
    notes=sorted({e.note for b in bb for e in b.events}); summary={}
    for b in bb:
        if not b.ending_hit and b.duplicate_of is None:summary[f'{b.smap.id} {b.smap.name}']=summary.get(f'{b.smap.id} {b.smap.name}',0)+1
    unique_count=sum(1 for b in bb if not b.ending_hit and b.duplicate_of is None); duplicate_count=sum(1 for b in bb if b.duplicate_of is not None); ending_count=sum(1 for b in bb if b.ending_hit)
    header_parts=[f"SMF Type {mid.type}",f"TPQ {mid.ticks_per_beat}"]
    if skipped_leading_bars:
        header_parts.append(f"leading empty bars skipped: {skipped_leading_bars}")
    header_parts.extend(embedded_header_metadata(mid))
    header_parts.extend([f"{len(bars_)} bar(s)",f"{len(bb)} two-bar block(s)",f"unique patterns {unique_count}",f"duplicates {duplicate_count}",f"ending hits {ending_count}",f"CH10 notes: {', '.join(map(str,notes)) or '(none)'}"])
    header_summary=html.escape(" · ".join(header_parts))
    block_data={}
    for b in bb:
        if b.ending_hit or b.duplicate_of is not None:
            continue
        flam_analysis=detect_flams(b.events,b.subdiv.get("tpq",1),loop_ticks=b.end-b.start,loop_start=b.start,selected_resolution=b.subdiv.get("resolution"))
        excluded=set()
        for item in flam_analysis.get("flams",[]):
            if item.get("remove_from_subdivision") and "grace_index" in item:
                excluded.add(int(item["grace_index"]))
        block_data[str(b.no)]={
            "duration":max(1,b.end-b.start),
            "tempo":tempo_at_tick(mid,b.start),
            "meter":[b.bars[0].num,b.bars[0].den],
            "events":[{"tick":e.tick-b.start,"note":e.note,"vel":e.vel,"dur":e.dur,"excluded":i in excluded} for i,e in enumerate(b.events)],
            "slots":[{"label":slot.label,"notes":list(slot.notes),"representative":slot.representative} for slot in b.smap.slots],
        }
    block_data_json=json.dumps(block_data,separators=(",",":"))
    accent_levels_json=json.dumps(ACCENT_LEVELS["schemes"],separators=(",",":"),ensure_ascii=False)
    accent_legend=" ".join(
        f'<i class="lg h{level["index"]-1}"></i><code>{html.escape(level["symbol"])}</code> '
        f'{html.escape(level["label"])} ({level["min_velocity"]}–{level["max_velocity"]})'
        for level in ACCENT_LEVELS["schemes"]["6-accent"]["levels"]
        if level["index"]>0
    )
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(path.name)} — ADC PatternLab</title><style>
:root{{--bg:#f4f6f8;--panel:#fff;--ink:#17202a;--muted:#65717e;--line:#d9dee4;--major:#9aa6b2;--raw:#1f6feb;--slot:#8a3ffc;--warn:#c2410c;--v0:#dbeafe;--v1:#93c5fd;--v2:#3b82f6;--v3:#1e3a8a;--h0:#fee2e2;--h1:#fecaca;--h2:#f87171;--h3:#dc2626;--h4:#7f1d1d}}@media(prefers-color-scheme:dark){{:root{{--bg:#11151a;--panel:#1a2027;--ink:#e6edf3;--muted:#9da9b5;--line:#303843;--major:#66717d;--raw:#58a6ff;--slot:#c297ff;--warn:#ff9b6a;--v0:#23395d;--v1:#2f6fab;--v2:#58a6ff;--v3:#b6d8ff;--h0:#4c1d1d;--h1:#7f1d1d;--h2:#b91c1c;--h3:#ef4444;--h4:#fca5a5}}}}*{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--ink)}}header{{position:sticky;top:0;z-index:1000;padding:14px 18px 12px;background:var(--panel);border-bottom:1px solid var(--line);box-shadow:0 3px 12px rgba(0,0,0,.14)}}h1{{margin:0 0 6px;font-size:20px}}.summary{{font-size:13px;color:var(--muted)}}button{{margin-top:8px;padding:7px 11px;border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--ink);font-weight:700;cursor:pointer}}.legend{{margin-left:14px;font-size:12px;color:var(--muted)}}.lg{{display:inline-block;width:12px;height:12px;margin:0 3px 0 7px;vertical-align:-2px;border:1px solid var(--line)}}.v0{{background:var(--v0)}}.v1{{background:var(--v1)}}.v2{{background:var(--v2)}}.v3{{background:var(--v3)}}.h0{{background:var(--h0)}}.h1{{background:var(--h1)}}.h2{{background:var(--h2)}}.h3{{background:var(--h3)}}.h4{{background:var(--h4)}}main{{overflow:auto;padding:12px}}svg{{display:block;cursor:pointer;user-select:none}}.bg{{fill:var(--panel);stroke:var(--line)}}.bad .bg{{stroke:var(--warn);stroke-width:2}}.title{{fill:var(--ink);font-size:13px;font-weight:750}}.meta{{fill:var(--muted);font-size:10px}}.sid{{fill:var(--slot);font-size:12px;font-weight:800}}.warning{{fill:var(--warn);font-size:10px;font-weight:800}}.row{{fill:var(--ink);font-size:8.5px}}.guide,.rguide{{stroke:var(--line);stroke-width:.7}}.major{{stroke:var(--major);stroke-width:1.45}}.barline{{stroke:var(--ink);stroke-width:2.1;opacity:.72}}.hit{{opacity:1}}.rawduration{{stroke-width:1.4;stroke-linecap:round;opacity:.72}}.rawhit{{stroke:var(--panel);stroke-width:.8}}.unknown-row{{fill:#dc2626!important;font-weight:800}}.deviation-aligned.rawhit{{fill:#2563eb}}.deviation-near.rawhit{{fill:#0891b2}}.deviation-moderate.rawhit{{fill:#f59e0b}}.deviation-far.rawhit{{fill:#dc2626}}.deviation-aligned.rawduration{{stroke:#2563eb}}.deviation-near.rawduration{{stroke:#0891b2}}.deviation-moderate.rawduration{{stroke:#f59e0b}}.deviation-far.rawduration{{stroke:#dc2626}}.ghost{{stroke:var(--ink);stroke-width:1;stroke-dasharray:2 1}}.flamgrace{{stroke-width:1.5;stroke-dasharray:none;opacity:1}}.flammain{{stroke-width:.8}}.ornnote{{fill:#7c3aed!important;stroke:#4c1d95!important;stroke-width:1.2!important;stroke-dasharray:none!important}}.ornduration{{stroke:#7c3aed!important;opacity:.95!important}}.slothit{{fill:var(--slot)}}.slotcell{{stroke:var(--panel);stroke-width:.35}}.velocity0{{fill:var(--v0)}}.velocity1{{fill:var(--v1)}}.velocity2{{fill:var(--v2)}}.velocity3{{fill:var(--v3)}}svg.accentmode .slotcell.hitstrength0{{fill:var(--h0)}}svg.accentmode .slotcell.hitstrength1{{fill:var(--h1)}}svg.accentmode .slotcell.hitstrength2{{fill:var(--h2)}}svg.accentmode .slotcell.hitstrength3{{fill:var(--h3)}}svg.accentmode .slotcell.hitstrength4{{fill:var(--h4)}}.unknown{{fill:var(--warn);stroke:var(--panel)}}.subdiv-layer{{display:none}}.subdiv-layer.active{{display:inline}}.slot{{display:none}}svg.slotmode .raw{{display:none}}svg.slotmode .slot{{display:inline}}details{{margin:0 18px 18px;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}}.pattern-controls-wrap{{overflow:visible}}.pattern-controls{{height:194px;display:flex;flex-direction:column;gap:5px;padding:6px 8px;border-top:1px solid var(--line);font:11px system-ui,sans-serif;color:var(--ink);background:var(--panel)}}.pattern-controls label{{display:flex;align-items:center;gap:4px;white-space:nowrap;min-width:0}}.pattern-controls select,.pattern-controls input[type=text]{{min-width:0;padding:3px 4px;border:1px solid var(--line);border-radius:5px;background:var(--panel);color:var(--ink);font-size:10.5px}}.catalog-row{{display:grid;grid-template-columns:70px 1fr 52px;gap:7px;align-items:center}}.catalog-row .genre-select{{width:100%}}.pattern-controls .number-label{{height:25px}}.pattern-controls .start-number{{width:62px}}.pattern-controls .name-preview{{min-width:92px;font-weight:800;color:var(--slot)}}.timing-fit{{display:flex;align-items:center;gap:5px;min-height:17px;white-space:nowrap;color:var(--muted);font-size:10px}}.timing-fit strong{{color:var(--ink)}}.fit-item{{padding:1px 3px;border-radius:4px}}.fit-item.selected{{background:var(--bg);color:var(--ink);font-weight:800;outline:1px solid var(--line)}}.fit-best{{margin-left:auto;color:var(--slot);font-weight:800}}.playback-box{{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:31px 28px 22px 7px;gap:4px 7px;padding-top:4px;border-top:1px solid var(--line)}}.pattern-controls .play-compare{{grid-column:1 / 3;margin:0;padding:6px 8px;font-size:11px;background:var(--slot);color:#fff;border-color:var(--slot)}}.playback-settings{{grid-column:1 / 3;display:grid;grid-template-columns:1fr 1fr;gap:8px;align-items:center}}.playback-settings label{{display:grid;grid-template-columns:auto 1fr;gap:4px}}.playback-settings select{{width:100%}}.play-stage{{grid-column:1 / 2;display:flex;align-items:center;gap:4px;min-width:0}}.stage-pill{{display:inline-flex;align-items:center;justify-content:center;min-width:29px;height:19px;padding:0 7px;border:1px solid var(--line);border-radius:999px;background:var(--panel);color:var(--muted);font-size:10px;font-weight:800;opacity:.55;transition:opacity .15s,background .15s,color .15s,transform .15s}}.stage-pill.unused{{display:none}}.stage-pill.active{{opacity:1;color:#fff;transform:translateY(-1px)}}.stage-raw.active{{background:#2563eb;border-color:#2563eb}}.stage-6.active{{background:#7c3aed;border-color:#7c3aed}}.stage-4.active{{background:#dc2626;border-color:#dc2626}}.play-progress{{grid-column:1 / 3;height:6px;overflow:hidden;border-radius:999px;background:var(--line)}}.play-progress span{{display:block;width:0;height:100%;background:var(--slot);transition:width .08s linear}}.pattern-controls .compare-midi{{grid-column:2 / 3;grid-row:3;margin:0;padding:3px 7px;font-size:10.5px;background:var(--panel);color:var(--ink);border-color:var(--line)}}.pattern-controls .play-compare.playing{{background:var(--warn);border-color:var(--warn)}}.pattern-controls .invalid{{border-color:var(--warn)!important;outline:1px solid var(--warn)}}#current-pattern{{display:inline-block;margin-left:10px;padding:3px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:11px;font-weight:700}}#number-status{{display:inline-block;margin-left:10px;font-size:12px;color:var(--muted)}}#number-status.error{{color:var(--warn);font-weight:700}}input[type=checkbox]{{width:16px;height:16px}}

.header-top{{display:flex;align-items:flex-start;justify-content:space-between;gap:18px}}.brand h1{{margin:0;font-size:20px;line-height:1.15}}.brand-sub{{margin-top:3px;color:var(--muted);font-size:11px}}.header-state{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}}.mode-badge{{padding:4px 9px;border-radius:999px;background:var(--bg);border:1px solid var(--line);font-size:11px;font-weight:800}}.summary{{margin-top:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.header-actions{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:9px;padding-top:8px;border-top:1px solid var(--line)}}.tabs,.action-buttons,.service-area{{display:flex;align-items:center;gap:6px;flex-wrap:wrap}}.tab-button{{margin:0;padding:6px 11px;background:transparent}}.tab-button.active{{background:var(--ink);border-color:var(--ink);color:var(--panel)}}.header-actions button{{margin:0}}.service-dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--major)}}.service-dot.online{{background:#16a34a}}.service-dot.offline{{background:#dc2626}}.service-text{{font-size:11px;color:var(--muted)}}.legend-panel{{margin:0;padding:0;border:0;background:transparent}}.legend-panel summary{{cursor:pointer;font-size:11px;font-weight:700;color:var(--muted);list-style:none}}.legend-panel summary::-webkit-details-marker{{display:none}}.legend-content{{position:absolute;right:18px;top:100%;width:min(680px,calc(100vw - 36px));padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel);box-shadow:0 8px 24px rgba(0,0,0,.18);font-size:11px;color:var(--muted);line-height:1.7}}.tab-panel{{display:none}}.tab-panel.active{{display:block}}.midi-browser{{max-width:900px;margin:18px auto;padding:18px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}}.midi-toolbar{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px}}.midi-toolbar h2{{margin:0;font-size:18px}}.midi-list{{display:flex;flex-direction:column;border:1px solid var(--line);border-radius:8px;overflow:hidden}}.midi-row{{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:12px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--line);cursor:pointer}}.midi-row:last-child{{border-bottom:0}}.midi-row:hover,.midi-row.playing{{background:var(--bg)}}.midi-name{{font-weight:750;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.midi-meta{{font-size:11px;color:var(--muted)}}.midi-play{{margin:0;padding:5px 10px}}.library-player{{margin-top:14px;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--bg)}}.library-now{{display:flex;justify-content:space-between;gap:12px;font-size:12px}}.library-progress{{height:8px;margin-top:9px;overflow:hidden;border-radius:999px;background:var(--line)}}.library-progress span{{display:block;width:0;height:100%;background:var(--slot)}}.library-controls{{display:flex;gap:8px;margin-top:8px}}.library-controls button{{margin:0}}.empty-message{{padding:18px;text-align:center;color:var(--muted)}}
</style></head><body><header>
<div class="header-top"><div class="brand"><h1>{html.escape(path.name)}</h1><div class="brand-sub">ADC PatternLab · {VERSION}</div></div><div class="header-state"><span id="current-pattern">Viewing: —</span><strong id="mode" class="mode-badge">RAW GM NOTES</strong></div></div>
<div class="summary" title="{header_summary}">{header_summary}</div>
<div class="header-actions"><div class="tabs"><button class="tab-button active" data-tab="analysis" type="button">Pattern Analysis</button><button class="tab-button" data-tab="midi" type="button">MIDI Files</button></div><div class="action-buttons"><button id="toggle">RAW / QUANTIZED</button><button id="slot-display" type="button" class="quantized-only">Velocity / Accent</button><button id="download-csv" type="button">Download CSV</button><span id="number-status"></span></div><div class="service-area"><span id="service-dot" class="service-dot"></span><span id="service-text" class="service-text">Checking playback service…</span><details class="legend-panel"><summary>Legend ▾</summary><div class="legend-content"><div>Velocity: <i class="lg v0"></i>0 (1–31) <i class="lg v1"></i>1 (32–63) <i class="lg v2"></i>2 (64–95) <i class="lg v3"></i>3 (96–127)</div><div>ADX 6-accent: {accent_legend}</div><div>RAW grid: <i class="lg" style="background:#2563eb"></i>aligned <i class="lg" style="background:#0891b2"></i>near <i class="lg" style="background:#f59e0b"></i>moderate <i class="lg" style="background:#dc2626"></i>far</div><div>RAW: <i class="lg" style="background:#7c3aed;border-color:#4c1d95"></i>ORN candidate · red label = outside SLOT_MAP</div></div></details></div></div>
</header><section id="tab-analysis" class="tab-panel active"><main><svg id="matrix" xmlns="http://www.w3.org/2000/svg" width="{sw}" height="{sh}" viewBox="0 0 {sw} {sh}">{''.join(body)}</svg></main><details><summary>Analysis notes</summary><p>Each block is checked only against earlier blocks in the same MIDI file. Pattern identity uses only relative onset tick and raw MIDI note. Velocity and note duration are ignored. A repeated block keeps its original Pattern number and omits the matrix drawing.</p><p>A final odd bar containing only one onset group at its beginning is labeled ENDING HIT and excluded from the pattern catalog.</p><p>Each card initially uses the automatically detected resolution. Its own Resolution selector can immediately switch the reference grid and SLOT quantization among 16, 32, 8T, and 16T without affecting other cards. Reloading the HTML restores the original automatic selections. Grid fit is a separate visual diagnostic: for each candidate grid it reports the percentage of RAW note-on events that fall within 5% of one grid step from the nearest line. Best marks the highest such percentage, with mean normalized error used only to break ties. It does not overwrite the shared rhythm-analysis decision.</p><p>If no SLOT_MAP covers every note, the nearest map is used, the card receives a red border, and uncovered MIDI notes are listed as MISSING NOTES. Ties fall back conservatively toward lower IDs, beginning with LEGACY 12.</p><p>RAW view places every note-on circle at its original MIDI tick position and extends a horizontal line to the recorded note-off position. Very short durations receive a two-pixel minimum display line; the note-on position itself is never moved. The vertical subdivision lines are reference overlays only; changing a card’s Resolution selector never moves RAW notes. Velocity controls circle size. RAW note colors indicate distance from the nearest line of the currently selected resolution and are recalculated independently for each card whenever its Resolution selector changes. Notes that currently trigger automatic ORN candidacy are shown in purple, overriding deviation color: velocity ≤ 30 ghost candidates and the grace note of each detected flam pair. Hovering a purple note shows the exact reason, including velocity threshold or flam confidence, tick gap, threshold, and whether the grace is removed from subdivision. Flam main hits remain blue because they stay in the ADX grid.</p><p>The report has two tabs: Pattern Analysis and Local MIDI Files. The MIDI Files tab obtains a restricted list of immediate, non-symlink MID files from the local playback service. The browser receives opaque IDs rather than filesystem paths and plays selected files through the configured FluidSynth/SF2 backend. In SLOT view, each retained hit fills its complete quantized cell. The Play button sends the MIDI generated from the current Compare Mode directly to the local PatternLab playback service, which uses the configured FluidSynth executable and SF2 SoundFont. The QUANTIZED display button switches between the original four-band MIDI Velocity view and the ADX 6-accent preview. Each non-duplicate card can play or download exactly the sequence selected in Compare Mode: RAW only, RAW → 6, RAW → 4, or RAW → 6 → 4. Every included section is repeated twice, and adjacent sections are separated by one quarter-note beat. ADX Accent uses the five playable levels of the JSON-defined 6-accent scheme. The displayed symbol, label, velocity range, and representative velocity come from accent_levels.json; an empty cell represents Rest. Flam grace notes marked for removal from subdivision are intentionally omitted there and belong to ORN; the main hit remains in the grid. Ghost candidates that are not classified as removable flam grace notes remain visible. When multiple retained raw hits collapse into one slot/cell, the strongest velocity is shown.</p><p>SLOT_MAP usage: <code>{html.escape(json.dumps(summary,ensure_ascii=False))}</code></p><p>The shared adc_rhythm_analysis module owns the complete subdivision decision: flam detection, grace-note exclusion, onset phase, note-duration evidence, and conservative filename hints. The same flam-filtered events are used for both phase and duration scoring. Beat anchors and the shared half-beat remain excluded from phase evidence.</p></details></section><section id="tab-midi" class="tab-panel"><div class="midi-browser"><div class="midi-toolbar"><div><h2>Local MIDI Files</h2><div class="midi-meta">Allowed local MIDI directory · paths are not exposed to the browser</div></div><button id="refresh-midi" type="button">Refresh</button></div><div id="midi-list" class="midi-list"><div class="empty-message">Open this tab to load the MIDI file list.</div></div><div class="library-player"><div class="library-now"><strong id="library-file">Nothing playing</strong><span id="library-time">0:00 / 0:00</span></div><div class="library-progress"><span id="library-progress-fill"></span></div><div class="library-controls"><button id="library-stop" type="button">■ Stop</button></div></div></div></section><script>(()=>{{
const s=document.getElementById('matrix'),m=document.getElementById('mode'),slotDisplay=document.getElementById('slot-display');slotDisplay.style.display='none';
const BLOCK_DATA={block_data_json};
const ACCENT_SCHEMES={accent_levels_json};
const TPQ={mid.ticks_per_beat};
const SOURCE_STEM={json.dumps(path.stem)};
let midiFilesLoaded=false;let libraryStartedAt=0;let libraryDuration=0;let libraryAnimation=null;let libraryCurrentId='';
function formatTime(seconds){{if(!Number.isFinite(seconds)||seconds<0)return '—';const s=Math.floor(seconds);return `${{Math.floor(s/60)}}:${{String(s%60).padStart(2,'0')}}`;}}
function switchTab(name){{document.querySelectorAll('.tab-button').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.id===`tab-${{name}}`));if(name==='midi'&&!midiFilesLoaded)loadMidiFiles();}}
document.querySelectorAll('.tab-button').forEach(button=>button.addEventListener('click',()=>switchTab(button.dataset.tab)));
async function checkService(){{const dot=document.getElementById('service-dot'),text=document.getElementById('service-text');try{{const r=await fetch('/api/midi-files',{{cache:'no-store'}});if(!r.ok)throw new Error();dot.classList.add('online');dot.classList.remove('offline');text.textContent='Playback service connected';}}catch(_e){{dot.classList.add('offline');dot.classList.remove('online');text.textContent='Playback service unavailable';}}}}
function renderMidiFiles(files){{const list=document.getElementById('midi-list');list.innerHTML='';if(!files.length){{list.innerHTML='<div class="empty-message">No MID files in the allowed server directory.</div>';return;}}files.forEach(file=>{{if(!file||typeof file.id!=='string'||typeof file.name!=='string')return;const row=document.createElement('div');row.className='midi-row';row.dataset.fileId=file.id;row.innerHTML=`<div><div class="midi-name"></div><div class="midi-meta"></div></div><div class="midi-meta">${{formatTime(file.duration_seconds)}}</div><button class="midi-play" type="button">▶ Play</button>`;row.querySelector('.midi-name').textContent=file.name;row.querySelector('.midi-meta').textContent=`${{Math.max(1,Math.round(file.size/1024))}} KB`;row.querySelector('.midi-play').addEventListener('click',e=>{{e.stopPropagation();playLibraryFile(file.id,file.name,file.duration_seconds);}});row.addEventListener('dblclick',()=>playLibraryFile(file.id,file.name,file.duration_seconds));list.appendChild(row);}});}}
async function loadMidiFiles(){{const list=document.getElementById('midi-list');list.innerHTML='<div class="empty-message">Loading MIDI files…</div>';try{{const r=await fetch('/api/midi-files',{{cache:'no-store'}});if(!r.ok)throw new Error(await r.text());const data=await r.json();renderMidiFiles(Array.isArray(data.files)?data.files:[]);midiFilesLoaded=true;checkService();}}catch(error){{list.innerHTML='<div class="empty-message">Playback service is unavailable. Start play_server.py and open this report through localhost.</div>';checkService();}}}}
function updateLibraryProgress(){{if(!libraryStartedAt||!libraryDuration)return;const elapsed=(performance.now()-libraryStartedAt)/1000;const ratio=Math.max(0,Math.min(1,elapsed/libraryDuration));document.getElementById('library-progress-fill').style.width=`${{ratio*100}}%`;document.getElementById('library-time').textContent=`${{formatTime(elapsed)}} / ${{formatTime(libraryDuration)}}`;if(ratio<1)libraryAnimation=requestAnimationFrame(updateLibraryProgress);else stopLibraryVisual(false);}}
function stopLibraryVisual(clearRows=true){{if(libraryAnimation){{cancelAnimationFrame(libraryAnimation);libraryAnimation=null;}}libraryStartedAt=0;document.getElementById('library-progress-fill').style.width='0%';if(clearRows)document.querySelectorAll('.midi-row').forEach(r=>r.classList.remove('playing'));}}
async function playLibraryFile(fileId,name,knownDuration){{await stopPreview();try{{const r=await fetch('/play-file',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:fileId}})}});const data=await r.json();if(!r.ok)throw new Error(data.error||`HTTP ${{r.status}}`);libraryCurrentId=fileId;libraryDuration=Number(data.duration_seconds??knownDuration)||0;libraryStartedAt=performance.now();document.getElementById('library-file').textContent=String(data.name||name);document.querySelectorAll('.midi-row').forEach(row=>row.classList.toggle('playing',row.dataset.fileId===fileId));stopLibraryVisual(false);libraryStartedAt=performance.now();if(libraryDuration>0)updateLibraryProgress();else document.getElementById('library-time').textContent='Playing';}}catch(error){{alert('MIDI playback failed.\\n\\n'+error.message);}}}}
async function stopLibraryPlayback(){{try{{await fetch('/stop',{{method:'POST'}});}}catch(_e){{}}stopLibraryVisual();document.getElementById('library-file').textContent='Nothing playing';document.getElementById('library-time').textContent='0:00 / 0:00';}}
document.getElementById('refresh-midi').addEventListener('click',()=>{{midiFilesLoaded=false;loadMidiFiles();}});document.getElementById('library-stop').addEventListener('click',stopLibraryPlayback);checkService();
function t(){{const v=s.classList.toggle('slotmode');m.textContent=v?(s.classList.contains('accentmode')?'QUANTIZED SLOT MAP · ACCENT':'QUANTIZED SLOT MAP · VELOCITY'):'RAW GM NOTES';slotDisplay.style.display=v?'inline-block':'none'}}
function toggleSlotDisplay(){{const accent=s.classList.toggle('accentmode');slotDisplay.textContent=accent?'QUANTIZED: Accent':'QUANTIZED: Velocity';if(s.classList.contains('slotmode'))m.textContent=accent?'QUANTIZED SLOT MAP · ACCENT':'QUANTIZED SLOT MAP · VELOCITY';}}
s.addEventListener('click',e=>{{if(!e.target.closest('.pattern-controls'))t()}});document.getElementById('toggle').addEventListener('click',t);slotDisplay.addEventListener('click',toggleSlotDisplay);const currentPattern=document.getElementById('current-pattern');
const visibleCards=new Map();
const cardObserver=new IntersectionObserver(entries=>{{
  entries.forEach(entry=>{{const panel=entry.target;entry.isIntersecting?visibleCards.set(panel,entry.intersectionRatio):visibleCards.delete(panel);}});
  const best=[...visibleCards.entries()].sort((a,b)=>b[1]-a[1])[0]?.[0];
  if(best){{const pattern=best.dataset.patternNo||'—';const block=best.dataset.block||'—';currentPattern.textContent=`Viewing: Pattern #${{String(pattern).padStart(3,'0')}} · B${{String(block).padStart(3,'0')}}`;}}
}},{{root:null,rootMargin:'-170px 0px -55% 0px',threshold:[0,.15,.35,.6,.85]}});
document.querySelectorAll('.pattern-controls[data-pattern-no]').forEach(panel=>cardObserver.observe(panel));
function csvCell(value){{const x=String(value??'');return /[",\\n]/.test(x)?'"'+x.replace(/"/g,'""')+'"':x}}
function writeU16(a,v){{a.push((v>>8)&255,v&255)}}
function writeU32(a,v){{a.push((v>>>24)&255,(v>>>16)&255,(v>>>8)&255,v&255)}}
function writeVar(a,v){{let buffer=v&0x7f;while((v>>=7)){{buffer<<=8;buffer|=((v&0x7f)|0x80)}}for(;;){{a.push(buffer&255);if(buffer&0x80)buffer>>=8;else break}}}}
function asciiBytes(text){{return [...new TextEncoder().encode(text)]}}
function quantizedVelocity(v,schemeName){{
  const scheme=ACCENT_SCHEMES[schemeName];
  if(!scheme||!Array.isArray(scheme.levels))throw new Error(`Missing accent scheme: ${{schemeName}}`);
  const value=Math.max(0,Math.min(127,Number(v)||0));
  const level=scheme.levels.find(item=>value>=item.min_velocity&&value<=item.max_velocity);
  if(!level)throw new Error(`Velocity ${{value}} is not covered by ${{schemeName}}`);
  return level.representative_velocity;
}}
function slotForNote(slots,note){{for(let i=0;i<slots.length;i++)if(slots[i].notes.includes(note))return i;return -1}}
function quantizedEvents(data,subdiv,levels){{
  const cpb={{'16':4,'32':8,'8T':3,'16T':6}}[subdiv]||4;
  const stepTicks=TPQ/cpb;
  const maxCell=Math.max(0,Math.ceil(data.duration/stepTicks)-1);
  const cells=new Map();
  data.events.forEach(ev=>{{
    if(ev.excluded)return;
    const si=slotForNote(data.slots,ev.note);if(si<0)return;
    const cell=Math.max(0,Math.min(maxCell,Math.round(ev.tick/stepTicks)));
    const snappedTick=Math.min(data.duration-1,Math.round(cell*stepTicks));
    const key=si+':'+cell;const prev=cells.get(key);
    const candidate={{...ev,originalTick:ev.tick,snappedTick}};
    if(!prev||ev.vel>prev.vel)cells.set(key,candidate);
  }});
  const out=[];
  cells.forEach((ev,key)=>{{
    const [siText]=key.split(':');const si=Number(siText);
    const start=ev.snappedTick;
    const duration=Math.max(1,Math.min(Math.max(1,ev.dur||Math.round(TPQ/8)),data.duration-start));
    out.push({{tick:start,originalTick:ev.originalTick,moveTicks:start-ev.originalTick,note:data.slots[si].representative,vel:quantizedVelocity(ev.vel,levels===4?'4-accent':'6-accent'),dur:duration}});
  }});
  return out.sort((a,b)=>a.tick-b.tick||a.note-b.note);
}}
function addRepeatedSection(target,events,start,duration,label){{
  target.push({{tick:start,type:'marker',text:label}});
  for(let rep=0;rep<2;rep++){{
    const base=start+rep*duration;
    events.forEach(ev=>{{
      const on=base+Math.max(0,Math.min(duration-1,Math.round(ev.tick)));
      const off=Math.max(on+1,Math.min(base+duration,on+Math.max(1,Math.round(ev.dur||1))));
      target.push({{tick:on,type:'on',note:ev.note,vel:ev.vel}});
      target.push({{tick:off,type:'off',note:ev.note,vel:0}});
    }});
  }}
  return start+duration*2;
}}
function makeComparisonMidi(data,subdiv,compareMode='both'){{
  const events=[];let cursor=0;
  const raw=data.events.map(ev=>({{tick:ev.tick,note:ev.note,vel:ev.vel,dur:ev.dur}}));
  cursor=addRepeatedSection(events,raw,cursor,data.duration,'RAW x2');
  const sections=[];
  if(compareMode==='both'||compareMode==='6')sections.push([quantizedEvents(data,subdiv,6),'QUANTIZED 6-ACCENT x2']);
  if(compareMode==='both'||compareMode==='4')sections.push([quantizedEvents(data,subdiv,4),'QUANTIZED 4-ACCENT x2']);
  sections.forEach(([sectionEvents,label])=>{{cursor+=TPQ;cursor=addRepeatedSection(events,sectionEvents,cursor,data.duration,label);}});
  const [num,den]=data.meter||[4,4];const dd=Math.max(0,Math.round(Math.log2(den||4)));
  events.push({{tick:0,type:'tempo',tempo:data.tempo||500000}});
  events.push({{tick:0,type:'timesig',num:num||4,dd}});
  events.push({{tick:cursor+1,type:'end'}});
  const order={{tempo:0,timesig:1,marker:2,off:3,on:4,end:9}};
  events.sort((a,b)=>a.tick-b.tick||(order[a.type]-order[b.type])||((a.note||0)-(b.note||0)));
  const track=[];let last=0;
  events.forEach(ev=>{{
    writeVar(track,Math.max(0,ev.tick-last));last=ev.tick;
    if(ev.type==='tempo'){{const t=ev.tempo;track.push(0xff,0x51,0x03,(t>>16)&255,(t>>8)&255,t&255)}}
    else if(ev.type==='timesig'){{track.push(0xff,0x58,0x04,ev.num&255,ev.dd&255,24,8)}}
    else if(ev.type==='marker'){{const b=asciiBytes(ev.text);track.push(0xff,0x06);writeVar(track,b.length);track.push(...b)}}
    else if(ev.type==='on')track.push(0x99,ev.note&127,Math.max(1,Math.min(127,ev.vel|0)))
    else if(ev.type==='off')track.push(0x89,ev.note&127,0)
    else if(ev.type==='end')track.push(0xff,0x2f,0x00);
  }});
  const file=[...asciiBytes('MThd')];writeU32(file,6);writeU16(file,0);writeU16(file,1);writeU16(file,TPQ);
  file.push(...asciiBytes('MTrk'));writeU32(file,track.length);file.push(...track);
  return new Uint8Array(file);
}}
let previewButton=null;
let previewEndTimer=null;
let previewAnimationFrame=null;
let previewTimeline=null;
function clearPlaybackVisual(panel){{
  if(!panel)return;
  panel.querySelectorAll('.stage-pill').forEach(pill=>pill.classList.remove('active','unused'));
  const progress=panel.querySelector('.play-progress span');if(progress)progress.style.width='0%';
}}
function resetPreviewButton(){{
  if(previewEndTimer){{clearTimeout(previewEndTimer);previewEndTimer=null;}}
  if(previewAnimationFrame){{cancelAnimationFrame(previewAnimationFrame);previewAnimationFrame=null;}}
  if(previewButton){{
    previewButton.textContent='▶ Play';
    previewButton.classList.remove('playing');
    clearPlaybackVisual(previewButton.closest('.pattern-controls'));
    previewButton=null;
  }}
  previewTimeline=null;
}}
async function stopPreview(){{
  try{{await fetch('/stop',{{method:'POST'}});}}catch(_e){{}}
  resetPreviewButton();
}}
function comparisonSections(data,compareMode){{
  const sections=[];let cursor=0;
  sections.push({{stage:'raw',start:cursor,end:cursor+data.duration*2}});cursor+=data.duration*2;
  const wanted=[];
  if(compareMode==='both'||compareMode==='6')wanted.push('6');
  if(compareMode==='both'||compareMode==='4')wanted.push('4');
  wanted.forEach(stage=>{{cursor+=TPQ;sections.push({{stage,start:cursor,end:cursor+data.duration*2}});cursor+=data.duration*2;}});
  return {{sections,totalTicks:cursor}};
}}
function comparisonDurationTicks(data,compareMode){{return comparisonSections(data,compareMode).totalTicks;}}
function preparePlaybackVisual(panel,compareMode){{
  panel.querySelectorAll('.stage-pill').forEach(pill=>{{
    const stage=pill.dataset.stage;
    const used=stage==='raw'||compareMode==='both'||compareMode===stage;
    pill.classList.toggle('unused',!used);pill.classList.remove('active');
  }});
  const progress=panel.querySelector('.play-progress span');if(progress)progress.style.width='0%';
}}
function updatePlaybackVisual(){{
  if(!previewTimeline||!previewButton)return;
  const panel=previewButton.closest('.pattern-controls');if(!panel)return;
  const elapsedSeconds=Math.max(0,(performance.now()-previewTimeline.startedAt)/1000);
  const elapsedTicks=elapsedSeconds/previewTimeline.secondsPerTick;
  const ratio=Math.max(0,Math.min(1,elapsedTicks/previewTimeline.totalTicks));
  const progress=panel.querySelector('.play-progress span');if(progress)progress.style.width=`${{ratio*100}}%`;
  const active=previewTimeline.sections.find(section=>elapsedTicks>=section.start&&elapsedTicks<section.end)?.stage||null;
  panel.querySelectorAll('.stage-pill').forEach(pill=>pill.classList.toggle('active',pill.dataset.stage===active));
  if(ratio<1)previewAnimationFrame=requestAnimationFrame(updatePlaybackVisual);
}}
async function playComparison(panel){{
  const button=panel.querySelector('.play-compare');
  if(previewButton===button){{await stopPreview();return;}}
  await stopPreview();
  const data=BLOCK_DATA[String(panel.dataset.block)];
  if(!data){{alert('FluidSynth playback is unavailable for this card.');return;}}
  const subdiv=panel.querySelector('.subdivision-select')?.value||'16';
  const compareMode=panel.querySelector('.compare-mode-select')?.value||'both';
  const bytes=makeComparisonMidi(data,subdiv,compareMode);
  preparePlaybackVisual(panel,compareMode);
  button.textContent='Connecting…';button.disabled=true;
  try{{
    const response=await fetch('/play',{{method:'POST',headers:{{'Content-Type':'audio/midi'}},body:bytes}});
    const message=await response.text();
    if(!response.ok)throw new Error(message||`HTTP ${{response.status}}`);
    previewButton=button;button.textContent='■ Stop';button.classList.add('playing');
    const secondsPerTick=(data.tempo||500000)/1000000/TPQ;
    const timeline=comparisonSections(data,compareMode);
    previewTimeline={{...timeline,secondsPerTick,startedAt:performance.now()}};
    updatePlaybackVisual();
    const totalSeconds=timeline.totalTicks*secondsPerTick;
    previewEndTimer=setTimeout(resetPreviewButton,Math.ceil((totalSeconds+1.2)*1000));
  }}catch(error){{
    clearPlaybackVisual(panel);
    alert('FluidSynth playback failed.\\n\\nStart play_server.py and open this report through http://127.0.0.1:8123/.\\n\\n'+error.message);
  }}finally{{
    button.disabled=false;
    if(!previewButton)button.textContent='▶ Play';
  }}
}}
function downloadComparison(panel){{
  const data=BLOCK_DATA[String(panel.dataset.block)];if(!data){{alert('Comparison MIDI is unavailable for this card.');return}}
  const subdiv=panel.querySelector('.subdivision-select')?.value||'16';
  const compareMode=panel.querySelector('.compare-mode-select')?.value||'both';
  const bytes=makeComparisonMidi(data,subdiv,compareMode);const blob=new Blob([bytes],{{type:'audio/midi'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);const modeTag={{raw:'RAW','6':'RAW-6','4':'RAW-4',both:'RAW-6-4'}}[compareMode]||'RAW-6-4';
  a.download=`${{SOURCE_STEM}}_B${{String(panel.dataset.block).padStart(3,'0')}}_${{subdiv}}_${{modeTag}}.MID`;
  document.body.appendChild(a);a.click();setTimeout(()=>{{URL.revokeObjectURL(a.href);a.remove()}},0);
}}
function allPanels(){{return [...document.querySelectorAll('.pattern-controls')]}}
function exportedPanels(){{
  return allPanels().filter(panel=>{{
    const exp=panel.querySelector('.export-check');
    return exp && !exp.disabled && exp.checked;
  }});
}}
function clearCalculated(){{
  allPanels().forEach(panel=>{{
    panel.dataset.patternName='';
    panel.querySelector('.name-preview').textContent='';
    const input=panel.querySelector('.start-number');
    input.classList.remove('invalid');
    if(input.dataset.auto==='1'){{
      input.value='';
      delete input.dataset.auto;
    }}
  }});
}}
function updateStatus(errors, count){{
  const status=document.getElementById('number-status');
  if(errors.length){{
    status.textContent=errors[0]+(errors.length>1?` (+${{errors.length-1}})`:'');
    status.classList.add('error');
  }}else if(count){{
    status.textContent=`${{count}} NAME(s) ready`;
    status.classList.remove('error');
  }}else{{
    status.textContent='';
    status.classList.remove('error');
  }}
}}
function calculateNames(showAlert=false){{
  clearCalculated();
  const panels=exportedPanels();
  const byGenre=new Map();
  panels.forEach(panel=>{{
    const genre=panel.querySelector('.genre-select').value;
    if(!byGenre.has(genre))byGenre.set(genre,[]);
    byGenre.get(genre).push(panel);
  }});
  const errors=[];
  const names=new Set();
  if(panels.length===0)errors.push('No patterns are selected for export.');
  byGenre.forEach((group,genre)=>{{
    const manual=group.filter(panel=>{{
      const input=panel.querySelector('.start-number');
      return input.value.trim()!=='' && input.dataset.auto!=='1';
    }});
    if(manual.length===0){{errors.push(`${{genre}}: enter a starting number in the first exported card.`);return;}}
    if(manual.length>1){{
      manual.forEach(panel=>panel.querySelector('.start-number').classList.add('invalid'));
      errors.push(`${{genre}}: starting numbers appear in more than one exported card.`);return;
    }}
    if(manual[0]!==group[0]){{
      manual[0].querySelector('.start-number').classList.add('invalid');
      errors.push(`${{genre}}: enter the starting number in block ${{group[0].dataset.block}}.`);return;
    }}
    const input=manual[0].querySelector('.start-number');
    const raw=input.value.trim();
    if(!/^\\d{{1,4}}$/.test(raw)){{
      input.classList.add('invalid');errors.push(`${{genre}}: use an integer from 1 to 9999.`);return;
    }}
    const first=Number(raw);
    if(first<1 || first>9999 || first+group.length-1>9999){{
      input.classList.add('invalid');errors.push(`${{genre}}: numbering must remain within 0001–9999.`);return;
    }}
    group.forEach((panel,index)=>{{
      const number=first+index;
      const padded=String(number).padStart(4,'0');
      const numberInput=panel.querySelector('.start-number');
      if(index>0){{numberInput.value=padded;numberInput.dataset.auto='1';}}
      const name=`${{genre}}_${{padded}}`;
      panel.dataset.patternName=name;
      panel.querySelector('.name-preview').textContent=name;
      if(names.has(name)){{
        numberInput.classList.add('invalid');
        errors.push(`Duplicate NAME: ${{name}}.`);
      }}
      names.add(name);
    }});
  }});
  updateStatus(errors,panels.length);
  if(errors.length && showAlert)alert('Cannot download CSV:\\n\\n'+errors.join('\\n'));
  return errors.length===0;
}}
function updateRawDeviation(card, selected){{
  const cellsPerBeat={{'16':4,'32':8,'8T':3,'16T':6}}[selected]||4;
  const duration=Number(card.dataset.durationTicks)||1;
  const beats=duration/Number({mid.ticks_per_beat});
  const cols=Math.max(1,Math.round(beats*cellsPerBeat));
  const stepTicks=duration/cols;
  card.querySelectorAll('.raw-event[data-tick-offset]').forEach(el=>{{
    const offset=Number(el.dataset.tickOffset)||0;
    const nearest=Math.round(offset/stepTicks)*stepTicks;
    const ratio=Math.abs(offset-nearest)/stepTicks;
    el.classList.remove('deviation-aligned','deviation-near','deviation-moderate','deviation-far');
    const cls=ratio<=0.05?'deviation-aligned':ratio<=0.15?'deviation-near':ratio<=0.30?'deviation-moderate':'deviation-far';
    el.classList.add(cls);
  }});
}}
function applySubdivision(panel){{
  const select=panel.querySelector('.subdivision-select');
  if(!select || select.disabled)return;
  const card=document.querySelector(`g.pattern-card[data-block="${{panel.dataset.block}}"]`);
  if(!card)return;
  const selected=select.value;
  card.querySelectorAll('.subdiv-layer').forEach(layer=>{{layer.classList.toggle('active',layer.dataset.subdiv===selected);}});
  const summary=card.querySelector('.grid-summary');
  if(summary){{const cells={{'16':4,'32':8,'8T':3,'16T':6}}[selected]||4;summary.textContent=(summary.dataset.prefix||'')+cells+' cells/beat';}}
  panel.querySelectorAll('.fit-item').forEach(item=>item.classList.toggle('selected',item.dataset.subdiv===selected));
  updateRawDeviation(card,selected);
}}
allPanels().forEach(panel=>{{
  const input=panel.querySelector('.start-number');
  input.addEventListener('input',()=>{{delete input.dataset.auto;calculateNames(false)}});
  panel.querySelector('.genre-select').addEventListener('change',()=>calculateNames(false));
  panel.querySelector('.export-check').addEventListener('change',()=>calculateNames(false));
  const subdivision=panel.querySelector('.subdivision-select');
  if(subdivision){{subdivision.addEventListener('change',()=>{{applySubdivision(panel);calculateNames(false)}});applySubdivision(panel);}}
  const compareMode=panel.querySelector('.compare-mode-select');
  if(compareMode)compareMode.addEventListener('change',()=>{{if(previewButton)void stopPreview();}});
  const play=panel.querySelector('.play-compare');
  if(play&&!play.disabled)play.addEventListener('click',e=>{{e.stopPropagation();void playComparison(panel)}});
  const compare=panel.querySelector('.compare-midi');
  if(compare&&!compare.disabled)compare.addEventListener('click',e=>{{e.stopPropagation();downloadComparison(panel)}});
}});
calculateNames(false);
document.getElementById('download-csv').addEventListener('click',()=>{{
  if(!calculateNames(true))return;
  const rows=[['FILE','START_BAR','END_BAR','NAME','TIME_SIG','SLOT_MAP','EXPORT','GENRE','SUBDIV','ORN','DUPLICATE_OF','SOURCE']];
  document.querySelectorAll('.pattern-controls').forEach(panel=>{{
    const exp=panel.querySelector('.export-check');
    const genre=panel.querySelector('.genre-select');
    const subdivision=panel.querySelector('.subdivision-select');
    const orn=panel.querySelector('.orn-check');
    const sourceRef={json.dumps(path.name)}+':'+panel.dataset.startBar+'-'+panel.dataset.endBar;
    rows.push([{json.dumps(path.name)},panel.dataset.startBar,panel.dataset.endBar,panel.dataset.patternName||'',panel.dataset.timeSig,panel.dataset.slotMap,exp.checked?'YES':'NO',genre.value,subdivision.value,orn.checked?'YES':'NO',panel.dataset.duplicateOf,sourceRef]);
  }});
  const csv='\\uFEFF'+rows.map(r=>r.map(csvCell).join(',')).join('\\r\\n');
  const blob=new Blob([csv],{{type:'text/csv;charset=utf-8'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download={json.dumps(path.stem + "_patternlab.csv")};document.body.appendChild(a);a.click();setTimeout(()=>{{URL.revokeObjectURL(a.href);a.remove()}},0);
}});
}})();</script></body></html>'''

def main(argv=None):
    p=argparse.ArgumentParser(prog=SCRIPT_NAME,description="Generate an interactive HTML/SVG drum pattern catalog from one MIDI file."); p.add_argument("input_midi",type=Path); p.add_argument("-o","--output",type=Path); p.add_argument("--slot-maps",type=Path,help="Canonical slot_map_definitions.json (default: beside this script)"); p.add_argument("--accent-levels",type=Path,help="accent_levels.json with 4-accent and 6-accent boundaries/representatives (default: beside this script)"); p.add_argument("--skip-leading-empty-bars",action="store_true",help="omit leading bars without CH10 note-on events while preserving absolute bar numbers"); p.add_argument("--version",action="version",version=VERSION_TEXT); a=p.parse_args(argv)
    if not a.input_midi.is_file():print(f'[ERROR] not found: {a.input_midi}',file=sys.stderr);return 2
    slot_map_path=a.slot_maps or Path(__file__).with_name("slot_map_definitions.json")
    accent_level_path=a.accent_levels or Path(__file__).with_name("accent_levels.json")
    global MAPS,ACCENT_LEVELS
    try:
        MAPS=load_slot_maps(slot_map_path)
        ACCENT_LEVELS=load_accent_levels(accent_level_path)
    except ValueError as e:print(f'[ERROR] {e}',file=sys.stderr);return 2
    try:mid=MidiFile(str(a.input_midi))
    except Exception as e:print(f'[ERROR] cannot read MIDI: {e}',file=sys.stderr);return 2
    ev,ts,mx=collect(mid); all_bars=make_bars(mid.ticks_per_beat,ts,mx); bars_=all_bars; skipped=0
    if a.skip_leading_empty_bars:
        bars_,skipped=skip_leading_empty_bars(all_bars,ev)
    bb=blocks(bars_,ev,mid.ticks_per_beat,a.input_midi.name)
    out=a.output or a.input_midi.with_name(a.input_midi.stem+'_PatternLab.html')
    # Windows preserves an existing directory entry's old letter case when the
    # same case-insensitive filename is opened again. Remove a legacy
    # *_patternlab.html entry first so the requested *_PatternLab.html spelling
    # is actually recorded on disk.
    if a.output is None:
        legacy=a.input_midi.with_name(a.input_midi.stem+'_patternlab.html')
        if legacy.exists() and legacy.resolve() != out.resolve():
            legacy.unlink()
        elif legacy.exists() and legacy.name != out.name:
            legacy.unlink()
    out.write_text(render(a.input_midi,mid,bars_,bb,skipped),encoding='utf-8')
    print(VERSION_TEXT); print(f'[OK] {out}'); print(f'[OK] bars={len(bars_)}, blocks={len(bb)}, drum_note_on={len(ev)}, skipped_leading_empty_bars={skipped}'); return 0
if __name__=='__main__':raise SystemExit(main())
