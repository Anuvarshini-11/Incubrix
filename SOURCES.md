# Sources and Dependencies

## Project implementation map

- Editor and browser media preview: `web/src/App.tsx`.
- API and generated OpenAPI: `app/main.py`.
- Timeline schema and editing operations: `app/models.py`, `app/engine.py`, `app/parser.py`.
- FFmpeg rendering and render jobs: `app/rendering.py`, `app/render_jobs.py`, `app/store.py`.
- Audio effects and silence analysis: `app/audio_tools.py`, `app/engine.py`, and `app/rendering.py`.
- Optional local speech recognition: `app/transcription.py`.

## External projects

- React: https://react.dev/
- Vite: https://vite.dev/
- FastAPI: https://fastapi.tiangolo.com/
- Pydantic: https://docs.pydantic.dev/
- FFmpeg/FFprobe: https://ffmpeg.org/documentation.html
- faster-whisper (optional local transcription): https://github.com/SYSTRAN/faster-whisper
- Playwright: https://playwright.dev/docs/intro

Dependencies are listed in `requirements.txt`, optional ASR dependencies in `requirements-asr.txt`, and frontend dependencies in `web/package.json` and `web/package-lock.json`.
