from fastapi.testclient import TestClient
from app.main import app
from app import store
import json,uuid
from pathlib import Path

def test_health():
 c=TestClient(app);assert c.get('/health').json()['status']=='ok';assert c.get('/ready').status_code==200

def test_render_runs_as_job_and_exposes_status_and_download(monkeypatch,tmp_path):
 c=TestClient(app);video_id=uuid.uuid4().hex
 media=tmp_path/'source.mp4';media.write_bytes(b'source')
 store.put_video(video_id,str(media),{'sha256':'0'*64,'duration_s':2,'width':320,'height':180,'frame_rate':'25/1','has_audio':False})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 def fake_render(timeline,source,output):
  Path(output).write_bytes(b'rendered')
  return {'expected_duration_s':2}
 monkeypatch.setattr('app.render_jobs.render',fake_render)
 submitted=c.post(f'/api/v1/sessions/{session_id}/render')
 assert submitted.status_code==202
 job=submitted.json()
 status=c.get(job['status_url'])
 assert status.status_code==200 and status.json()['status']=='completed' and status.json()['progress']==100
 downloaded=c.get(status.json()['download_url'])
 assert downloaded.status_code==200 and downloaded.content==b'rendered'

def test_render_job_rejects_missing_source_file():
 c=TestClient(app);video_id=uuid.uuid4().hex
 store.put_video(video_id,'missing-source.mp4',{'sha256':'0'*64,'duration_s':2,'has_audio':False})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 response=c.post(f'/api/v1/sessions/{session_id}/render')
 assert response.status_code==404

def test_transcription_endpoint_saves_asr_words(monkeypatch,tmp_path):
 c=TestClient(app);video_id=uuid.uuid4().hex
 media=tmp_path/'source.mp4';media.write_bytes(b'source')
 store.put_video(video_id,str(media),{'sha256':'0'*64,'duration_s':5,'has_audio':True})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 from app.models import Word
 monkeypatch.setattr('app.main.transcribe_file',lambda path:[Word(id='asr_000001',start_s=1,end_s=1.4,text='hello',confidence=.9)])
 response=c.post(f'/api/v1/sessions/{session_id}/transcribe')
 assert response.status_code==200 and response.json()['operations'][0]['provider']=='faster-whisper'
 timeline=c.get(f'/api/v1/timelines/{session_id}').json()['timeline']
 assert timeline['transcript'][0]['text']=='hello'

def test_transcription_reports_optional_dependency_unavailable(monkeypatch,tmp_path):
 c=TestClient(app);video_id=uuid.uuid4().hex
 media=tmp_path/'source.mp4';media.write_bytes(b'source')
 store.put_video(video_id,str(media),{'sha256':'0'*64,'duration_s':5,'has_audio':True})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 from app.transcription import TranscriptionUnavailable
 def unavailable(path): raise TranscriptionUnavailable('Install the optional ASR package.')
 monkeypatch.setattr('app.main.transcribe_file',unavailable)
 response=c.post(f'/api/v1/sessions/{session_id}/transcribe')
 assert response.status_code==503 and 'optional ASR' in response.json()['detail']['error']['message']

def test_render_job_failure_is_reported_to_status_endpoint(monkeypatch,tmp_path):
 c=TestClient(app);video_id=uuid.uuid4().hex
 media=tmp_path/'source.mp4';media.write_bytes(b'source')
 store.put_video(video_id,str(media),{'sha256':'0'*64,'duration_s':2,'has_audio':False})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 from app.rendering import RenderError
 def fail_render(*args): raise RenderError('synthetic renderer failure')
 monkeypatch.setattr('app.render_jobs.render',fail_render)
 submitted=c.post(f'/api/v1/sessions/{session_id}/render')
 status=c.get(submitted.json()['status_url'])
 assert submitted.status_code==202
 assert status.json()['status']=='failed' and status.json()['error']=='synthetic renderer failure'

def test_remove_silence_applies_detected_source_intervals(monkeypatch,tmp_path):
 c=TestClient(app);video_id=uuid.uuid4().hex
 media=tmp_path/'source.mp4';media.write_bytes(b'source')
 store.put_video(video_id,str(media),{'sha256':'0'*64,'duration_s':10,'has_audio':True})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 monkeypatch.setattr('app.main.detect_silence',lambda path,duration:[(3,5)])
 response=c.post(f'/api/v1/sessions/{session_id}/remove-silence')
 assert response.status_code==200 and response.json()['status']=='applied'
 timeline=c.get(f'/api/v1/timelines/{session_id}').json()['timeline']
 assert abs(sum((s['source_end_s']-s['source_start_s'])/s['speed'] for s in timeline['segments'])-7.84)<1e-6

