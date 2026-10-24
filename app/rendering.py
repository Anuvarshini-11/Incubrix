from __future__ import annotations
import json, shutil, subprocess, tempfile, os, re
from pathlib import Path
from .models import Timeline
from .engine import duration, caption_output_interval, bleep_output_intervals
class RenderError(RuntimeError): pass

def _atempo_chain(speed: float) -> str:
    filters=[]
    remaining=speed
    while remaining < .5:
        filters.append("atempo=0.5"); remaining /= .5
    while remaining > 2:
        filters.append("atempo=2"); remaining /= 2
    filters.append(f"atempo={remaining:.6g}")
    return ",".join(filters)

def _ass_time(seconds:float)->str:
    centiseconds=max(0,round(seconds*100)); hours,centiseconds=divmod(centiseconds,3600*100)
    minutes,centiseconds=divmod(centiseconds,60*100); whole,centiseconds=divmod(centiseconds,100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{centiseconds:02d}"

def _ass_color(value:str)->str:
    named={"white":"FFFFFF","black":"000000","yellow":"00FFFF","red":"0000FF","green":"00FF00","blue":"FF0000"}
    color=named.get(value.lower(),value.lstrip("#").upper())
    if len(color)!=6 or any(char not in "0123456789ABCDEF" for char in color): color="FFFFFF"
    return f"&H00{color[4:6]}{color[2:4]}{color[0:2]}"

def _ass_escape(text:str)->str:
    return text.replace("\\","\\\\").replace("{","\\{").replace("}","\\}").replace("\n","\\N")

def caption_ass(timeline:Timeline)->str:
    width=timeline.source.width or 1280; height=timeline.source.height or 720
    rows=["[Script Info]","ScriptType: v4.00+",f"PlayResX: {width}",f"PlayResY: {height}","WrapStyle: 2","ScaledBorderAndShadow: yes","","[V4+ Styles]","Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding","Style: Default,Arial,36,&H00FFFFFF,&H000000FF,&H80000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,32,32,30,1","","[Events]","Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    align={"bottom":2,"center":5,"top":8}
    for caption in timeline.captions:
        interval=caption_output_interval(timeline,caption)
        if interval is None or interval[1]<=interval[0]: continue
        style=caption.style
        override=f"{{\\an{align[style.position]}\\fs{style.size}\\c{_ass_color(style.color)}}}"
        rows.append(f"Dialogue: 0,{_ass_time(interval[0])},{_ass_time(interval[1])},Default,,0,0,0,,{override}{_ass_escape(caption.text)}")
    return "\n".join(rows)+"\n"

def _escape_filter_path(path:Path)->str:
    value=path.resolve().as_posix()
    for char in ("\\",":","'",",","[","]",";"):
        value=value.replace(char,"\\"+char)
    return value

def _audio_mix_args(timeline:Timeline,music_path:str|None)->list[str]:
    source_audio=timeline.source.has_audio and not timeline.mute_audio
    intervals=bleep_output_intervals(timeline) if source_audio else []
    if not source_audio and not music_path:return []
    if source_audio and not music_path and not intervals:return []
    inputs=[];filters=[]
    music_index=None
    if music_path:
        music_index=1;inputs.extend(["-stream_loop","-1","-i",music_path])
    if intervals:
        tone_index=2 if music_path else 1
        inputs.extend(["-f","lavfi","-i",f"sine=frequency=1000:sample_rate=48000:duration={duration(timeline):.9g}"])
        condition="+".join(f"between(t,{start:.9g},{end:.9g})" for start,end in intervals)
        filters.extend([
            f"[0:a]volume='if({condition},0,1)'[source]",
            f"[{tone_index}:a]volume='if({condition},0.35,0)'[tone]",
            "[source][tone]amix=inputs=2:duration=first:normalize=0[processed]",
        ])
    elif source_audio:
        filters.append("[0:a]anull[processed]")
    if music_index is not None:
        filters.append(f"[{music_index}:a]volume={timeline.music_gain:.6g}[bed]")
        if source_audio and timeline.duck_music:
            filters.append("[bed][processed]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[ducked]")
            filters.append("[processed][ducked]amix=inputs=2:duration=first:normalize=0[mixed]")
        elif source_audio:
            filters.append("[processed][bed]amix=inputs=2:duration=first:normalize=0[mixed]")
        else:
            filters.append("[bed]anull[mixed]")
    else:
        filters.append("[processed]anull[mixed]")
    return [*inputs,"-filter_complex",";".join(filters),"-map","[mixed]"]

def render(timeline:Timeline,source_path:str,output_path:str,music_path:str|None=None)->dict:
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg: raise RenderError("FFmpeg is not installed; rendering is unavailable in this environment.")
    out=Path(output_path); out.parent.mkdir(parents=True,exist_ok=True)
    # Render into a unique staging directory so concurrent jobs cannot collide and failed renders
    # never truncate an existing successful output.
    try:
        with tempfile.TemporaryDirectory(prefix=f"{out.stem}_",dir=out.parent) as work:
            temp=Path(work); parts=[]
            for i,seg in enumerate(timeline.segments):
                part=temp/f"part_{i:04d}.mp4"; pts=1/seg.speed
                frame_rate=timeline.source.frame_rate if re.fullmatch(r"\d+(?:/\d+)?",timeline.source.frame_rate) and timeline.source.frame_rate!="0/0" else "30"
                # Apply the constant frame-rate conversion first; fps after setpts would
                # normalize timestamps back to source duration and silently cancel speed.
                source_length=seg.source_end_s-seg.source_start_s
                vf=f"trim=duration={source_length:.12g},setpts=PTS-STARTPTS,fps={frame_rate},setpts={pts:.12g}*PTS"
                cmd=[ffmpeg,"-y","-ss",str(seg.source_start_s),"-i",source_path,"-map","0:v:0"]
                if timeline.source.has_audio and not timeline.mute_audio: cmd.extend(["-map","0:a:0?"])
                cmd.extend(["-vf",vf])
                if timeline.source.has_audio and not timeline.mute_audio:
                    cmd.extend(["-af",f"atrim=duration={source_length:.12g},asetpts=PTS-STARTPTS,{_atempo_chain(seg.speed)},volume={timeline.audio_gain:.6g}"])
                cmd.extend(["-c:v","libx264","-preset","veryfast"])
                if timeline.source.has_audio and not timeline.mute_audio: cmd.extend(["-c:a","aac"])
                cmd.extend(["-avoid_negative_ts","make_zero",str(part)])
                p=subprocess.run(cmd,capture_output=True,text=True)
                if p.returncode: raise RenderError(p.stderr[-2000:])
                parts.append(part)
            concat=temp/"concat.txt"
            def quote_concat(path:Path)->str: return path.as_posix().replace("'","'\\''")
            concat.write_text("".join(f"file '{quote_concat(p)}'\n" for p in parts),encoding="utf-8")
            staged= temp/"final.mp4"
            intervals=[caption_output_interval(timeline,c) for c in timeline.captions]
            has_visible_captions=any(interval and interval[1]>interval[0] for interval in intervals)
            audio_enabled=timeline.source.has_audio and not timeline.mute_audio
            audio_mix_args=_audio_mix_args(timeline,music_path)
            final_audio_enabled=audio_enabled or bool(music_path)
            audio_input_count=(4 if music_path else 0)+(4 if audio_enabled and bleep_output_intervals(timeline) else 0)
            audio_input_args=audio_mix_args[:audio_input_count]
            audio_output_args=audio_mix_args[audio_input_count:]
            if has_visible_captions:
                filters=subprocess.run([ffmpeg,"-hide_banner","-filters"],capture_output=True,text=True)
                if filters.returncode or not re.search(r"\bsubtitles\s+V->V",filters.stdout):
                    raise RenderError("This FFmpeg build does not include the subtitles/libass filter required to burn captions into the video.")
                ass=temp/"captions.ass"; ass.write_text(caption_ass(timeline),encoding="utf-8-sig")
                filter_arg=f"subtitles=filename='{_escape_filter_path(ass)}'"
                command=[ffmpeg,"-y","-f","concat","-safe","0","-i",str(concat)]
                command.extend(audio_input_args)
                command.extend(["-vf",filter_arg,"-map","0:v:0"])
                if final_audio_enabled:command.extend(audio_output_args if audio_mix_args else ["-map","0:a:0?"])
                command.extend(["-c:v","libx264","-preset","veryfast"])
                if final_audio_enabled: command.extend(["-c:a","aac"])
                if music_path:command.append("-shortest")
                command.append(str(staged))
            else:
                command=[ffmpeg,"-y","-f","concat","-safe","0","-i",str(concat)]
                command.extend(audio_input_args)
                command.extend(["-map","0:v:0","-c:v","libx264","-preset","veryfast","-crf","18"])
                if final_audio_enabled:
                    command.extend(audio_output_args if audio_mix_args else ["-map","0:a:0?"])
                    command.extend(["-c:a","aac","-b:a","192k"])
                if music_path:command.append("-shortest")
                command.extend(["-movflags","+faststart",str(staged)])
            p=subprocess.run(command,capture_output=True,text=True)
            if p.returncode: raise RenderError(p.stderr[-2000:])
            if not staged.exists() or staged.stat().st_size==0: raise RenderError("FFmpeg produced an empty output file.")
            os.replace(staged,out)
    except OSError as exc:
        raise RenderError(str(exc)) from exc
    source_audio=timeline.source.has_audio and not timeline.mute_audio
    manifest={"output":out.name,"expected_duration_s":duration(timeline),"audio_muted":timeline.mute_audio,"audio_gain":timeline.audio_gain,"background_music_added":bool(music_path),"music_gain":timeline.music_gain,"music_ducking":bool(music_path and source_audio and timeline.duck_music),"bleeps":[{"output_interval_s":interval} for interval in bleep_output_intervals(timeline)],"captions":[{"id":c.id,"output_interval_s":caption_output_interval(timeline,c)} for c in timeline.captions],"captions_burned_in":any(caption_output_interval(timeline,c) is not None for c in timeline.captions)}
    manifest_path=out.with_suffix(".manifest.json"); staged_manifest=manifest_path.with_suffix(".manifest.json.tmp")
    staged_manifest.write_text(json.dumps(manifest,indent=2),encoding="utf-8"); os.replace(staged_manifest,manifest_path)
    return manifest
