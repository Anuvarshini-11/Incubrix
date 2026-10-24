from __future__ import annotations
import hashlib,json,os,uuid,shutil,subprocess,re,mimetypes
from pathlib import Path
from fastapi import FastAPI,UploadFile,File,HTTPException,Header,BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse,StreamingResponse
from starlette.requests import Request
from pydantic import BaseModel
from . import store
from .models import Timeline,Source,Segment,Word,InstructionResult
from .engine import History,duration,timeline_hash,validate,replace_transcript,remove_silence,set_background_music
from .parser import execute_instruction
from .render_jobs import run_render_job
from .transcription import TranscriptionUnavailable,transcribe_file
from .audio_tools import AudioAnalysisError,detect_silence
DATA=Path(os.getenv("INCUBRIX_DATA","data")); MEDIA=DATA/"media"; OUTPUT=DATA/"renders"; MEDIA.mkdir(parents=True,exist_ok=True); OUTPUT.mkdir(parents=True,exist_ok=True)
MEDIA_TOOLS=Path(os.getenv("FFMPEG_BIN", "")) if os.getenv("FFMPEG_BIN") else None
if MEDIA_TOOLS and MEDIA_TOOLS.is_dir(): os.environ["PATH"]=str(MEDIA_TOOLS)+os.pathsep+os.environ.get("PATH","")
app=FastAPI(title="IncuBrix Video Editing Engine",version="1.0.0")
app.add_middleware(CORSMiddleware,allow_origins=[os.getenv("WEB_ORIGIN","http://localhost:5173"),"http://127.0.0.1:5173"],allow_methods=["*"],allow_headers=["*"],expose_headers=["Accept-Ranges","Content-Range","Content-Length"])
store.init()
class CreateSession(BaseModel): video_id:str
class Instruction(BaseModel): instruction:str
class Clarification(BaseModel): answer:str; clarification_id:str|None=None; instruction:str|None=None
class TranscriptUpdate(BaseModel): words:list[Word]

def error(code,msg,status=400,details=None): return HTTPException(status_code=status,detail={"error":{"code":code,"message":msg,"retryable":False,"details":details or {}}})
def idempotency_lookup(key:str|None,payload:bytes,scope:str):
    if not key: return None,None
    scoped_key=f"{scope}:{key}"
    digest=hashlib.sha256(payload).hexdigest(); cached=store.idempotent_get(scoped_key)
    if not cached: return digest,None
    if cached["payload_hash"]!=digest: raise error("IDEMPOTENCY_CONFLICT","This idempotency key was already used for a different request.",409)
    return digest,json.loads(cached["response"])
def metadata(path:Path,sha:str):
    probe=shutil.which("ffprobe")
    if not probe: raise RuntimeError("FFprobe is required to validate media and determine accurate duration. Install FFmpeg/FFprobe and restart the API.")
    p=subprocess.run([probe,"-v","error","-show_streams","-show_format","-of","json",str(path)],capture_output=True,text=True)
    if p.returncode: raise ValueError("File is not a readable media file")
    data=json.loads(p.stdout); streams=data.get("streams",[]); v=next((s for s in streams if s.get("codec_type")=="video"),None)
    if not v: raise ValueError("Uploaded file contains no video stream")
    return {"sha256":sha,"duration_s":float(data["format"]["duration"]),"width":v.get("width"),"height":v.get("height"),"frame_rate":v.get("r_frame_rate","25/1"),"has_audio":any(s.get("codec_type")=="audio" for s in streams)}
@app.get("/health")
def health(): return {"status":"ok"}
@app.get("/ready")
def ready():
    try:
        with store.connect() as connection: connection.execute("SELECT 1").fetchone()
    except Exception as exc:
        raise error("NOT_READY",f"Database is unavailable: {exc}",503)
    return {"status":"ready","capabilities":{"media_probe":bool(shutil.which("ffprobe")),"render":bool(shutil.which("ffmpeg"))}}
