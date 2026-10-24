import subprocess

from app.audio_tools import detect_silence


def test_detect_silence_parses_complete_and_trailing_ranges(monkeypatch):
    monkeypatch.setattr('app.audio_tools.shutil.which', lambda _: 'ffmpeg')
    result = subprocess.CompletedProcess(
        ['ffmpeg'], 0, '',
        '[silencedetect] silence_start: 1.25\n'
        '[silencedetect] silence_end: 2.5 | silence_duration: 1.25\n'
        '[silencedetect] silence_start: 8.5\n',
    )
    monkeypatch.setattr('app.audio_tools.subprocess.run', lambda *args, **kwargs: result)
    assert detect_silence('source.mp4', 10) == [(1.25, 2.5), (8.5, 10)]
