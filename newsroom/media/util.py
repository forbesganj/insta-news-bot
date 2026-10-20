"""ffmpeg/ffprobe helpers and font discovery."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


class MediaError(RuntimeError):
    pass


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def find_font(configured: str, bold: bool) -> str | None:
    if configured and Path(configured).exists():
        return configured
    for candidate in (BOLD_CANDIDATES if bold else REGULAR_CANDIDATES):
        if Path(candidate).exists():
            return candidate
    return None


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise MediaError(f"{cmd[0]} failed ({result.returncode}): {result.stderr[-600:]}")
    return result


def probe(path: Path) -> dict:
    result = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(result.stdout)


def probe_duration(path: Path) -> float:
    data = probe(path)
    try:
        return float(data["format"]["duration"])
    except (KeyError, ValueError) as exc:
        raise MediaError(f"cannot read duration of {path}") from exc