def test_music_upload_attaches_audio_track_to_session(monkeypatch,tmp_path):
 c=TestClient(app);video_id=uuid.uuid4().hex
 media=tmp_path/'source.mp4';media.write_bytes(b'source')
 store.put_video(video_id,str(media),{'sha256':'0'*64,'duration_s':10,'has_audio':True})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 monkeypatch.setattr('app.main.shutil.which',lambda _:'ffprobe')
 def audio_probe(command,**kwargs):
  return __import__('subprocess').CompletedProcess(command,0,json.dumps({'streams':[{'codec_type':'audio'}]}),'')
 monkeypatch.setattr('app.main.subprocess.run',audio_probe)
 response=c.post(f'/api/v1/sessions/{session_id}/music',files={'file':('bed.wav',b'audio','audio/wav')})
 assert response.status_code==201
 assert response.json()['timeline']['background_music_id']==response.json()['asset_id']
 assert c.get(f'/api/v1/timelines/{session_id}').json()['timeline']['music_gain']==0.25

def test_music_asset_cannot_be_referenced_from_another_session():
 c=TestClient(app);video_id=uuid.uuid4().hex
 store.put_video(video_id,'missing.mp4',{'sha256':'0'*64,'duration_s':10,'has_audio':True})
 first=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 second=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 asset_id=uuid.uuid4().hex
 store.put_audio_asset(asset_id,first,'bed.wav')
 timeline=c.get(f'/api/v1/timelines/{second}').json()['timeline']
 timeline['background_music_id']=asset_id
 response=c.put(f'/api/v1/sessions/{second}/timeline',json=timeline)
 assert response.status_code==400

def test_generated_openapi_31():
 c=TestClient(app);spec=c.get('/openapi.json').json()
 assert spec['openapi'].startswith('3.1')
 assert '/api/v1/render-jobs/{job_id}' in spec['paths']
 assert '/api/v1/sessions/{session_id}/transcribe' in spec['paths']
 assert '/api/v1/sessions/{session_id}/music' in spec['paths']
 assert '/api/v1/sessions/{session_id}/remove-silence' in spec['paths']

def test_transcript_update_and_clarification_flow(tmp_path):
 c=TestClient(app);video_id=uuid.uuid4().hex
 media=tmp_path/'source.mp4';media.write_bytes(b'test')
 store.put_video(video_id,str(media),{'sha256':'0'*64,'duration_s':20,'width':1280,'height':720,'frame_rate':'25/1','has_audio':True})
 created=c.post('/api/v1/sessions',json={'video_id':video_id});assert created.status_code==201
 session_id=created.json()['session_id']
 words=[{'id':'w1','start_s':1,'end_s':1.5,'text':'price'},{'id':'w2','start_s':8,'end_s':8.5,'text':'price'}]
 updated=c.put(f'/api/v1/sessions/{session_id}/transcript',json={'words':words})
 assert updated.status_code==200 and updated.json()['status']=='applied'
 instruction='Add caption "Cost" when I say "price"'
 pending=c.post(f'/api/v1/sessions/{session_id}/instructions',json={'instruction':instruction}).json()
 assert pending['status']=='needs_clarification' and len(pending['options'])==2
 applied=c.post(f'/api/v1/sessions/{session_id}/clarifications',json={'clarification_id':pending['clarification_id'],'answer':pending['options'][1]}).json()
 assert applied['status']=='applied'
 timeline=c.get(f'/api/v1/timelines/{session_id}').json()['timeline']
 assert timeline['captions'][0]['anchor']['word_ids']==['w2']

def test_clarification_token_rejects_stale_response():
 c=TestClient(app);video_id=uuid.uuid4().hex
 store.put_video(video_id,'missing.mp4',{'sha256':'0'*64,'duration_s':10,'width':640,'height':360,'frame_rate':'25/1','has_audio':False})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 words=[{'id':'w1','start_s':1,'end_s':1.2,'text':'price'},{'id':'w2','start_s':8,'end_s':8.2,'text':'price'}]
 c.put(f'/api/v1/sessions/{session_id}/transcript',json={'words':words})
 url=f'/api/v1/sessions/{session_id}/instructions';body={'instruction':'Add caption "Cost" when I say "price"'}
 first=c.post(url,json=body).json();second=c.post(url,json=body).json()
 stale=c.post(f'/api/v1/sessions/{session_id}/clarifications',json={'answer':first['options'][0],'clarification_id':first['clarification_id']}).json()
 assert stale['status']=='rejected'
 valid=c.post(f'/api/v1/sessions/{session_id}/clarifications',json={'answer':second['options'][1],'clarification_id':second['clarification_id']}).json()
 assert valid['status']=='applied'

def test_invalid_transcript_is_rejected_without_revision():
 c=TestClient(app);video_id=uuid.uuid4().hex
 store.put_video(video_id,'missing.mp4',{'sha256':'0'*64,'duration_s':10,'width':640,'height':360,'frame_rate':'25/1','has_audio':False})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 response=c.put(f'/api/v1/sessions/{session_id}/transcript',json={'words':[{'id':'bad','start_s':9,'end_s':12,'text':'out of bounds'}]})
 assert response.status_code==400
 assert c.get(f'/api/v1/timelines/{session_id}').json()['timeline']['revision']==0

