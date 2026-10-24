from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess

import pytest

from app.models import Anchor, Bleep, Caption, Segment, Source, Timeline, Word
from app.rendering import RenderError, _atempo_chain, render
from app.engine import add_caption, caption_output_interval, duration


def make_timeline(has_audio=True):
    return Timeline(
        timeline_id="render-test",
        source=Source(media_id="source", sha256="0" * 64, duration_s=8, has_audio=has_audio),
        segments=[
            Segment(id="first", source_start_s=0, source_end_s=3, speed=2),
            Segment(id="second", source_start_s=5, source_end_s=8, speed=0.5),
        ],
    )


@pytest.mark.parametrize(("speed", "expected"), [(0.25, "atempo=0.5,atempo=0.5"), (0.5, "atempo=0.5"), (1, "atempo=1"), (2, "atempo=2"), (4, "atempo=2,atempo=2")])
def test_audio_tempo_factorization(speed, expected):
    assert _atempo_chain(speed) == expected


def test_render_builds_video_and_audio_segments_atomically(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rendering.shutil.which", lambda _: "ffmpeg")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.rendering.subprocess.run", fake_run)
    output = tmp_path / "output.mp4"
    output.write_bytes(b"previous render")
    manifest = render(make_timeline(), "source.mp4", str(output))

    assert output.read_bytes() == b"rendered"
    assert manifest["expected_duration_s"] == 7.5
    segment_commands = commands[:2]
    assert all("-af" in command for command in segment_commands)
    assert "atempo=2" in segment_commands[0][segment_commands[0].index("-af") + 1]
    assert "atempo=0.5" in segment_commands[1][segment_commands[1].index("-af") + 1]
    assert "volume=1" in segment_commands[0][segment_commands[0].index("-af") + 1]
    assert "trim=duration=3" in segment_commands[0][segment_commands[0].index("-vf") + 1]
    assert "-t" not in segment_commands[0]
    assert "-c:a" in commands[-1] and "aac" in commands[-1]
    assert "-map" in commands[-1] and "0:a:0?" in commands[-1]


def test_failed_render_preserves_previous_output(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rendering.shutil.which", lambda _: "ffmpeg")

    def fail_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "simulated ffmpeg failure")

    monkeypatch.setattr("app.rendering.subprocess.run", fail_run)
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"known-good")
    with pytest.raises(RenderError, match="simulated ffmpeg failure"):
        render(make_timeline(), "source.mp4", str(output))
    assert output.read_bytes() == b"known-good"


def test_silent_source_does_not_force_audio_encoding(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rendering.shutil.which", lambda _: "ffmpeg")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.rendering.subprocess.run", fake_run)
    render(make_timeline(has_audio=False), "silent.mp4", str(tmp_path / "silent-output.mp4"))
    assert all("-af" not in command for command in commands)
    assert "-c:a" not in commands[-1]

def test_muted_timeline_does_not_include_audio_in_render(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rendering.shutil.which", lambda _: "ffmpeg")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.rendering.subprocess.run", fake_run)
    timeline = make_timeline()
    timeline.mute_audio = True
    manifest = render(timeline, "source.mp4", str(tmp_path / "muted.mp4"))

    assert all("-af" not in command and "0:a:0?" not in command and "-c:a" not in command for command in commands)
    assert manifest["audio_muted"] is True

def test_audio_gain_is_applied_and_reported(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rendering.shutil.which", lambda _: "ffmpeg")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.rendering.subprocess.run", fake_run)
    timeline = make_timeline()
    timeline.audio_gain = 0.5
    manifest = render(timeline, "source.mp4", str(tmp_path / "gain-output.mp4"))

    assert all("volume=0.5" in command[command.index("-af") + 1] for command in commands[:2])
    assert manifest["audio_gain"] == 0.5

