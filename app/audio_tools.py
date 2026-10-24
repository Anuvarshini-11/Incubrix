from __future__ import annotations

import re
import shutil
import subprocess


class AudioAnalysisError(RuntimeError):
    pass


def detect_silence(path: str, duration_s: float, threshold_db: int = -35, minimum_s: float = 0.7) -> list[tuple[float, float]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioAnalysisError("FFmpeg is required for silence detection.")
    command = [
        ffmpeg, "-hide_banner", "-nostats", "-i", path,
        "-af", f"silencedetect=noise={threshold_db}dB:d={minimum_s}",
        "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AudioAnalysisError(result.stderr[-2000:] or "FFmpeg could not analyze this audio.")
    log = result.stderr
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", log)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", log)]
    intervals = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else duration_s
        if start < end:
            intervals.append((max(0, start), min(duration_s, end)))
    return intervals
