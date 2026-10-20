"""Media quality check before anything is queued for publishing."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageStat

from ..settings import Settings
from .util import MediaError, have_ffmpeg, probe, run


@dataclass
class QAReport:
    passed: bool
    issues: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def check_video(path: Path, settings: Settings, expect_audio: bool, caption: str, source_line: str) -> QAReport:
    cfg = settings.get("qa", {}) or {}
    issues: list[str] = []
    if not have_ffmpeg():
        return QAReport(False, ["ffmpeg/ffprobe not available"])
    if not path.exists():
        return QAReport(False, [f"video file missing: {path}"])
    try:
        data = probe(path)
    except MediaError as exc:
        return QAReport(False, [str(exc)])
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration", 0.0))
    size_mb = path.stat().st_size / 1_000_000
    info = {"duration": round(duration, 2), "size_mb": round(size_mb, 2)}
    if video is None:
        issues.append("no video stream")
    else:
        w, h = int(video.get("width", 0)), int(video.get("height", 0))
        info.update({"width": w, "height": h, "codec": video.get("codec_name")})
        if (w, h) != (int(settings.get("media.width", 1080)), int(settings.get("media.height", 1920))):
            issues.append(f"resolution {w}x{h} is not 9:16 {settings.get('media.width')}x{settings.get('media.height')}")
        if video.get("codec_name") != "h264":
            issues.append(f"video codec {video.get('codec_name')} (need h264)")
        try:
            num, den = str(video.get("r_frame_rate", "30/1")).split("/")
            fps = float(num) / max(float(den), 1.0)
            if not float(cfg.get("min_fps", 23)) <= fps <= 60:
                issues.append(f"frame rate {fps:.1f} outside {cfg.get('min_fps', 23)}-60")
        except ValueError:
            pass
    if audio is None:
        issues.append("no audio stream")
    elif audio.get("codec_name") != "aac":
        issues.append(f"audio codec {audio.get('codec_name')} (need aac)")
    if expect_audio and audio is None:
        issues.append("expected spoken audio but none present")
    lo, hi = float(cfg.get("min_seconds", 12)), float(cfg.get("max_seconds", 60))
    if not lo <= duration <= hi:
        issues.append(f"duration {duration:.1f}s outside {lo:.0f}-{hi:.0f}s")
    if size_mb > float(cfg.get("max_mb", 90)):
        issues.append(f"file is {size_mb:.0f} MB (limit {cfg.get('max_mb', 90)})")
    if "Source:" not in caption:
        issues.append("caption has no 'Source:' attribution")
    if not source_line:
        issues.append("no on-screen source attribution")
    # not-black / not-blank check on three sampled frames
    if video is not None and duration > 0:
        with tempfile.TemporaryDirectory() as tmp:
            for i, frac in enumerate((0.1, 0.5, 0.9)):
                out = Path(tmp) / f"f{i}.png"
                try:
                    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{duration * frac:.2f}", "-i", str(path),
                         "-frames:v", "1", str(out)])
                    stat = ImageStat.Stat(Image.open(out).convert("L"))
                    if stat.mean[0] < float(cfg.get("min_luma", 12)) or stat.stddev[0] < 3:
                        issues.append(f"frame at {int(frac * 100)}% looks blank or black")
                except MediaError as exc:
                    issues.append(f"could not sample frame: {exc}")
                    break
    return QAReport(not issues, issues, info)
