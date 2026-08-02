#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adc-patternlab.py 260802e

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

from adc_rhythm_analysis import analyze_event_rhythm, detect_flams

SCRIPT_NAME="adc-patternlab.py"; VERSION="260802e"; VERSION_TEXT=f"{SCRIPT_NAME} {VERSION}"
GHOST_CANDIDATE_MAX_VELOCITY=30
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
class Slot: label:str; notes:Tuple[int,...]
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
            seen_slots.add(slot_no); slots.append((slot_no,Slot(label,tuple(allowed))))
        expected=list(range(len(slots)))
        actual=sorted(seen_slots)
        if actual!=expected:raise ValueError(f"{name}: slot numbers must be contiguous 0..{len(slots)-1}")
        maps.append(SMap(mid,name,tuple(slot for _,slot in sorted(slots))))
    maps.sort(key=lambda m:m.id)
    return tuple(maps)

MAPS:Tuple[SMap,...]=()


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
    return tuple(sorted((e.tick-block.start,e.note,e.vel,e.dur) for e in block.events))

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
    """Map a present MIDI note to the three ADX hit strengths.

    No Hit is represented by the absence of a note in the quantized slot/cell.
    Every positive MIDI velocity remains a hit: 1..60 weak, 61..100 medium,
    and 101..127 strong.
    """
    if velocity <= 60:return 0,"weak hit"
    if velocity <= 100:return 1,"medium hit"
    return 2,"strong hit"

def reference_card(b,x,y,w=430,h=350,path=None):
    bars=str(b.bars[0].no) if len(b.bars)==1 else f'{b.bars[0].no}–{b.bars[-1].no}'
    p=[f'<g class="block duplicate {"bad" if b.unknown else ""}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="bg"/>']
    p += [tx(x+16,y+28,f'B{b.no:03d}  bars {bars}',"title"),tx(x+w/2,y+105,f'Pattern #{b.pattern_no:03d}',"dup-pattern","middle"),tx(x+w/2,y+139,f'Same as B{b.duplicate_of:03d}',"dup-same","middle"),tx(x+w/2,y+169,f'ID {b.smap.id} {b.smap.name} · matrix omitted',"meta","middle"),tx(x+w/2,y+192,('MISSING NOTES: '+','.join(map(str,b.unknown))) if b.unknown else '',"warning","middle"),tx(x+16,y+248,'duplicate checked within this MIDI file only',"meta"),card_controls(path,b,x,y+264,w),'</g>']
    return ''.join(p)

def ending_card(b,x,y,w=430,h=350,path=None):
    notes=', '.join(f'{e.note}({e.vel})' for e in b.events) or '(none)'
    bar=str(b.bars[0].no)
    p=[f'<g class="block ending"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="bg"/>']
    p += [tx(x+16,y+28,f'B{b.no:03d}  bar {bar}',"title"),tx(x+w/2,y+100,'ENDING HIT',"ending-title","middle"),tx(x+w/2,y+134,'excluded from pattern catalog',"dup-same","middle"),tx(x+w/2,y+166,f'notes: {notes}',"meta","middle"),tx(x+16,y+248,'single onset group at the start of the final odd bar',"meta"),card_controls(path,b,x,y+264,w,disabled=True),'</g>']
    return ''.join(p)

