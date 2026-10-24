from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .models import Word


class TranscriptionUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _load_model(name: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionUnavailable(
            "Automatic transcription is not installed. Install the optional requirements-asr.txt package and retry."
        ) from exc
    return WhisperModel(name, device=device, compute_type=compute_type)


def transcribe_file(path: str | Path) -> list[Word]:
    model = _load_model(
        os.getenv("INCUBRIX_WHISPER_MODEL", "tiny"),
        os.getenv("INCUBRIX_WHISPER_DEVICE", "cpu"),
        os.getenv("INCUBRIX_WHISPER_COMPUTE_TYPE", "int8"),
    )
    segments, _ = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
    words = []
    for segment in segments:
        for index, word in enumerate(segment.words or []):
            text = word.word.strip()
            if not text:
                continue
            words.append(Word(
                id=f"asr_{len(words) + 1:06d}",
                start_s=max(0, float(word.start)),
                end_s=float(word.end),
                text=text,
                confidence=max(0, min(1, float(word.probability))),
            ))
    return words
