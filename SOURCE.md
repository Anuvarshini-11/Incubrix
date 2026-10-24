# Source Code and Run Guide

## Project overview

IncuBrix is a local video-editing app with a React/Vite frontend and a FastAPI backend. The browser handles the editing workspace and preview; the API stores uploaded media and timeline history, probes media with FFprobe, and renders edits with FFmpeg.

## Source map

| Path | Purpose |
| --- | --- |
| `web/src/App.tsx` | Editor UI, video preview, upload, split/audio controls, assistant commands, and API calls |
| `web/src/style.css` | Frontend styling |
| `web/vite.config.ts` | Vite dev server and local API proxy |
| `app/main.py` | FastAPI endpoints for media, sessions, timelines, history, transcription, and rendering |
| `app/models.py` | Timeline and media data models |
| `app/engine.py` | Timeline validation, duration, and history operations |
| `app/parser.py` | Supported natural-language edit commands |
| `app/rendering.py` | FFmpeg rendering pipeline |
| `app/render_jobs.py` | Persisted render job state lifecycle |
| `app/transcription.py` | Optional local faster-whisper recognition |
| `app/audio_tools.py` | FFmpeg silence analysis |
| `app/store.py` | SQLite persistence |
| `schemas/` | API and timeline schema files |
| `tests/` | Backend tests |
| `Dockerfile` | Backend image with FFmpeg installed |
| `docker-compose.yml` | Local API and frontend services |

## Run with Docker

1. Install and start Docker Desktop, with its Linux container engine running.
2. From the project root, run:

   ```powershell
   docker compose up --build
   ```

3. Open `http://localhost:5173`. The API docs are at `http://localhost:8000/docs`.
4. Stop the services with `Ctrl+C`; use `docker compose down` to remove the containers. Uploaded media and the database remain in the `incubrix-data` volume.

The frontend waits until the API health check succeeds before starting. The API stores its database, uploads, and renders under `/srv/data`, which is mounted to the persistent `incubrix-data` Docker volume.

The timeline supports splitting a clip at the playhead and setting source audio gain from 0% to 100%. Gain is persisted in timeline history and applied to preview and FFmpeg export; controls are omitted when the source has no audio stream. Users may upload a background music track; FFmpeg renders it with side-chain ducking beneath source audio. Bleep effects are anchored to source ranges and replace the original audio with a tone in the rendered output.

The render API returns a job ID which the frontend polls until completion. Jobs run in the API process and active jobs are marked failed after an API restart. Clip reorder and transcript-driven filler removal are available through the editing assistant. To enable ASR locally, install `requirements-asr.txt`; the default tiny model downloads on first use and can be changed with `INCUBRIX_WHISPER_MODEL`.

If either port is already in use, stop the other service using it before starting Docker Compose. If Docker reports that it cannot connect to the Docker API, start Docker Desktop and wait until its engine is ready, then retry.

## Run without Docker

Start the backend and frontend in separate terminals:

```powershell
.\run_backend.ps1
```

```powershell
cd web
npm ci
npm run dev
```

Open the frontend URL shown by Vite, usually `http://localhost:5173`.

Run browser end-to-end tests against the already-running app with `npm run test:e2e` from `web`. Install Playwright Chromium first with `npx playwright install chromium`; the tests do not start duplicate servers.

## Audio preview

Audio comes from the uploaded video itself. Use a video that has an audio stream, confirm the browser tab and video-player volume are not muted, and check that the editor does not show `MUTED`. The sample `data/sample_av.mp4` includes an audio stream. Preview support also depends on the browser supporting the file's audio and video codecs.