def card(b,x,y,w=430,h=350,path=None):
    beats=max(1.0,(b.end-b.start)/max(1,b.subdiv.get("tpq",1)))
    detected=b.subdiv.get("subdivision","unknown")
    initial_subdiv={
        "straight-16":"16","triplet-8":"8T","triplet-8T":"8T",
        "triplet-16":"16T","triplet-16T":"16T",
    }.get(detected,"16")
    subdivision_cells={"16":4,"8T":3,"16T":6}
    hh,fh,lw=58,28,96; plot_h=260; gx,gy=x+lw,y+hh; gw,gh=w-lw-8,plot_h-hh-fh
    raw=sorted({e.note for e in b.events},reverse=True) or [36]
    slots=list(range(len(b.smap.slots)-1,-1,-1)); p=[]
    p.append(f'<g class="block pattern-card {"bad" if b.unknown else ""}" data-block="{b.no}">')
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

    flam_analysis=detect_flams(b.events,b.subdiv.get("tpq",1),loop_ticks=b.end-b.start,loop_start=b.start)
    excluded_grace_ids={id(b.events[int(item["grace_index"])]) for item in flam_analysis["flams"] if item.get("remove_from_subdivision") and "grace_index" in item}
    pair_role={}; pair_delta={}; pair_confidence={}
    for item in flam_analysis["flams"]:
        grace=b.events[item["grace_index"]]; main=b.events[item["main_index"]]; delta=item["gap_ticks"]
        pair_role[id(grace)]="grace"; pair_role[id(main)]="main"
        pair_delta[id(grace)]=pair_delta[id(main)]=delta
        pair_confidence[id(grace)]=pair_confidence[id(main)]=item["confidence"]
    flam_threshold=flam_analysis["settings"].get("flam_max_gap_ticks",0)

    p.append('<g class="raw">'); rh=gh/len(raw); rmap={n:i for i,n in enumerate(raw)}
    for i,n in enumerate(raw):
        yy=gy+i*rh; p += [tx(x+8,yy+rh*.7,f'{n} {GM.get(n,"non-GM")}',"row"),f'<line x1="{gx}" y1="{yy+rh:.2f}" x2="{gx+gw}" y2="{yy+rh:.2f}" class="rguide"/>']
    grace_offset=min(10.0,max(5.0,rh*.22)); duration=max(1,b.end-b.start)
    for e in b.events:
        frac=(e.tick-b.start)/duration; cx=gx+max(0.0,min(1.0,frac))*gw
        base_cy=gy+(rmap[e.note]+.5)*rh; rr=2+2.2*e.vel/127; role=pair_role.get(id(e)); cy=base_cy-grace_offset if role=="grace" else base_cy
        classes=["hit","rawhit"]
        if e.note in b.unknown:classes.append("unknown")
        if e.vel<=GHOST_CANDIDATE_MAX_VELOCITY:classes.append("ghost")
        if role=="grace":classes.append("flamgrace")
        if role=="main":classes.append("flammain")
        labels=[]
        if e.vel<=GHOST_CANDIDATE_MAX_VELOCITY:labels.append("ghost candidate")
        if role:labels.append(f"flam candidate ({role}, {pair_confidence[id(e)]}, delta {pair_delta[id(e)]} ticks, threshold {flam_threshold})")
        extra=("; "+"; ".join(labels)) if labels else ""
        actual_duration_width=max(0.0,e.dur/duration*gw)
        duration_x2=min(gx+gw,max(cx+2.0,cx+actual_duration_width))
        p.append(f'<line x1="{cx:.2f}" y1="{cy:.2f}" x2="{duration_x2:.2f}" y2="{cy:.2f}" class="rawduration"><title>note {e.note}, note-on {e.tick}, note-off {e.tick+e.dur}, duration {e.dur} ticks</title></line>')
        p.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{rr:.2f}" class="{" ".join(classes)}"><title>note {e.note}, velocity {e.vel}, duration {e.dur}, tick {e.tick}{extra}</title></circle>')
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
            vlevel=velocity_level(e.vel); hlevel,hlabel=adx_hit_level(e.vel)
            p.append(f'<rect x="{xx+.6:.2f}" y="{yy+.6:.2f}" width="{max(.5,cell_w-1.2):.2f}" height="{max(.5,sh-1.2):.2f}" rx="1.2" class="slotcell velocity{vlevel} hitstrength{hlevel}"><title>slot {si} {b.smap.slots[si].label}; raw {e.note}; velocity {e.vel} (band {vlevel}); ADX {hlabel}; duration {e.dur}; subdivision {subdiv}</title></rect>')
        p.append('</g>')
    p.append('</g>')
    foot='click SVG: RAW ↔ SLOT' if not b.unknown else 'WARNING · nearest SLOT_MAP used · missing notes: '+','.join(map(str,b.unknown))
    p += [tx(x+10,y+251,foot,"meta"),card_controls(path,b,x,y+264,w),'</g>']; return ''.join(p)

