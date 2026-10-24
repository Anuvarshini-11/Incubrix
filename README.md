# IncuBrix Prompt-Based Video Editing Engine

A local-first video-editing application with a React workspace, FastAPI service, a deterministic content-anchored timeline core, SQLite persistence, and FFmpeg-based segment rendering. The original upload is stored separately and never overwritten.

## Run locally

Requirements: Python 3.12+, Node.js 20+, and FFmpeg/FFprobe on `PATH` for accurate media probing and rendering. Copy `.env.example` to `.env`, then start the API and web app in separate terminals:

- Python environment: install `requirements.txt`, then run `uvicorn app.main:app --reload` from the repository root.
- Windows: run `./run_backend.ps1` to start the FFmpeg-enabled API on port 8000, then start the frontend with `cd web; npm run dev`. The Vite proxy forwards API and media requests to the backend.
- Frontend: `cd web`, run `npm install`, then `npm run dev`.
- Open `http://localhost:5173`; API docs are at `http://localhost:8000/docs`.

Docker startup: start Docker Desktop, then run `docker compose up --build` from the project root. API health: `/health` and `/ready`.

See [SOURCE.md](./SOURCE.md) for a source-code map, Docker run steps, and audio-preview troubleshooting.

Tests: `python -m pytest -q` (no paid provider/API keys required). Frontend production build: `cd web; npm run build`. Browser workflow: start the existing Docker stack, install Chromium with `cd web; npx playwright install chromium`, then run `npm run test:e2e`. The E2E workflow uses `data/sample_av.mp4` and requires FFmpeg/FFprobe on the test machine.

## Implemented behavior

- Safe extension-checked upload, byte limit, SHA-256, FFprobe metadata validation, local SQLite media/session persistence.
- Timeline schema with immutable source reference, word-level speaker/confidence fields, source-interval segments, content-anchored captions, canonical JSON hash, validation, current-time range cuts/speed changes, deterministic undo/redo, and explicit rejection/clarification outcomes.
- Deterministic instruction parser for first/range cuts, speed changes, caption anchors, undo/redo, and explicit rejection of unsupported requests. Ambiguous caption phrases require a session-bound clarification choice.
- Transcript-aware filler-word removal (`um`, `uh`, `erm`, `er`, `ah`, `hmm`) and clip reordering with undo/redo. Both require accurate word timings.
- FFmpeg silence detection/removal (threshold -35 dB, minimum 0.7 seconds), source-anchored audio bleep intervals rendered as a 1 kHz tone, and optional uploaded background music mixed with side-chain ducking.
- Optional local faster-whisper speech recognition; install `requirements-asr.txt` to enable it. The first request downloads the configured model; no cloud provider or key is used.
- React drag/drop and file-picker upload, browser video preview, scrubbable timeline, manual timed caption editor, assistant, activity list, persisted undo/redo, and render download.
- Split a clip at the playhead, adjust source audio from silent to 100% in preview and rendered exports, and mute audio; audio controls are disabled with a clear warning when FFprobe finds no source audio.
- Assistant demo commands for trimming the first/last seconds, setting playback speed, muting/unmuting audio, adding timestamped captions, and undo/redo. Unsupported commands are rejected without changing the timeline.
- Additional editor commands include `move clip 2 to position 1`, `remove filler words`, and `bleep from 4 seconds to 5 seconds`. Use the timeline's **Remove silence** action for FFmpeg-detected silent ranges.
- FFmpeg segment extraction/concat rendering applies trims, speed changes, audio gain/mute, bleep tones, optional side-chain ducked background music, and timed caption burn-in when the FFmpeg build includes libass. Rendered output is a separate MP4 with a JSON manifest.
- Rendering is tracked as a persisted job, and the frontend polls job status until a downloadable output is ready. Jobs run in the API process; active jobs interrupted by an API restart are marked failed.
- Generic validated timeline updates persist trim, split, speed, audio gain/mute, and caption edits in the same undo/redo history as API instructions.
- Idempotent upload and instruction requests when clients send an `Idempotency-Key` header; conflicting key reuse returns HTTP 409.
- Active browser session restoration after refresh using local browser storage; temporary API outages no longer erase the saved project pointer.

## Important limits (not implied to work)

