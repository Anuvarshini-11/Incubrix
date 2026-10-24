from __future__ import annotations
import json, sqlite3, os
from pathlib import Path
DB=Path(os.getenv("INCUBRIX_DB","data/incubrix.sqlite3"))
DB.parent.mkdir(parents=True,exist_ok=True)
def connect():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY,path TEXT NOT NULL,metadata TEXT NOT NULL); CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,video_id TEXT NOT NULL,timeline TEXT NOT NULL,history TEXT NOT NULL,pending TEXT); CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,response TEXT NOT NULL); CREATE TABLE IF NOT EXISTS render_jobs(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL,error TEXT,download_url TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP); CREATE TABLE IF NOT EXISTS audio_assets(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,path TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);''')
        columns={row[1] for row in c.execute('PRAGMA table_info(sessions)')}
        if "pending" not in columns:c.execute("ALTER TABLE sessions ADD COLUMN pending TEXT")
        c.execute("UPDATE render_jobs SET status='failed',error='Render was interrupted by an API restart.',updated_at=CURRENT_TIMESTAMP WHERE status IN ('queued','running')")
def put_video(i,path,meta):
    with connect() as c:c.execute('INSERT INTO videos VALUES(?,?,?)',(i,str(path),json.dumps(meta)))
def get_video(i):
    with connect() as c:r=c.execute('SELECT * FROM videos WHERE id=?',(i,)).fetchone()
    return dict(r) if r else None
def put_session(i,video,timeline,history,pending=None):
    with connect() as c:c.execute('INSERT OR REPLACE INTO sessions(id,video_id,timeline,history,pending) VALUES(?,?,?,?,?)',(i,video,timeline,history,json.dumps(pending) if pending else None))
def get_session(i):
    with connect() as c:r=c.execute('SELECT * FROM sessions WHERE id=?',(i,)).fetchone()
    return dict(r) if r else None
def get_session_by_video(video_id):
    with connect() as c:r=c.execute('SELECT * FROM sessions WHERE video_id=? ORDER BY rowid DESC LIMIT 1',(video_id,)).fetchone()
    return dict(r) if r else None
def idempotent_get(key):
    with connect() as c:r=c.execute('SELECT * FROM idempotency WHERE key=?',(key,)).fetchone()
    return dict(r) if r else None
def idempotent_put(key,h,response):
    with connect() as c:c.execute('INSERT OR REPLACE INTO idempotency VALUES(?,?,?)',(key,h,json.dumps(response)))

def create_render_job(job_id,session_id):
    with connect() as c:c.execute("INSERT INTO render_jobs(id,session_id,status,progress) VALUES(?,?,'queued',0)",(job_id,session_id))
def update_render_job(job_id,status,progress,error=None,download_url=None):
    with connect() as c:c.execute("UPDATE render_jobs SET status=?,progress=?,error=?,download_url=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,progress,error,download_url,job_id))
def get_render_job(job_id):
    with connect() as c:r=c.execute("SELECT * FROM render_jobs WHERE id=?",(job_id,)).fetchone()
    return dict(r) if r else None
def get_active_render_job(session_id):
    with connect() as c:r=c.execute("SELECT * FROM render_jobs WHERE session_id=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",(session_id,)).fetchone()
    return dict(r) if r else None
def put_audio_asset(asset_id,session_id,path):
    with connect() as c:c.execute("INSERT INTO audio_assets(id,session_id,path) VALUES(?,?,?)",(asset_id,session_id,str(path)))
def get_audio_asset(asset_id,session_id):
    with connect() as c:r=c.execute("SELECT * FROM audio_assets WHERE id=? AND session_id=?",(asset_id,session_id)).fetchone()
    return dict(r) if r else None