def test_instruction_idempotency_replays_without_applying_twice():
 c=TestClient(app);video_id=uuid.uuid4().hex
 store.put_video(video_id,'missing.mp4',{'sha256':'0'*64,'duration_s':10,'width':640,'height':360,'frame_rate':'25/1','has_audio':False})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 url=f'/api/v1/sessions/{session_id}/instructions';headers={'Idempotency-Key':uuid.uuid4().hex}
 first=c.post(url,json={'instruction':'Cut the first 1 second'},headers=headers)
 replay=c.post(url,json={'instruction':'Cut the first 1 second'},headers=headers)
 conflict=c.post(url,json={'instruction':'Cut the first 2 seconds'},headers=headers)
 assert first.status_code==200 and replay.json()==first.json()
 assert conflict.status_code==409
 assert c.get(f'/api/v1/timelines/{session_id}').json()['timeline']['revision']==1

def test_media_preview_uses_upload_format_mime_type(tmp_path):
 c=TestClient(app);video_id=uuid.uuid4().hex
 media=tmp_path/'sample.webm';media.write_bytes(b'webm')
 store.put_video(video_id,str(media),{'sha256':'0'*64,'duration_s':1,'width':640,'height':360,'frame_rate':'30/1','has_audio':False})
 response=c.get(f'/api/v1/media/{video_id}')
 assert response.status_code==200 and response.headers['content-type'].startswith('video/webm')
 ranged=c.get(f'/api/v1/media/{video_id}',headers={'Range':'bytes=1-2'})
 assert ranged.status_code==206 and ranged.content==b'eb'
 assert ranged.headers['content-range']=='bytes 1-2/4'

def test_timeline_endpoint_persists_edits_and_undo_redo():
 c=TestClient(app);video_id=uuid.uuid4().hex
 store.put_video(video_id,'missing.mp4',{'sha256':'0'*64,'duration_s':10,'width':640,'height':360,'frame_rate':'25/1','has_audio':True})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 timeline=c.get(f'/api/v1/timelines/{session_id}').json()['timeline']
 timeline['segments']=[{'id':'trimmed','source_start_s':2,'source_end_s':8,'speed':1.5}]
 timeline['mute_audio']=True
 timeline['audio_gain']=0.75
 timeline['captions']=[{'id':'caption-1','text':'Welcome','anchor':{'word_ids':[],'source_start_s':3,'source_end_s':5}}]
 saved=c.put(f'/api/v1/sessions/{session_id}/timeline',json=timeline)
 assert saved.status_code==200
 assert saved.json()['timeline']['revision']==1
 assert c.get(f'/api/v1/sessions/{session_id}/history').json()['can_undo'] is True
 undone=c.post(f'/api/v1/sessions/{session_id}/undo')
 assert undone.status_code==200
 restored=c.get(f'/api/v1/timelines/{session_id}').json()['timeline']
 assert restored['segments'][0]['source_start_s']==0
 assert restored['mute_audio'] is False and restored['audio_gain']==1 and restored['captions']==[]
 assert c.get(f'/api/v1/sessions/{session_id}/history').json()['can_redo'] is True
 redone=c.post(f'/api/v1/sessions/{session_id}/redo')
 assert redone.status_code==200
 replay=c.get(f'/api/v1/timelines/{session_id}').json()['timeline']
 assert replay['mute_audio'] is True and replay['audio_gain']==0.75 and replay['captions'][0]['text']=='Welcome'

def test_timeline_rejects_audio_gain_outside_supported_range():
 c=TestClient(app);video_id=uuid.uuid4().hex
 store.put_video(video_id,'missing.mp4',{'sha256':'0'*64,'duration_s':10,'width':640,'height':360,'frame_rate':'25/1','has_audio':True})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 timeline=c.get(f'/api/v1/timelines/{session_id}').json()['timeline']
 timeline['audio_gain']=1.1
 response=c.put(f'/api/v1/sessions/{session_id}/timeline',json=timeline)
 assert response.status_code==422

def test_split_segments_persist_with_original_duration_and_undo():
 c=TestClient(app);video_id=uuid.uuid4().hex
 store.put_video(video_id,'missing.mp4',{'sha256':'0'*64,'duration_s':10,'width':640,'height':360,'frame_rate':'25/1','has_audio':True})
 session_id=c.post('/api/v1/sessions',json={'video_id':video_id}).json()['session_id']
 timeline=c.get(f'/api/v1/timelines/{session_id}').json()['timeline']
 timeline['segments']=[
  {'id':'left','source_start_s':0,'source_end_s':4,'speed':1},
  {'id':'right','source_start_s':4,'source_end_s':10,'speed':1},
 ]
 saved=c.put(f'/api/v1/sessions/{session_id}/timeline',json=timeline)
 assert saved.status_code==200 and len(saved.json()['timeline']['segments'])==2
 assert saved.json()['duration_s']==10
 undone=c.post(f'/api/v1/sessions/{session_id}/undo')
 restored=c.get(f'/api/v1/timelines/{session_id}')
 assert undone.status_code==200 and len(restored.json()['timeline']['segments'])==1