def select_options(items, selected):
    return ''.join(
        f'<option value="{html.escape(value)}" {"selected" if value == selected else ""}>{html.escape(label)}</option>'
        for value, label in items
    )

SUBDIVISIONS = [
    ("16", "16"),
    ("8T", "8T"),
    ("16T", "16T"),
]

def card_controls(path, b, x, y, w=430, disabled=False):
    default_genre=infer_genre(path.name)
    genre_options=select_options([(code, f"{code} - {name}") for code,name in GENRES], default_genre)
    detected=b.subdiv.get("subdivision", "unknown")
    display_detected={
        "straight-16":"16",
        "triplet-8":"8T",
        "triplet-8T":"8T",
        "triplet-16":"16T",
        "triplet-16T":"16T",
    }.get(detected, "16")
    subdivision_options=select_options(SUBDIVISIONS, display_detected)
    export_checked=(not disabled and b.duplicate_of is None)
    orn_candidate=(
        any(e.vel<=GHOST_CANDIDATE_MAX_VELOCITY for e in b.events)
        or bool(detect_flams(b.events,b.subdiv.get("tpq",1),loop_ticks=b.end-b.start,loop_start=b.start)["flams"])
    )
    dis=' disabled' if disabled else ''
    checked_export=' checked' if export_checked else ''
    checked_orn=' checked' if orn_candidate and not disabled else ''
    dup=b.duplicate_of or ""
    return f'''<foreignObject x="{x+10}" y="{y}" width="{w-20}" height="78" class="pattern-controls-wrap">
<div xmlns="http://www.w3.org/1999/xhtml" class="pattern-controls" data-block="{b.no}" data-start-bar="{b.bars[0].no}" data-end-bar="{b.bars[-1].no}" data-time-sig="{html.escape("→".join(f"{bar.num}/{bar.den}" for bar in b.bars) if len({(bar.num,bar.den) for bar in b.bars}) > 1 else f"{b.bars[0].num}/{b.bars[0].den}")}" data-slot-map="{html.escape(b.smap.name)}" data-duplicate-of="{dup}">
<label><input class="export-check" type="checkbox"{checked_export}{dis}/> Export</label>
<label>Genre <select class="genre-select"{dis}>{genre_options}</select></label>
<label><input class="orn-check" type="checkbox"{checked_orn}{dis}/> ORN</label>
<label>Subdivision <select class="subdivision-select" title="analysis confidence {html.escape(str(b.subdiv.get("confidence", "")))}"{dis}>{subdivision_options}</select></label>
<label class="number-label">No. <input class="start-number" type="text" inputmode="numeric" maxlength="4" placeholder="start" aria-label="Starting pattern number"{dis}/><output class="name-preview" aria-live="polite"></output></label>
</div></foreignObject>'''

