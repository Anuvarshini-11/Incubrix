from __future__ import annotations

from . import store
from .models import Timeline
from .rendering import RenderError, render


def run_render_job(job_id: str, timeline_data: str, source_path: str, output_path: str, music_path: str | None = None) -> None:
    store.update_render_job(job_id, "running", 5)
    try:
        timeline = Timeline.model_validate_json(timeline_data)
        if music_path:
            render(timeline, source_path, output_path, music_path)
        else:
            render(timeline, source_path, output_path)
    except (RenderError, OSError, ValueError) as exc:
        store.update_render_job(job_id, "failed", 100, str(exc))
        return
    store.update_render_job(job_id, "completed", 100, download_url=f"/api/v1/renders/{job_id}")
