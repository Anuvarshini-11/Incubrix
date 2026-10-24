from __future__ import annotations
import hashlib, json, math, re, uuid
from copy import deepcopy
from .models import Timeline, Segment, Caption, Anchor, Bleep

class EditError(ValueError):
    pass

def canonical_json(timeline: Timeline) -> str:
    data = timeline.model_dump(mode="json", exclude={"warnings"})
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

def timeline_hash(timeline: Timeline) -> str:
    return hashlib.sha256(canonical_json(timeline).encode()).hexdigest()

def duration(timeline: Timeline) -> float:
    return sum((s.source_end_s-s.source_start_s)/s.speed for s in timeline.segments)

def validate(t: Timeline) -> None:
    if not t.segments:
        raise EditError("timeline must contain at least one segment")
    ids = set()
    previous_start = -1.0
    for w in t.transcript:
        if w.id in ids: raise EditError(f"duplicate word id: {w.id}")
        ids.add(w.id)
        if not (math.isfinite(w.start_s) and math.isfinite(w.end_s) and w.start_s < w.end_s <= t.source.duration_s):
            raise EditError(f"invalid transcript interval: {w.id}")
        if w.start_s < previous_start:
            raise EditError("transcript words must be ordered by start time")
        previous_start = w.start_s
    for s in t.segments:
        if not (math.isfinite(s.source_start_s) and math.isfinite(s.source_end_s) and 0 <= s.source_start_s < s.source_end_s <= t.source.duration_s):
            raise EditError(f"invalid segment interval: {s.id}")
        if not math.isfinite(s.speed) or not .25 <= s.speed <= 4: raise EditError(f"unsupported speed: {s.speed}")
    for c in t.captions:
        if c.anchor.word_ids and not set(c.anchor.word_ids).issubset(ids): raise EditError(f"caption {c.id} references unknown words")
        if not c.text.strip(): raise EditError(f"caption {c.id} cannot be empty")
        if c.anchor.source_start_s is not None and (
            not math.isfinite(c.anchor.source_start_s)
            or not 0 <= c.anchor.source_start_s < t.source.duration_s
        ): raise EditError(f"caption {c.id} has an invalid start time")
        if c.anchor.source_end_s is not None and (
            not math.isfinite(c.anchor.source_end_s)
            or not 0 < c.anchor.source_end_s <= t.source.duration_s
        ): raise EditError(f"caption {c.id} has an invalid end time")
        if (
            c.anchor.source_start_s is not None
            and c.anchor.source_end_s is not None
            and c.anchor.source_start_s >= c.anchor.source_end_s
        ): raise EditError(f"caption {c.id} must end after it starts")
    bleep_ids=set()
    for bleep in t.bleeps:
        if bleep.id in bleep_ids: raise EditError(f"duplicate bleep id: {bleep.id}")
        bleep_ids.add(bleep.id)
        if not (math.isfinite(bleep.source_start_s) and math.isfinite(bleep.source_end_s) and 0<=bleep.source_start_s<bleep.source_end_s<=t.source.duration_s):
            raise EditError(f"invalid bleep interval: {bleep.id}")
        if not t.source.has_audio: raise EditError("bleeps require a source audio track")

def _split_at(t: Timeline | list[Segment], point: float) -> list[Segment]:
    out=[]; cursor=0.0
    for seg in (t.segments if isinstance(t, Timeline) else t):
        outdur=(seg.source_end_s-seg.source_start_s)/seg.speed
        nextc=cursor+outdur
        if point <= cursor or point >= nextc:
            out.append(seg)
        else:
            src=seg.source_start_s+(point-cursor)*seg.speed
            out.extend([Segment(id=seg.id+"a",source_start_s=seg.source_start_s,source_end_s=src,speed=seg.speed),Segment(id=seg.id+"b",source_start_s=src,source_end_s=seg.source_end_s,speed=seg.speed)])
        cursor=nextc
    return out

def _map_range(t: Timeline, start: float, end: float) -> tuple[float,float]:
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration(t)+1e-6):
        raise EditError(f"range must be within current timeline duration ({duration(t):.3f}s)")
    segments=_split_at(_split_at(t,start),end); selected=[]; cursor=0
    for seg in segments:
        d=(seg.source_end_s-seg.source_start_s)/seg.speed
        mid=cursor+d/2
        if start <= mid < end: selected.append(seg)
        cursor+=d
    return selected[0].source_start_s, selected[-1].source_end_s

def cut(t: Timeline, start: float, end: float) -> dict:
    total=duration(t)
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= total):
        raise EditError(f"range must be within current timeline duration ({total:.3f}s)")
    if start == 0 and end >= total: raise EditError("cutting the whole timeline is not allowed")
    before=duration(t); left=_split_at(_split_at(t,start),end); cursor=0; kept=[]
    for s in left:
        d=(s.source_end_s-s.source_start_s)/s.speed; mid=cursor+d/2
        if not start <= mid < end: kept.append(s)
        cursor+=d
    if not kept: raise EditError("cutting the whole timeline is not allowed")
    t.segments=kept; t.revision+=1
    return {"type":"cut","current_start_s":start,"current_end_s":end,"duration_before_s":before,"duration_after_s":duration(t)}