This is a local editing MVP, not a production multi-user service. ASR is optional and model-dependent; transcript accuracy should be reviewed before captioning or filler removal. There is no speaker diarization, semantic search, section keep-only, revision restore, distributed render worker, callback signing, S3/MinIO integration, or URL ingest. Filler removal only removes the supported filler tokens from an existing word-timed transcript. Silence detection uses a fixed threshold/duration and should be reviewed for speech/breathing cutoffs. Render jobs are tracked in SQLite but execute in the API process; a restart marks active jobs failed. Idempotency currently covers upload and instruction requests, but is not a transactional guarantee under concurrent retries. Rendering requires FFmpeg; caption burn-in additionally requires a build with the subtitles/libass filter. FFprobe is required for upload validation and accurate metadata. Render duration and A/V synchronization have not been certified to a one-frame tolerance. Docker Compose is configured for local persistent API data, not MinIO. Authentication, quotas, rate limiting, and worker isolation are absent; do not expose this service to the public internet or upload sensitive media to an exposed service.

To enable local ASR in a Python environment, install `python -m pip install -r requirements-asr.txt`. In Docker, install into the current API container with `docker compose exec api pip install -r /srv/requirements-asr.txt`; the container image includes this optional requirements file, but does not install the large model dependency by default. Set `INCUBRIX_WHISPER_MODEL` to select a model (default `tiny`).

All clock times currently mean **current timeline time**. Source time is retained in segments and anchors, but a public source-time switch is not implemented. Speed range accepted by the engine is 0.25x–4x. Unsupported or ambiguous natural language is rejected or clarified rather than treated as successful.

## API

All app routes are under `/api/v1/`; FastAPI generates OpenAPI 3.1 at `/openapi.json` and Swagger UI at `/docs`.

- `POST /api/v1/videos` multipart `file` → video id and probed metadata.
- `GET /api/v1/videos/{video_id}` and `/transcript`; `/api/v1/media/{video_id}` serves the uploaded preview.
- `PUT /api/v1/sessions/{session_id}/transcript` JSON `{ "words": [{"id":"w1","start_s":1.0,"end_s":1.4,"text":"hello"}] }` imports or replaces source-time word timings.
- `POST /api/v1/sessions/{session_id}/transcribe` performs optional local faster-whisper word transcription.
- `POST /api/v1/sessions/{session_id}/remove-silence` detects and cuts long silent ranges using FFmpeg.
- `POST /api/v1/sessions/{session_id}/music` uploads a background track; rendering mixes it with optional side-chain ducking.
- `POST /api/v1/sessions` JSON `{ "video_id": "..." }`.
- `GET /api/v1/timelines/{timeline_id}`.
- `PUT /api/v1/sessions/{session_id}/timeline` persists a validated full timeline revision, including timed captions, audio mute state, and audio gain (0.0–1.0).
- `POST /api/v1/sessions/{session_id}/instructions` JSON `{ "instruction": "..." }`.
- `POST .../undo`, `POST .../redo`, `GET .../history`, `POST .../render`.
- `GET /api/v1/render-jobs/{job_id}` returns job state; `GET /api/v1/renders/{job_id}` downloads a completed output.

Repeated word-anchored caption requests return `needs_clarification` with a session-bound token. The UI's manual captions store source intervals, appear on the timeline, and can be edited, deleted, or selected to seek. The assistant supports `bleep from 4 seconds to 5 seconds`; marked ranges are replaced with a 1 kHz tone in rendered audio. Successful renders include supported trim, speed, mute, bleep, and caption edits; caption burn-in requires FFmpeg libass, otherwise the render reports a clear error.

Structured edit results use `applied`, `needs_clarification`, or `rejected`. Upload validation errors use FastAPI's `detail.error` object with code/message/retryable/details. OpenAPI is generated from the running application; `openapi.yaml` is a snapshot and must be regenerated after API changes.

## Session runner

The runner calls the live API. JSON session format: `{ "video": "./path/to/video.mp4", "steps": [{"transcript":[{"id":"w1","start_s":1,"end_s":1.4,"text":"hello"}]},{"say":"Add caption 'Hi' when I say 'hello'"},{"render":true}] }`. A following `{"answer":"Occurrence 1"}` step resolves a preceding clarification. Replace the sample media path with a supported local video. Run `python -m app.session_runner ./sessions/example.json --output ./artifacts/example` while the API is running.

## Security/configuration

Settings: `INCUBRIX_DATA`, `INCUBRIX_DB`, `WEB_ORIGIN`; `.env.example` contains placeholders only. Upload names are replaced with random identifiers, file size is bounded, and media content is verified by FFprobe when installed. URL fetching is absent, avoiding SSRF in this increment. Never provide user-authored FFmpeg filter strings. The API is intended for local development only; authentication, rate limiting and production isolation are future work.

## Deployment

Container deployment is intended for a private development environment: `docker compose up --build`. The compose stack has a persistent volume and API health check. Do not publish it directly to the internet; add authentication, quotas, private object storage, worker isolation, TLS, and rate limiting before public deployment.
