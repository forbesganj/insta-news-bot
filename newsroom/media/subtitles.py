"""Word-by-word captions: ASS (burned in by ffmpeg) and SRT (kept for reference)."""
from __future__ import annotations

from pathlib import Path


def _ass_time(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _srt_time(seconds: float) -> str:
    ms = int(round(max(seconds, 0.0) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[tuple[str, float, float]], max_words: int = 3, max_chars: int = 15):
    chunks, current, chars = [], [], 0
    for item in words:
        word = item[0]
        if current and (len(current) >= max_words or chars + len(word) + 1 > max_chars):
            chunks.append(current)
            current, chars = [], 0
        current.append(item)
        chars += len(word) + 1
    if current:
        chunks.append(current)
    return chunks


def build_ass(groups: list[list[tuple[str, float, float]]], width: int = 1080, height: int = 1920,
              font: str = "DejaVu Sans", accent_bgr: str = "00B8FF", margin_v: int = 540,
              font_size: int = 78) -> str:
    """`groups` = one list of absolute (word, start, end) per beat, so a caption never spans two beats.
    Highlights the spoken word in the accent colour."""
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,"
        f"60,60,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    chunks = [chunk for words in groups for chunk in chunk_words(words)]
    for chunk in chunks:
        for i, (word, start, end) in enumerate(chunk):
            nxt = chunk[i + 1][1] if i + 1 < len(chunk) else end
            stop = max(end, nxt) if i + 1 < len(chunk) else end
            parts = []
            for j, (w, _, _) in enumerate(chunk):
                w = _escape(w).upper()
                parts.append(f"{{\\c&H{accent_bgr}&}}{w}{{\\c&HFFFFFF&}}" if j == i else w)
            shrink = "{\\fs60}" if sum(len(w) + 1 for w, _, _ in chunk) > 17 else ""
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(stop)},Default,,0,0,0,,{shrink}{' '.join(parts)}")
    return header + "\n".join(events) + "\n"


def build_srt(groups: list[list[tuple[str, float, float]]]) -> str:
    lines = []
    chunks = [chunk for words in groups for chunk in chunk_words(words, max_words=6, max_chars=42)]
    for index, chunk in enumerate(chunks, start=1):
        start, end = chunk[0][1], chunk[-1][2]
        lines.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{' '.join(w for w, _, _ in chunk)}\n")
    return "\n".join(lines)


def hex_to_bgr(hex_color: str) -> str:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return "00B8FF"
    return value[4:6] + value[2:4] + value[0:2]


def write_subtitles(groups: list[list[tuple[str, float, float]]], directory: Path, accent_hex: str, font: str,
                    width: int, height: int) -> tuple[Path, Path]:
    ass_path = directory / "subs.ass"
    srt_path = directory / "subs.srt"
    ass_path.write_text(build_ass(groups, width, height, font, hex_to_bgr(accent_hex)), encoding="utf-8")
    srt_path.write_text(build_srt(groups), encoding="utf-8")
    return ass_path, srt_path