def set_speed(t: Timeline,start:float,end:float,speed:float)->dict:
    if not math.isfinite(speed) or not .25<=speed<=4: raise EditError("speed must be between 0.25x and 4x")
    _map_range(t,start,end); before=duration(t); parts=_split_at(_split_at(t,start),end); cursor=0; out=[]
    for s in parts:
        d=(s.source_end_s-s.source_start_s)/s.speed; mid=cursor+d/2
        out.append(Segment(id=s.id,source_start_s=s.source_start_s,source_end_s=s.source_end_s,speed=speed if start<=mid<end else s.speed)); cursor+=d
    t.segments=out; t.revision+=1
    return {"type":"speed","current_start_s":start,"current_end_s":end,"speed":speed,"duration_before_s":before,"duration_after_s":duration(t)}

def reorder_segment(t:Timeline,source_position:int,target_position:int)->dict:
    count=len(t.segments)
    if not 1<=source_position<=count or not 1<=target_position<=count:
        raise EditError(f"clip positions must be between 1 and {count}")
    segment=t.segments.pop(source_position-1)
    t.segments.insert(target_position-1,segment)
    t.revision+=1
    return {"type":"reorder","from_position":source_position,"to_position":target_position,"segment_id":segment.id}

def remove_filler_words(t:Timeline)->dict:
    fillers={"um","uh","erm","er","ah","hmm"}
    matches=[word for word in t.transcript if re.sub(r"[^\w]","",word.text.lower()) in fillers]
    ranges=[]
    cursor=0.0
    for segment in t.segments:
        for word in matches:
            start=max(segment.source_start_s,word.start_s)
            end=min(segment.source_end_s,word.end_s)
            if start<end:
                ranges.append((cursor+(start-segment.source_start_s)/segment.speed,cursor+(end-segment.source_start_s)/segment.speed))
        cursor+=(segment.source_end_s-segment.source_start_s)/segment.speed
    if not ranges: raise EditError("No supported filler words (um, uh, erm, er, ah, hmm) were found in the transcript.")
    merged=[]
    for start,end in sorted(ranges):
        start=max(0,start-.04);end=min(duration(t),end+.04)
        if merged and start<=merged[-1][1]:
            merged[-1]=(merged[-1][0],max(merged[-1][1],end))
        else: merged.append((start,end))
    before=duration(t)
    for start,end in reversed(merged):
        cut(t,start,end)
    return {"type":"remove_fillers","words":[word.text for word in matches],"removed_intervals":len(merged),"duration_before_s":before,"duration_after_s":duration(t)}

def add_bleep(t:Timeline,start:float,end:float)->dict:
    if not t.source.has_audio or t.mute_audio:
        raise EditError("Unmute a video with an audio track before adding a bleep.")
    if not (math.isfinite(start) and math.isfinite(end) and 0<=start<end<=duration(t)):
        raise EditError("Bleep range must be within the current timeline duration.")
    cursor=0.0; intervals=[]
    for segment in t.segments:
        length=(segment.source_end_s-segment.source_start_s)/segment.speed
        overlap_start=max(start,cursor);overlap_end=min(end,cursor+length)
        if overlap_start<overlap_end:
            source_start=segment.source_start_s+(overlap_start-cursor)*segment.speed
            source_end=segment.source_start_s+(overlap_end-cursor)*segment.speed
            intervals.append((source_start,source_end))
        cursor+=length
    if not intervals: raise EditError("Bleep range does not overlap a retained clip.")
    for source_start,source_end in intervals:
        t.bleeps.append(Bleep(id=f"bleep_{uuid.uuid4().hex}",source_start_s=source_start,source_end_s=source_end))
    t.revision+=1
    return {"type":"bleep","source_intervals_s":[list(interval) for interval in intervals]}

def remove_silence(t:Timeline,source_intervals:list[tuple[float,float]],minimum_silence_s:float=0.7)->dict:
    ranges=[];cursor=0.0
    for segment in t.segments:
        for silence_start,silence_end in source_intervals:
            start=max(segment.source_start_s,silence_start)
            end=min(segment.source_end_s,silence_end)
            if end-start>=minimum_silence_s:
                output_start=cursor+(start-segment.source_start_s)/segment.speed
                output_end=cursor+(end-segment.source_start_s)/segment.speed
                ranges.append((output_start,output_end))
        cursor+=(segment.source_end_s-segment.source_start_s)/segment.speed
    if not ranges: raise EditError("No detected silence overlaps the current timeline.")
    merged=[]
    for start,end in sorted(ranges):
        start=max(0,start-.08);end=min(duration(t),end+.08)
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],end))
        else:merged.append((start,end))
    before=duration(t)
    for start,end in reversed(merged):cut(t,start,end)
    return {"type":"remove_silence","removed_intervals":len(merged),"duration_before_s":before,"duration_after_s":duration(t)}