def test_render_generates_tone_and_censors_audio_for_bleep(monkeypatch,tmp_path):
    monkeypatch.setattr("app.rendering.shutil.which",lambda _:"ffmpeg")
    commands=[]
    def fake_run(command,**kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command,0,"","")
    monkeypatch.setattr("app.rendering.subprocess.run",fake_run)
    timeline=make_timeline()
    timeline.bleeps=[Bleep(id="censor",source_start_s=0.5,source_end_s=1)]
    manifest=render(timeline,"source.mp4",str(tmp_path/"bleep.mp4"))
    output=commands[-1]
    assert any("sine=frequency=1000" in argument for argument in output)
    assert "-filter_complex" in output
    assert "amix=inputs=2" in output[output.index("-filter_complex")+1]
    assert manifest["bleeps"][0]["output_interval_s"]==(0.25,0.5)

def test_audio_gain_must_stay_within_supported_range():
    with pytest.raises(ValueError):
        Timeline(**{**make_timeline().model_dump(), "audio_gain": 1.1})

def test_music_gain_must_stay_within_supported_range():
    with pytest.raises(ValueError):
        Timeline(**{**make_timeline().model_dump(), "music_gain": 1.1})

def test_legacy_timeline_defaults_audio_gain_to_original_level():
    legacy = make_timeline().model_dump(exclude={"audio_gain"})
    assert Timeline(**legacy).audio_gain == 1

def test_manual_caption_interval_maps_to_edited_timeline_time():
    timeline = make_timeline()
    caption = Caption(
        id="manual",
        text="Welcome",
        anchor=Anchor(word_ids=[], source_start_s=1, source_end_s=2),
    )
    assert caption_output_interval(timeline, caption) == (0.5, 1.0)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg/FFprobe integration tools are not installed")
def test_real_ffmpeg_render_keeps_audio_and_speed_duration(tmp_path):
    source_path = tmp_path / "source.mp4"
    subprocess.run([
        shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source_path),
    ], check=True)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    timeline = Timeline(
        timeline_id="real-render",
        source=Source(media_id="real-source", sha256=source_hash, duration_s=4, width=320, height=180, frame_rate="25/1", has_audio=True),
        segments=[Segment(id="fast", source_start_s=0, source_end_s=1, speed=2), Segment(id="slow", source_start_s=2, source_end_s=4, speed=0.5)],
        transcript=[Word(id="word", start_s=2.2, end_s=2.6, text="hello")],
        audio_gain=0.5,
        bleeps=[Bleep(id="censor",source_start_s=2.2,source_end_s=2.6)],
    )
    add_caption(timeline, "Hello", "hello")
    music_path=tmp_path/"music.wav"
    subprocess.run([
        shutil.which("ffmpeg"),"-y","-hide_banner","-loglevel","error",
        "-f","lavfi","-i","sine=frequency=220:sample_rate=48000","-t","2","-c:a","pcm_s16le",str(music_path),
    ],check=True)
    output_path = tmp_path / "result.mp4"
    render(timeline, str(source_path), str(output_path),str(music_path))
    probe = json.loads(subprocess.check_output([
        shutil.which("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output_path),
    ], text=True))
    assert {stream["codec_type"] for stream in probe["streams"]} == {"video", "audio"}
    assert abs(float(probe["format"]["duration"]) - duration(timeline)) < 0.15
    volume = subprocess.run([
        shutil.which("ffmpeg"), "-hide_banner", "-i", str(output_path), "-vn",
        "-af", "volumedetect", "-f", "null", "-",
    ], capture_output=True, text=True, check=True)
    mean_volume = re.search(r"mean_volume:\s*(-?[\d.]+) dB", volume.stderr)
    assert mean_volume is not None and -36 < float(mean_volume.group(1)) < -12
    manifest = json.loads(output_path.with_suffix(".manifest.json").read_text())
    assert manifest["captions_burned_in"] is True
    assert manifest["audio_gain"] == 0.5
    assert manifest["background_music_added"] is True and manifest["music_ducking"] is True