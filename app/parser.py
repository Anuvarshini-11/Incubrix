from __future__ import annotations
import re
from .engine import cut,set_speed,add_caption,add_bleep,reorder_segment,remove_filler_words,EditError,AmbiguousEdit,duration

def parse_time(s:str)->float:
    s=s.strip().lower()
    if s.startswith("the first "):
        s=s[10:]
    m=re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?",s)
    if m:
        a,b,c=m.groups(); return float(int(a)*60+int(b)) if c is None else float(int(a)*3600+int(b)*60+int(c))
    m=re.fullmatch(r"(?:(\d+)\s*(?:minutes?|mins?|m)\s*)?(?:(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)?)?",s)
    if m and (m.group(1) or m.group(2)): return float(m.group(1) or 0)*60+float(m.group(2) or 0)
    try:return float(s)
    except ValueError as e: raise EditError(f"could not understand time {s!r}") from e

def execute_instruction(history,instruction:str,occurrence:int|None=None)->dict:
    t=history.current; text=instruction.strip(); low=text.lower()
    if low in {"undo","undo."}: return {"op":"undo","summary":"Undid the most recent edit." ,"timeline":history.undo()}
    if low in {"redo","redo."}: return {"op":"redo","summary":"Redid the most recently undone edit.","timeline":history.redo()}
    try:
        m=re.search(r"cut\s+(?:the\s+)?first\s+(.+)$",low)
        if m:
            op=history.apply(cut,0,parse_time(m.group(1))); return {"op":op,"summary":f"Removed the first {op['current_end_s']:g} seconds; duration is now {op['duration_after_s']:.2f}s.","timeline":history.current}
        m=re.search(r"cut\s+(?:from\s+)?(.+?)\s+(?:to|through)\s+(.+)$",low)
        if m:
            a,b=parse_time(m.group(1)),parse_time(m.group(2)); op=history.apply(cut,a,b); return {"op":op,"summary":f"Cut current timeline time {a:g}–{b:g}s.","timeline":history.current}
        m=re.search(r"(?:speed up|make)\s+(?:the\s+)?(?:section\s+)?(?:from\s+)?(.+?)\s+(?:to|through)\s+(.+?)\s+by\s+(\d+(?:\.\d+)?)\s*x",low)
        if m:
            a,b,s=parse_time(m.group(1)),parse_time(m.group(2)),float(m.group(3)); op=history.apply(set_speed,a,b,s); return {"op":op,"summary":f"Set current timeline {a:g}–{b:g}s to {s:g}x playback.","timeline":history.current}
        if re.fullmatch(r"(?:remove|cut)\s+(?:the\s+)?filler\s+words",low):
            op=history.apply(remove_filler_words); return {"op":op,"summary":f"Removed {len(op['words'])} filler words from the timeline.","timeline":history.current}
        m=re.fullmatch(r"bleep\s+(?:from\s+)?(.+?)\s+(?:to|through)\s+(.+)",low)
        if m:
            start,end=parse_time(m.group(1)),parse_time(m.group(2));op=history.apply(add_bleep,start,end)
            return {"op":op,"summary":f"Added an audio bleep from {start:g} to {end:g} seconds.","timeline":history.current}
        m=re.fullmatch(r"move\s+clip\s+(\d+)\s+to\s+(?:position\s+)?(\d+)",low)
        if m:
            source,target=map(int,m.groups());op=history.apply(reorder_segment,source,target)
            return {"op":op,"summary":f"Moved clip {source} to position {target}.","timeline":history.current}
        m=re.search(r"add\s+(?:a\s+)?caption\s+['\"“”](.+?)['\"“”]\s+when\s+i\s+say\s+['\"“”]?(.+?)['\"“”]?$",text,re.I)
        if m:
            op=history.apply(add_caption,m.group(1),m.group(2),occurrence); return {"op":op,"summary":f"Added caption {m.group(1)!r} anchored to the matching spoken words.","timeline":history.current}
        return {"rejected":"I couldn't map that instruction to a supported operation; no changes were made."}
    except AmbiguousEdit as exc:
        return {"clarification":True,"question":"The anchor phrase occurs more than once. Which occurrence should I use?","options":[f"Occurrence {i+1}" for i,_ in enumerate(exc.matches)],"summary":"No edit applied; please choose a match."}
    except (EditError,ValueError) as exc:
        return {"rejected":str(exc)}