def bleep_output_intervals(t:Timeline)->list[tuple[float,float]]:
    intervals=[]
    for bleep in t.bleeps:
        cursor=0.0
        for segment in t.segments:
            start=max(segment.source_start_s,bleep.source_start_s)
            end=min(segment.source_end_s,bleep.source_end_s)
            if start<end:
                intervals.append((cursor+(start-segment.source_start_s)/segment.speed,cursor+(end-segment.source_start_s)/segment.speed))
            cursor+=(segment.source_end_s-segment.source_start_s)/segment.speed
    return intervals

def set_background_music(t:Timeline,asset_id:str)->dict:
    if not asset_id: raise EditError("music asset id is required")
    t.background_music_id=asset_id
    t.revision+=1
    return {"type":"set_background_music","asset_id":asset_id,"ducking":t.duck_music}

def add_caption(t:Timeline,text:str,phrase:str,occurrence:int|None=None)->dict:
    phrase=phrase.strip()
    if not phrase: raise EditError("caption anchor phrase cannot be empty")
    words=t.transcript; matches=[]
    norm=lambda x: re.sub(r"[^\w']","",x.lower())
    target=[norm(x) for x in phrase.split()]
    tokens=[norm(w.text) for w in words]
    for i in range(max(0,len(tokens)-len(target)+1)):
        candidate=words[i:i+len(target)]
        if tokens[i:i+len(target)]==target and all(
            candidate[j+1].start_s-candidate[j].end_s <= 1.5
            for j in range(len(candidate)-1)
        ):
            matches.append(candidate)
    if not matches: raise EditError("anchor phrase was not found in the transcript")
    if len(matches)>1 and occurrence is None: raise AmbiguousEdit(matches)
    if occurrence is not None and not 1 <= occurrence <= len(matches):
        raise EditError(f"occurrence must be between 1 and {len(matches)}")
    match=matches[(occurrence or 1)-1]; ids=[w.id for w in match]
    next_id=hashlib.sha256((t.timeline_id+"|"+str(t.revision+1)+"|"+text+"|"+"|".join(ids)).encode()).hexdigest()[:12]
    c=Caption(id="cap_"+next_id,text=text,anchor=Anchor(word_ids=ids,source_start_s=match[0].start_s,source_end_s=match[-1].end_s))
    t.captions.append(c); t.revision+=1
    return {"type":"add_caption","caption_id":c.id,"text":text,"anchor_word_ids":ids,"source_interval_s":[match[0].start_s,match[-1].end_s]}

def replace_transcript(t:Timeline, words:list)->dict:
    t.transcript=deepcopy(words)
    t.revision+=1
    return {"type":"replace_transcript","word_count":len(words)}

class AmbiguousEdit(EditError):
    def __init__(self,matches): self.matches=matches; super().__init__("multiple transcript matches")

def caption_output_interval(t:Timeline,c:Caption)->tuple[float,float]|None:
    if not c.anchor.word_ids:
        if c.anchor.source_start_s is None or c.anchor.source_end_s is None:return None
        cursor=0.0; starts=[]; ends=[]
        for seg in t.segments:
            a=max(seg.source_start_s,c.anchor.source_start_s)
            b=min(seg.source_end_s,c.anchor.source_end_s)
            if a < b:
                starts.append(cursor+(a-seg.source_start_s)/seg.speed)
                ends.append(cursor+(b-seg.source_start_s)/seg.speed)
            cursor+=(seg.source_end_s-seg.source_start_s)/seg.speed
        if not starts:return None
        return round(min(starts),9),round(max(ends),9)
    wanted=set(c.anchor.word_ids); cursor=0.0; starts=[]; ends=[]
    byid={w.id:w for w in t.transcript}
    for seg in t.segments:
        for wid in wanted:
            w=byid.get(wid)
            if w and max(seg.source_start_s,w.start_s)<min(seg.source_end_s,w.end_s):
                a=max(seg.source_start_s,w.start_s); b=min(seg.source_end_s,w.end_s)
                starts.append(cursor+(a-seg.source_start_s)/seg.speed); ends.append(cursor+(b-seg.source_start_s)/seg.speed)
        cursor+=(seg.source_end_s-seg.source_start_s)/seg.speed
    if not starts:return None
    return round(min(starts),9),round(max(ends),9)

class History:
    def __init__(self,t:Timeline): self.current=t; self.undo_stack=[]; self.redo_stack=[]
    def apply(self,fn,*args,**kwargs):
        candidate=deepcopy(self.current); result=fn(candidate,*args,**kwargs); validate(candidate)
        self.undo_stack.append(deepcopy(self.current)); self.current=candidate; self.redo_stack.clear(); return result
    def undo(self):
        if not self.undo_stack: raise EditError("nothing to undo")
        self.redo_stack.append(deepcopy(self.current)); self.current=self.undo_stack.pop(); return self.current
    def redo(self):
        if not self.redo_stack: raise EditError("nothing to redo")
        self.undo_stack.append(deepcopy(self.current)); self.current=self.redo_stack.pop(); return self.current