def render(path,mid,bars_,bb,skipped_leading_bars=0):
    cw,ch,gx,gy,mar,ncol=430,350,18,18,18,3; nrow=max(1,math.ceil(len(bb)/ncol)); sw=mar*2+ncol*cw+(ncol-1)*gx; sh=mar*2+nrow*ch+(nrow-1)*gy
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
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(path.name)} — ADC PatternLab</title><style>
:root{{--bg:#f4f6f8;--panel:#fff;--ink:#17202a;--muted:#65717e;--line:#d9dee4;--major:#9aa6b2;--raw:#1f6feb;--slot:#8a3ffc;--warn:#c2410c;--v0:#dbeafe;--v1:#93c5fd;--v2:#3b82f6;--v3:#1e3a8a;--h0:#fecaca;--h1:#ef4444;--h2:#991b1b}}@media(prefers-color-scheme:dark){{:root{{--bg:#11151a;--panel:#1a2027;--ink:#e6edf3;--muted:#9da9b5;--line:#303843;--major:#66717d;--raw:#58a6ff;--slot:#c297ff;--warn:#ff9b6a;--v0:#23395d;--v1:#2f6fab;--v2:#58a6ff;--v3:#b6d8ff;--h0:#5f2525;--h1:#c24141;--h2:#ff8a8a}}}}*{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--ink)}}header{{position:relative;z-index:3;padding:14px 18px 12px;background:var(--panel);border-bottom:1px solid var(--line)}}h1{{margin:0 0 6px;font-size:20px}}.summary{{font-size:13px;color:var(--muted)}}button{{margin-top:8px;padding:7px 11px;border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--ink);font-weight:700;cursor:pointer}}.legend{{margin-left:14px;font-size:12px;color:var(--muted)}}.lg{{display:inline-block;width:12px;height:12px;margin:0 3px 0 7px;vertical-align:-2px;border:1px solid var(--line)}}.v0{{background:var(--v0)}}.v1{{background:var(--v1)}}.v2{{background:var(--v2)}}.v3{{background:var(--v3)}}.h0{{background:var(--h0)}}.h1{{background:var(--h1)}}.h2{{background:var(--h2)}}main{{overflow:auto;padding:12px}}svg{{display:block;cursor:pointer;user-select:none}}.bg{{fill:var(--panel);stroke:var(--line)}}.bad .bg{{stroke:var(--warn);stroke-width:2}}.title{{fill:var(--ink);font-size:13px;font-weight:750}}.meta{{fill:var(--muted);font-size:10px}}.sid{{fill:var(--slot);font-size:12px;font-weight:800}}.warning{{fill:var(--warn);font-size:10px;font-weight:800}}.row{{fill:var(--ink);font-size:8.5px}}.guide,.rguide{{stroke:var(--line);stroke-width:.7}}.major{{stroke:var(--major);stroke-width:1.45}}.barline{{stroke:var(--ink);stroke-width:2.1;opacity:.72}}.hit{{opacity:1}}.rawduration{{stroke:var(--raw);stroke-width:1.4;stroke-linecap:round;opacity:.62}}.rawhit{{fill:var(--raw);stroke:var(--panel);stroke-width:.8}}.ghost{{stroke:var(--ink);stroke-width:1;stroke-dasharray:2 1}}.flamgrace{{fill:var(--panel);stroke:var(--raw);stroke-width:1.5;stroke-dasharray:none;opacity:1}}.flammain{{stroke:var(--raw);stroke-width:.6}}.slothit{{fill:var(--slot)}}.slotcell{{stroke:var(--panel);stroke-width:.35}}.velocity0{{fill:var(--v0)}}.velocity1{{fill:var(--v1)}}.velocity2{{fill:var(--v2)}}.velocity3{{fill:var(--v3)}}svg.accentmode .slotcell.hitstrength0{{fill:var(--h0)}}svg.accentmode .slotcell.hitstrength1{{fill:var(--h1)}}svg.accentmode .slotcell.hitstrength2{{fill:var(--h2)}}.unknown{{fill:var(--warn);stroke:var(--panel)}}.subdiv-layer{{display:none}}.subdiv-layer.active{{display:inline}}.slot{{display:none}}svg.slotmode .raw{{display:none}}svg.slotmode .slot{{display:inline}}details{{margin:0 18px 18px;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}}.pattern-controls-wrap{{overflow:visible}}.pattern-controls{{height:76px;display:grid;grid-template-columns:70px 1fr 52px 1.15fr;align-items:center;gap:5px 7px;padding:6px 8px;border-top:1px solid var(--line);font:11px system-ui,sans-serif;color:var(--ink);background:var(--panel)}}.pattern-controls label{{display:flex;align-items:center;gap:3px;white-space:nowrap;min-width:0}}.pattern-controls select,.pattern-controls input[type=text]{{min-width:0;width:100%;padding:3px 4px;border:1px solid var(--line);border-radius:5px;background:var(--panel);color:var(--ink);font-size:10.5px}}.pattern-controls .number-label{{grid-column:2 / 5}}.pattern-controls .start-number{{max-width:62px}}.pattern-controls .name-preview{{min-width:92px;font-weight:800;color:var(--slot)}}.pattern-controls .invalid{{border-color:var(--warn)!important;outline:1px solid var(--warn)}}#number-status{{display:inline-block;margin-left:10px;font-size:12px;color:var(--muted)}}#number-status.error{{color:var(--warn);font-weight:700}}input[type=checkbox]{{width:16px;height:16px}}
</style></head><body><header><h1>{html.escape(path.name)} — ADC PatternLab <small style="font-size:12px;color:var(--muted)">{VERSION}</small></h1><div class="summary">{header_summary}</div><button id="toggle">Toggle RAW / SLOT</button> <button id="slot-display" type="button">SLOT: Velocity</button> <button id="download-csv" type="button">Download CSV</button><span id="number-status"></span> <strong id="mode">RAW GM NOTES</strong><div class="legend">Velocity: <i class="lg v0"></i>0 (1–31) <i class="lg v1"></i>1 (32–63) <i class="lg v2"></i>2 (64–95) <i class="lg v3"></i>3 (96–127)</div><div class="legend">ADX Accent: <i class="lg h0"></i>Weak Hit (1–60) <i class="lg h1"></i>Medium Hit (61–100) <i class="lg h2"></i>Strong Hit (101–127)</div><div class="legend">RAW: ○ flam grace · dashed ring ghost candidate (velocity ≤ 30)</div></header><main><svg id="matrix" xmlns="http://www.w3.org/2000/svg" width="{sw}" height="{sh}" viewBox="0 0 {sw} {sh}">{''.join(body)}</svg></main><details><summary>Analysis notes</summary><p>Each block is checked only against earlier blocks in the same MIDI file. Exact identity uses relative tick, raw note, velocity, and note duration. A repeated block keeps its original Pattern number and omits the matrix drawing.</p><p>A final odd bar containing only one onset group at its beginning is labeled ENDING HIT and excluded from the pattern catalog.</p><p>Each card initially uses the automatically detected subdivision. Its own Subdivision selector can immediately switch the reference grid and SLOT quantization among 16, 8T, and 16T without affecting other cards. Reloading the HTML restores the original automatic selections.</p><p>If no SLOT_MAP covers every note, the nearest map is used, the card receives a red border, and uncovered MIDI notes are listed as MISSING NOTES. Ties fall back conservatively toward lower IDs, beginning with LEGACY 12.</p><p>RAW view places every note-on circle at its original MIDI tick position and extends a horizontal line to the recorded note-off position. Very short durations receive a two-pixel minimum display line; the note-on position itself is never moved. The vertical subdivision lines are reference overlays only; changing a card’s Subdivision selector never moves RAW notes. Velocity controls circle size. Velocity ≤ 30 is marked as a ghost candidate with a dashed ring. Conservative flam grace notes remain hollow circles slightly above their raw-note row, while their true horizontal timing relative to the main hit is preserved. Tooltips retain the true tick delta.</p><p>In SLOT view, each retained hit fills its complete quantized cell. The SLOT display button switches between the original four-band MIDI Velocity view and the ADX Accent preview. ADX Accent shows three hit strengths: Weak Hit (velocity 1–60), Medium Hit (61–100), and Strong Hit (101–127). An empty cell already represents no hit, and every positive velocity remains visible as one of the three hit strengths. Flam grace notes marked for removal from subdivision are intentionally omitted there and belong to ORN; the main hit remains in the grid. Ghost candidates that are not classified as removable flam grace notes remain visible. When multiple retained raw hits collapse into one slot/cell, the strongest velocity is shown.</p><p>SLOT_MAP usage: <code>{html.escape(json.dumps(summary,ensure_ascii=False))}</code></p><p>The shared adc_rhythm_analysis module owns the complete subdivision decision: flam detection, grace-note exclusion, onset phase, note-duration evidence, and conservative filename hints. The same flam-filtered events are used for both phase and duration scoring. Beat anchors and the shared half-beat remain excluded from phase evidence.</p></details><script>(()=>{{
const s=document.getElementById('matrix'),m=document.getElementById('mode'),slotDisplay=document.getElementById('slot-display');
function t(){{const v=s.classList.toggle('slotmode');m.textContent=v?(s.classList.contains('accentmode')?'2-BAR SLOT_MAP · ADX ACCENT':'2-BAR SLOT_MAP · VELOCITY'):'RAW GM NOTES'}}
function toggleSlotDisplay(){{const accent=s.classList.toggle('accentmode');slotDisplay.textContent=accent?'SLOT: ADX Accent':'SLOT: Velocity';if(s.classList.contains('slotmode'))m.textContent=accent?'2-BAR SLOT_MAP · ADX ACCENT':'2-BAR SLOT_MAP · VELOCITY';}}
s.addEventListener('click',e=>{{if(!e.target.closest('.pattern-controls'))t()}});document.getElementById('toggle').addEventListener('click',t);slotDisplay.addEventListener('click',toggleSlotDisplay);
function csvCell(value){{const x=String(value??'');return /[",\\n]/.test(x)?'"'+x.replace(/"/g,'""')+'"':x}}
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
function applySubdivision(panel){{
  const select=panel.querySelector('.subdivision-select');
  if(!select || select.disabled)return;
  const card=document.querySelector(`g.pattern-card[data-block="${{panel.dataset.block}}"]`);
  if(!card)return;
  const selected=select.value;
  card.querySelectorAll('.subdiv-layer').forEach(layer=>{{layer.classList.toggle('active',layer.dataset.subdiv===selected);}});
  const summary=card.querySelector('.grid-summary');
  if(summary){{const cells={{'16':4,'8T':3,'16T':6}}[selected]||4;summary.textContent=(summary.dataset.prefix||'')+cells+' cells/beat';}}
}}
allPanels().forEach(panel=>{{
  const input=panel.querySelector('.start-number');
  input.addEventListener('input',()=>{{delete input.dataset.auto;calculateNames(false)}});
  panel.querySelector('.genre-select').addEventListener('change',()=>calculateNames(false));
  panel.querySelector('.export-check').addEventListener('change',()=>calculateNames(false));
  const subdivision=panel.querySelector('.subdivision-select');
  if(subdivision){{subdivision.addEventListener('change',()=>{{applySubdivision(panel);calculateNames(false)}});applySubdivision(panel);}}
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
    p=argparse.ArgumentParser(prog=SCRIPT_NAME,description="Generate an interactive HTML/SVG drum pattern catalog from one MIDI file."); p.add_argument("input_midi",type=Path); p.add_argument("-o","--output",type=Path); p.add_argument("--slot-maps",type=Path,help="Canonical slot_map_definitions.json (default: beside this script)"); p.add_argument("--skip-leading-empty-bars",action="store_true",help="omit leading bars without CH10 note-on events while preserving absolute bar numbers"); p.add_argument("--version",action="version",version=VERSION_TEXT); a=p.parse_args(argv)
    if not a.input_midi.is_file():print(f'[ERROR] not found: {a.input_midi}',file=sys.stderr);return 2
    slot_map_path=a.slot_maps or Path(__file__).with_name("slot_map_definitions.json")
    global MAPS
    try:MAPS=load_slot_maps(slot_map_path)
    except ValueError as e:print(f'[ERROR] {e}',file=sys.stderr);return 2
    try:mid=MidiFile(str(a.input_midi))
    except Exception as e:print(f'[ERROR] cannot read MIDI: {e}',file=sys.stderr);return 2
    ev,ts,mx=collect(mid); all_bars=make_bars(mid.ticks_per_beat,ts,mx); bars_=all_bars; skipped=0
    if a.skip_leading_empty_bars:
        bars_,skipped=skip_leading_empty_bars(all_bars,ev)
    bb=blocks(bars_,ev,mid.ticks_per_beat,a.input_midi.name); out=a.output or a.input_midi.with_name(a.input_midi.stem+'_patternlab.html'); out.write_text(render(a.input_midi,mid,bars_,bb,skipped),encoding='utf-8'); print(VERSION_TEXT); print(f'[OK] {out}'); print(f'[OK] bars={len(bars_)}, blocks={len(bb)}, drum_note_on={len(ev)}, skipped_leading_empty_bars={skipped}'); return 0
if __name__=='__main__':raise SystemExit(main())