@app.post("/api/v1/videos",status_code=201)
async def upload_video(file:UploadFile=File(...),idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
    if not file.filename or Path(file.filename).suffix.lower() not in {".mp4",".mov",".mkv",".webm"}: raise error("INVALID_MEDIA","Upload a supported video file (.mp4, .mov, .mkv, .webm).")
    content=await file.read(512*1024*1024+1)
    if len(content)>512*1024*1024: raise error("FILE_TOO_LARGE","Maximum upload size is 512 MiB.",413)
    payload=(file.filename+"\0").encode()+content
    digest,cached=idempotency_lookup(idempotency_key,payload,"upload")
    if cached is not None: return cached
    sha=hashlib.sha256(content).hexdigest(); vid=uuid.uuid4().hex; path=MEDIA/f"{vid}{Path(file.filename).suffix.lower()}"; path.write_bytes(content)
    try: meta=metadata(path,sha)
    except RuntimeError as e:
        path.unlink(missing_ok=True)
        raise error("MEDIA_TOOL_UNAVAILABLE",str(e),503)
    except Exception as e: path.unlink(missing_ok=True); raise error("INVALID_MEDIA",str(e))
    store.put_video(vid,path,meta); response={"video_id":vid,"filename":Path(file.filename).name,"metadata":meta}
    if idempotency_key: store.idempotent_put(f"upload:{idempotency_key}",digest,response)
    return response
@app.post("/api/v1/sessions/{session_id}/remove-silence")
def remove_silence_session(session_id:str):
    row,h=load(session_id)
    if not h.current.source.has_audio: raise error("NO_AUDIO","Silence detection requires a source audio track.",422)
    video=store.get_video(row["video_id"])
    try: intervals=detect_silence(video["path"],h.current.source.duration_s)
    except AudioAnalysisError as exc: raise error("AUDIO_ANALYSIS_FAILED",str(exc),503)
    if not intervals: return result("applied","No silence longer than 0.7 seconds was detected.",h)
    try: op=h.apply(remove_silence,intervals)
    except ValueError as exc: return result("rejected",str(exc),h)
    save(row["video_id"],h)
    return result("applied",f"Removed {op['removed_intervals']} detected silent intervals.",h,operations=[op])
@app.post("/api/v1/sessions/{session_id}/music",status_code=201)
async def upload_background_music(session_id:str,file:UploadFile=File(...)):
    row,h=load(session_id)
    extension=Path(file.filename or "").suffix.lower()
    if extension not in {".mp3",".wav",".m4a",".aac",".ogg",".flac"}:
        raise error("INVALID_AUDIO","Upload a supported music file (.mp3, .wav, .m4a, .aac, .ogg, .flac).")
    content=await file.read(128*1024*1024+1)
    if len(content)>128*1024*1024:raise error("FILE_TOO_LARGE","Maximum background music size is 128 MiB.",413)
    probe=shutil.which("ffprobe")
    if not probe:raise error("MEDIA_TOOL_UNAVAILABLE","FFprobe is required to validate background audio.",503)
    asset_id=uuid.uuid4().hex;path=MEDIA/f"music_{asset_id}{extension}";path.write_bytes(content)
    probed=subprocess.run([probe,"-v","error","-show_streams","-of","json",str(path)],capture_output=True,text=True)
    try:streams=json.loads(probed.stdout).get("streams",[]) if probed.returncode==0 else []
    except json.JSONDecodeError:streams=[]
    if not any(stream.get("codec_type")=="audio" for stream in streams):
        path.unlink(missing_ok=True)
        raise error("INVALID_AUDIO","The uploaded file contains no readable audio stream.")
    store.put_audio_asset(asset_id,session_id,path)
    try:op=h.apply(set_background_music,asset_id)
    except (ValueError,TypeError) as exc:
        path.unlink(missing_ok=True)
        raise error("INVALID_AUDIO",str(exc))
    save(row["video_id"],h)
    return {"asset_id":asset_id,"timeline":h.current.model_dump(),"operation":op}
@app.get("/api/v1/videos/{video_id}")
def get_video(video_id:str):
    v=store.get_video(video_id)
    if not v: raise error("NOT_FOUND","Video not found",404)
    return {"video_id":video_id,"metadata":json.loads(v["metadata"])}
@app.get("/api/v1/media/{video_id}")
def get_media(video_id:str,request:Request):
    v=store.get_video(video_id)
    if not v: raise error("NOT_FOUND","Video not found",404)
    media_path=Path(v["path"])
    media_type=mimetypes.guess_type(v["path"])[0] or "application/octet-stream"
    file_size=media_path.stat().st_size
    range_header=request.headers.get("range")
    if range_header:
        match=re.fullmatch(r"bytes=(\d*)-(\d*)",range_header.strip())
        if not match: raise HTTPException(status_code=416,headers={"Content-Range":f"bytes */{file_size}"})
        first,last=match.groups()
        if not first:
            suffix_length=int(last or 0); start=max(0,file_size-suffix_length); end=file_size-1
        else:
            start=int(first); end=min(int(last) if last else file_size-1,file_size-1)
        if start>=file_size or start>end: raise HTTPException(status_code=416,headers={"Content-Range":f"bytes */{file_size}"})
        length=end-start+1
        def stream_range():
            with media_path.open("rb") as media_file:
                media_file.seek(start); remaining=length
                while remaining:
                    chunk=media_file.read(min(1024*1024,remaining))
                    if not chunk: break
                    remaining-=len(chunk); yield chunk
        return StreamingResponse(stream_range(),status_code=206,media_type=media_type,headers={"Accept-Ranges":"bytes","Content-Range":f"bytes {start}-{end}/{file_size}","Content-Length":str(length),"Content-Disposition":f'inline; filename="{media_path.name}"'})
    return FileResponse(v["path"],media_type=media_type,filename=media_path.name,content_disposition_type="inline",headers={"Accept-Ranges":"bytes"})
@app.get("/api/v1/videos/{video_id}/transcript")
def transcript(video_id:str):
    if not store.get_video(video_id): raise error("NOT_FOUND","Video not found",404)
    s=store.get_session_by_video(video_id) if hasattr(store,"get_session_by_video") else None
    words=json.loads(s["timeline"])["transcript"] if s else []
    return {"words":words,"provider":"stored-transcript" if words else "not-run","note":"Run the optional local faster-whisper transcription endpoint or provide word timings manually."}
@app.put("/api/v1/sessions/{session_id}/transcript")
def update_transcript(session_id:str,body:TranscriptUpdate):
    row,h=load(session_id)
    try: op=h.apply(replace_transcript,body.words)
    except (ValueError,TypeError) as e: raise error("INVALID_TRANSCRIPT",str(e))
    save(row["video_id"],h)
    return result("applied",f"Transcript updated with {op['word_count']} words.",h,operations=[op])
@app.post("/api/v1/sessions/{session_id}/transcribe")
def transcribe_session(session_id:str):
    row,h=load(session_id)
    if not h.current.source.has_audio: raise error("NO_AUDIO","This video has no audio track to transcribe.",422)
    video=store.get_video(row["video_id"])
    try: words=transcribe_file(video["path"])
    except TranscriptionUnavailable as exc: raise error("TRANSCRIPTION_UNAVAILABLE",str(exc),503)
    except (OSError,RuntimeError,ValueError) as exc: raise error("TRANSCRIPTION_FAILED",str(exc),503)
    try: op=h.apply(replace_transcript,words)
    except (ValueError,TypeError) as exc: raise error("INVALID_TRANSCRIPT",str(exc))
    save(row["video_id"],h)
    response=result("applied",f"Recognized {op['word_count']} words.",h,operations=[{**op,"provider":"faster-whisper"}])
    return response
@app.post("/api/v1/sessions",status_code=201)
def create_session(body:CreateSession):
    v=store.get_video(body.video_id)
    if not v: raise error("NOT_FOUND","Video not found",404)
    meta=json.loads(v["metadata"]); t=Timeline(timeline_id=uuid.uuid4().hex,source=Source(media_id=body.video_id,sha256=meta["sha256"],duration_s=meta["duration_s"],width=meta.get("width"),height=meta.get("height"),frame_rate=meta.get("frame_rate","25/1"),has_audio=meta.get("has_audio",True)),segments=[Segment(id="seg_initial",source_start_s=0,source_end_s=meta["duration_s"])])
    h=History(t); save(body.video_id,h); return {"session_id":t.timeline_id,"timeline":t.model_dump(),"timeline_hash":timeline_hash(t)}
def save(video_id,h,pending=None):
    store.put_session(h.current.timeline_id,video_id,h.current.model_dump_json(),json.dumps({"undo":[x.model_dump() for x in h.undo_stack],"redo":[x.model_dump() for x in h.redo_stack]}),pending)
def load(sid):
    row=store.get_session(sid)
    if not row: raise error("NOT_FOUND","Session not found",404)
    t=Timeline.model_validate_json(row["timeline"]); hist=json.loads(row["history"]); h=History(t); h.undo_stack=[Timeline.model_validate(x) for x in hist["undo"]];h.redo_stack=[Timeline.model_validate(x) for x in hist["redo"]];return row,h
def result(status,summary,h,operations=None,question=None,options=None,warnings=None,clarification_id=None):
    return InstructionResult(status=status,summary=summary,operations=operations or [],question=question,options=options or [],timeline_hash=timeline_hash(h.current),duration_s=duration(h.current),warnings=warnings or [],revision_id=f"rev_{h.current.revision}",clarification_id=clarification_id).model_dump()
@app.get("/api/v1/timelines/{timeline_id}")
def get_timeline(timeline_id:str):
    _,h=load(timeline_id);return {"timeline":h.current.model_dump(),"timeline_hash":timeline_hash(h.current),"duration_s":duration(h.current)}
@app.post("/api/v1/sessions/{session_id}/instructions")
def instruction(session_id:str,body:Instruction,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
    digest,cached=idempotency_lookup(idempotency_key,(session_id+"\0"+body.instruction).encode(),f"instruction:{session_id}")
    if cached is not None: return cached
    row,h=load(session_id); action=execute_instruction(h,body.instruction)
    if action.get("clarification"):
        clarification_id=uuid.uuid4().hex
        pending={"clarification_id":clarification_id,"instruction":body.instruction,"options":action["options"]}
        save(row["video_id"],h,pending)
        return result("needs_clarification",action["summary"],h,question=action["question"],options=action["options"],clarification_id=clarification_id)
    if action.get("rejected"):
        save(row["video_id"],h)
        return result("rejected",action["rejected"],h)
    save(row["video_id"],h); op=action.get("op"); response=result("applied",action["summary"],h,operations=[op] if isinstance(op,dict) else [])
    if idempotency_key: store.idempotent_put(f"instruction:{session_id}:{idempotency_key}",digest,response)
    return response
@app.post("/api/v1/sessions/{session_id}/clarifications")
def clarification(session_id:str,body:Clarification):
    row,h=load(session_id)
    pending=json.loads(row["pending"]) if row.get("pending") else None
    if not pending: return result("rejected","There is no pending clarification for this session.",h)
    if not body.clarification_id or body.clarification_id!=pending["clarification_id"]:
        return result("rejected","This clarification is no longer current. Submit the instruction again to get current choices.",h)
    if body.instruction and body.instruction.strip()!=pending["instruction"].strip():
        return result("rejected","This answer does not match the pending instruction. Choose one of its listed options.",h)
    if body.answer not in pending["options"]:
        return result("rejected","Choose one of the listed occurrence options for the pending instruction.",h)
    match=re.search(r"occurrence\s+(\d+)",body.answer,re.I)
    if not match: return result("rejected","The selected option is invalid.",h)
    action=execute_instruction(h,pending["instruction"],int(match.group(1)))
    if action.get("clarification"):
        new_id=uuid.uuid4().hex
        save(row["video_id"],h,{"clarification_id":new_id,"instruction":pending["instruction"],"options":action["options"]})
        return result("needs_clarification",action["summary"],h,question=action["question"],options=action["options"],clarification_id=new_id)
    if action.get("rejected"): return result("rejected",action["rejected"],h)
    save(row["video_id"],h); op=action.get("op")
    return result("applied",action["summary"],h,operations=[op] if isinstance(op,dict) else [])
@app.get("/api/v1/sessions/{session_id}/history")
def history(session_id:str):
    _,h=load(session_id);return {"revision":h.current.revision,"can_undo":bool(h.undo_stack),"can_redo":bool(h.redo_stack),"undo_depth":len(h.undo_stack)}
@app.put("/api/v1/sessions/{session_id}/timeline")
def update_timeline(session_id:str,body:Timeline):
    row,h=load(session_id)
    if body.timeline_id != session_id or body.source != h.current.source:
        raise error("INVALID_TIMELINE","Timeline identity and source media cannot be changed.")
    if body.background_music_id and not store.get_audio_asset(body.background_music_id,session_id):
        raise error("INVALID_TIMELINE","The selected background music does not belong to this session.")
    candidate=body.model_copy(deep=True)
    if candidate.model_dump(exclude={"revision"}) == h.current.model_dump(exclude={"revision"}):
        raise error("NO_CHANGE","The timeline is already in that state.",409)
    candidate.revision=h.current.revision+1
    try: validate(candidate)
    except (ValueError,TypeError) as exc: raise error("INVALID_TIMELINE",str(exc))
    h.undo_stack.append(h.current.model_copy(deep=True))
    h.current=candidate
    h.redo_stack.clear()
    save(row["video_id"],h)
    return {"timeline":h.current.model_dump(),"timeline_hash":timeline_hash(h.current),"duration_s":duration(h.current)}
@app.post("/api/v1/sessions/{session_id}/undo")
def undo(session_id:str):
    row,h=load(session_id)
    try:h.undo()
    except ValueError as e:raise error("NO_UNDO",str(e),409)
    save(row["video_id"],h);return result("applied","Undid exactly one edit.",h,operations=[{"type":"undo"}])
@app.post("/api/v1/sessions/{session_id}/redo")
def redo(session_id:str):
    row,h=load(session_id)
    try:h.redo()
    except ValueError as e:raise error("NO_REDO",str(e),409)
    save(row["video_id"],h);return result("applied","Redid one edit.",h,operations=[{"type":"redo"}])
@app.post("/api/v1/sessions/{session_id}/render",status_code=202)
def render_session(session_id:str,background_tasks:BackgroundTasks):
    row,h=load(session_id)
    active=store.get_active_render_job(session_id)
    if active:
        return {"job_id":active["id"],"status_url":f"/api/v1/render-jobs/{active['id']}","status":active["status"],"progress":active["progress"]}
    video=store.get_video(row["video_id"])
    if not video or not Path(video["path"]).is_file():raise error("NOT_FOUND","The source video is missing.",404)
    music=store.get_audio_asset(h.current.background_music_id,session_id) if h.current.background_music_id else None
    if h.current.background_music_id and not music:raise error("INVALID_TIMELINE","The selected background music is unavailable.")
    job_id=uuid.uuid4().hex
    store.create_render_job(job_id,session_id)
    output=OUTPUT/f"{job_id}.mp4"
    background_tasks.add_task(run_render_job,job_id,h.current.model_dump_json(),video["path"],str(output),music["path"] if music else None)
    return {"job_id":job_id,"status_url":f"/api/v1/render-jobs/{job_id}","status":"queued","progress":0}
@app.get("/api/v1/render-jobs/{job_id}")
def render_job_status(job_id:str):
    job=store.get_render_job(job_id)
    if not job:raise error("NOT_FOUND","Render job not found.",404)
    return {key:job[key] for key in ("id","session_id","status","progress","error","download_url","created_at","updated_at")}
@app.get("/api/v1/renders/{session_id}")
def download_render(session_id:str):
    job=store.get_render_job(session_id)
    path=OUTPUT/f"{session_id}.mp4"
    if not path.exists():raise error("NOT_FOUND","Render not found",404)
    if job and job["status"]!="completed":raise error("RENDER_NOT_READY","Render job has not completed.",409)
    return FileResponse(path,media_type="video/mp4",filename=path.name)
