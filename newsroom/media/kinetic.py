"""Kinetic-typography Reels (style "kinetic", the v1.2 default).

One idea per screen, hard cuts on the beat, alternating colours, heavy type that slams or pops in, a few
purpose-built graphics (flight route, check mark, smoke, alert pulse, icon), punch-in zoom, shake, film grain,
and an original synthesised beat. No voice-over: the text IS the content.
"""
from __future__ import annotations

import logging
import math
import multiprocessing
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..settings import Settings
from . import music, sound
from .icons import icon_image
from .tts import TTS, voice_status
from .util import MediaError, find_font, have_ffmpeg, probe_duration, run

log = logging.getLogger(__name__)

W, H = 1080, 1920
YEL, BLK, WHT, RED = (255, 212, 0), (11, 11, 12), (255, 255, 255), (255, 59, 48)
PALETTES: dict[str, dict[str, Any]] = {
    "ink":   dict(bg=(11, 11, 12), glow=((190, 20, 20), 540, 1500, 520, 120), base=WHT, accent=YEL, pop=YEL),
    "alert": dict(bg=(11, 11, 12), glow=((190, 20, 20), 540, 1100, 560, 140), base=WHT, accent=RED, pop=RED),
    "sun":   dict(bg=YEL, glow=((255, 240, 150), 300, 500, 600, 110), base=BLK, accent=BLK, pop=(30, 64, 220)),
    "sea":   dict(bg=(10, 42, 102), glow=((40, 110, 220), 760, 600, 600, 110), base=WHT, accent=YEL, pop=YEL),
    "mint":  dict(bg=(14, 176, 118), glow=((120, 255, 200), 540, 760, 620, 100), base=WHT, accent=WHT, pop=WHT),
    "night": dict(bg=BLK, glow=((60, 70, 120), 300, 1300, 560, 110), base=WHT, accent=YEL, pop=YEL),
}
SLAM_KINDS = {"hook", "alert", "twist", "closer", "stat"}
GRAPHICS = {"none", "route", "check", "smoke", "pulse", "counter", "bars", "phone", "flow"}
DATA_GRAPHICS = {"counter", "bars", "phone", "flow"}


# --------------------------------------------------------------------------- timeline
@dataclass
class Scene:
    kind: str
    lines: list[str]
    sub: str = ""
    accent: int = 0
    graphic: str = "none"          # none | route | check | smoke | pulse | icon:<name>
    label: str = ""                # destination for the route graphic
    palette: str = "night"
    start: float = 0.0
    dur: float = 2.2
    line_t: list[float] = field(default_factory=list)
    sub_t: float = 0.0
    slam: list[bool] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)   # payload for counter / bars / phone / flow graphics
    src: int = 0                   # index in the original scene list (maps a scene to its narration clip)
    lead: float = 0.0              # seconds of silence before the narration starts

    @property
    def end(self) -> float:
        return self.start + self.dur


def _scene_from_dict(raw: dict[str, Any]) -> Scene:
    lines = [str(l).strip() for l in raw.get("lines", []) if str(l).strip()][:4]
    if not lines:
        raise MediaError("scene has no lines")
    graphic = str(raw.get("graphic", "none") or "none").lower()
    if graphic not in GRAPHICS and not graphic.startswith("icon:"):
        graphic = "none"
    accent = int(raw.get("accent", 0) or 0)
    return Scene(kind=str(raw.get("kind", "detail")), lines=lines, sub=str(raw.get("sub", "") or "").strip(),
                 accent=accent if 0 <= accent < len(lines) else 0, graphic=graphic,
                 label=str(raw.get("label", "") or "").strip().upper(),
                 data=dict(raw.get("data") or {}))


def assign_palettes(scenes: list[Scene]) -> None:
    cycle, ci, prev = ["sun", "sea", "night"], 0, None
    for sc in scenes:
        if sc.kind == "hook":
            p = "ink"
        elif sc.kind == "closer":
            p = "sun" if prev != "sun" else "ink"
        elif sc.kind == "alert":
            p = "alert"
        elif sc.kind == "relief":
            p = "mint"
        else:
            p = cycle[ci % 3]
            ci += 1
            if p == prev:
                p = cycle[ci % 3]
                ci += 1
        sc.palette, prev = p, p


def build_timeline(raw_scenes: list[dict[str, Any]], bpm: float, max_seconds: float = 30.0,
                   audio_durs: list[float] | None = None) -> list[Scene]:
    """With `audio_durs` (narration length per scene) each scene lasts as long as its narration needs."""
    scenes = [_scene_from_dict(r) for r in raw_scenes]
    for i, sc in enumerate(scenes):
        sc.src = i
    assign_palettes(scenes)
    beat = 60.0 / bpm
    t = 0.0
    for sc in scenes:
        words = sum(len(l.split()) for l in sc.lines) + 0.5 * len(sc.sub.split())
        beats = 4 if words <= 4 else 6 if words <= 8 else 8
        if sc.kind == "hook":
            beats = 4 if words <= 8 else 6
        if sc.kind == "closer":
            beats = 8
        if sc.kind == "relief":
            beats = 4 if not sc.sub else 6
        if sc.graphic == "route" or sc.graphic in DATA_GRAPHICS:
            beats = max(beats, 6)
        dur = beats * beat
        stagger = 0.40
        if audio_durs is not None:
            audio = audio_durs[sc.src]
            sc.lead = 0.10 if sc.kind == "hook" else 0.20
            tail = 0.60 if sc.kind == "closer" else 0.30
            half = beat / 2
            dur = max(dur if audio < 1.2 else 0.0, math.ceil((audio + sc.lead + tail) / half) * half, 4 * half)
            stagger = min(0.40, max(0.18, 0.7 * audio / max(len(sc.lines), 1)))
        sc.start, sc.dur = t, dur
        sc.line_t = [round(0.0 + i * stagger, 3) for i in range(len(sc.lines))]
        if sc.kind == "relief":
            sc.line_t = [0.65 + i * stagger for i in range(len(sc.lines))]
        sc.sub_t = (sc.line_t[-1] + (0.55 if audio_durs is None else 0.45)) if sc.sub else 0.0
        sc.slam = [sc.kind == "hook" or (sc.kind in SLAM_KINDS and i == sc.accent) for i in range(len(sc.lines))]
        t += sc.dur
    while t > max_seconds and len(scenes) > 4:            # keep it snappy: drop the least important middle scene
        drop = len(scenes) // 2
        removed = scenes.pop(drop)
        t -= removed.dur
        shift = removed.dur
        for later in scenes[drop:]:
            later.start -= shift
    return scenes


# --------------------------------------------------------------------------- drawing helpers
def ease_out_cubic(x: float) -> float:
    return 1 - (1 - x) ** 3


def ease_out_back(x: float) -> float:
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def bezier(u: float, p0, p1, p2):  # noqa: ANN001, ANN201
    return ((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u ** 2 * p2[0],
            (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u ** 2 * p2[1])


class Renderer:
    P0, P1, P2 = (-80, 1500), (420, 800), (880, 1060)

    def __init__(self, settings: Settings, scenes: list[Scene], source_line: str, bpm: float) -> None:
        self.s = settings
        self.scenes = scenes
        self.source_line = source_line
        self.bpm = bpm
        self.total = scenes[-1].end
        configured = str(settings.get("media.font_bold", ""))
        bundled = str(settings.root / "assets" / "fonts" / "Poppins-Bold.ttf")
        self.font_path = configured if configured and Path(configured).exists() else (
            bundled if Path(bundled).exists() else find_font("", True))
        if not self.font_path:
            raise MediaError("no bold TrueType font found (bundle assets/fonts/Poppins-Bold.ttf)")
        self.handle = str(settings.get("brand.handle", ""))
        self._sprites: dict[tuple, Image.Image] = {}
        self.bgs = [self._background(sc.palette) for sc in scenes]
        self.noise = [np.random.default_rng(i).normal(0, 4.5, (H, W, 1)).astype(np.int16) for i in range(6)]
        self.plane = self._plane(170)
        self.puff = self._puff()
        rng = np.random.default_rng(7)
        self.particles = [(float(rng.uniform(0, W)), float(rng.uniform(40, 130)), float(rng.uniform(3, 9)),
                           float(rng.uniform(0, 1))) for _ in range(46)]
        self.puffs = [(float(rng.uniform(80, 1000)), float(rng.uniform(150, 330)), float(rng.uniform(0.7, 1.5)),
                       float(rng.uniform(0, 3))) for _ in range(9)]
        self.hits = [sc.start for sc in scenes if sc.kind in ("hook", "alert")]
        self.flashes = [sc.start for sc in scenes if sc.kind == "alert"]

    # ---------------------------------------------------------------- assets
    def _background(self, palette: str) -> Image.Image:
        p = PALETTES[palette]
        base = Image.new("RGBA", (W, H), p["bg"] + (255,))
        color, cx, cy, r, alpha = p["glow"]
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (alpha,))
        base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(r / 2.2)))
        vig = Image.new("L", (W, H), 0)
        ImageDraw.Draw(vig).ellipse((-300, -200, W + 300, H + 200), fill=255)
        vig = vig.filter(ImageFilter.GaussianBlur(260))
        v = np.asarray(vig, dtype=np.float32)[:, :, None] / 255.0
        arr = np.asarray(base.convert("RGB"), dtype=np.float32) * (0.72 + 0.28 * v)    # vignette baked in once
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")

    @staticmethod
    def _plane(size: int) -> Image.Image:
        s = size * 3
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        pts = [(0.5, 0.0), (0.56, 0.25), (1.0, 0.62), (1.0, 0.72), (0.56, 0.58), (0.54, 0.82), (0.72, 0.92),
               (0.72, 1.0), (0.5, 0.95), (0.28, 1.0), (0.28, 0.92), (0.46, 0.82), (0.44, 0.58), (0.0, 0.72),
               (0.0, 0.62), (0.44, 0.25)]
        ImageDraw.Draw(im).polygon([(x * s * 0.9 + s * 0.05, y * s * 0.9 + s * 0.05) for x, y in pts], fill=BLK + (255,))
        return im.resize((size, size), Image.LANCZOS)

    @staticmethod
    def _puff() -> Image.Image:
        im = Image.new("RGBA", (360, 360), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((60, 60, 300, 300), fill=(200, 200, 205, 255))
        return im.filter(ImageFilter.GaussianBlur(46))

    def fit(self, text: str, max_w: int, start: int, min_size: int = 44) -> int:
        size = start
        while size > min_size:
            if ImageFont.truetype(self.font_path, size).getlength(text) <= max_w:
                return size
            size -= 4
        return min_size

    def sprite(self, text: str, size: int, color: tuple) -> Image.Image:
        key = (text, size, color)
        if key not in self._sprites:
            f = ImageFont.truetype(self.font_path, size)
            sw = max(2, int(size * 0.035))                     # thickens Poppins Bold into a heavier face
            wpx, hpx = int(f.getlength(text)) + 4 * sw + 20, int(size * 1.5)
            im = Image.new("RGBA", (wpx, hpx), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((wpx / 2, hpx / 2), text, font=f, fill=color, anchor="mm", stroke_width=sw,
                                    stroke_fill=color)
            self._sprites[key] = im
        return self._sprites[key]

    def put(self, canvas: Image.Image, text: str, size: int, cy: float, t: float, t_in: float, color: tuple,
            slam: bool = False, alpha_mul: float = 1.0, cx: float = W / 2) -> None:
        if t < t_in:
            return
        dt = t - t_in
        sp = self.sprite(text, size, color)
        if slam:
            k = min(dt / 0.14, 1.0)
            s = 1.0 + (0.28 if t_in > 0 else 0.05) * (1 - ease_out_cubic(k))
            a = 1.0 if (t_in == 0 or k > 0.25) else k / 0.25
        else:
            k = min(dt / 0.22, 1.0)
            s = 0.80 + 0.20 * ease_out_back(k)
            a = min(dt / 0.08, 1.0)
        a *= alpha_mul
        w2, h2 = max(int(sp.width * s), 1), max(int(sp.height * s), 1)
        img = sp.resize((w2, h2), Image.BILINEAR) if abs(s - 1) > 0.003 else sp
        if a < 0.999:
            r, g, b, al = img.split()
            img = Image.merge("RGBA", (r, g, b, al.point(lambda v: int(v * a))))
        canvas.paste(img, (int(cx - w2 / 2), int(cy - h2 / 2)), img)

    # ------------------------------------------------------------------ layout
    def layout(self, sc: Scene, zone: str) -> tuple[list[tuple[str, int, float]], float | None]:
        """Returns [(text, size, centre_y)] for the big lines and the y for the sub line.
        zone: "top" (graphic below the text), "bottom" (graphic above the text) or "" (text only)."""
        avail, cy = {"top": (520, 480), "top_small": (420, 430), "bottom": (380, 1250)}.get(zone, (900, 930))
        sizes = []
        for i, line in enumerate(sc.lines):
            start = 300 if i == sc.accent else 170
            if len(sc.lines) == 1:
                start = 280
            max_w = 860 if sc.slam[i] else 940
            sizes.append(self.fit(line.upper(), max_w, start))
        total = sum(int(sz * 1.2) for sz in sizes)
        if total > avail:
            f = avail / total
            sizes = [max(44, int(sz * f)) for sz in sizes]
            total = sum(int(sz * 1.2) for sz in sizes)
        sub_gap = 110 if sc.sub else 0
        y = cy - (total + sub_gap) / 2
        out = []
        for line, sz in zip(sc.lines, sizes):
            out.append((line.upper(), sz, y + sz * 0.6))
            y += int(sz * 1.2)
        return out, (y + 20 + 40) if sc.sub else None

    # --------------------------------------------------------------- graphics
    def g_route(self, canvas: Image.Image, t: float, label: str, base: tuple) -> None:
        d = ImageDraw.Draw(canvas)
        prog = min(max((t - 0.35) / 2.0, 0.0), 1.0)
        pe = 1 - (1 - prog) ** 2
        for i in range(int(pe * 60)):
            x, y = bezier(i / 60, self.P0, self.P1, self.P2)
            d.ellipse((x - 7, y - 7, x + 7, y + 7), fill=base)
        px, py = self.P2
        d.ellipse((px - 20, py - 20, px + 20, py + 20), fill=base)
        if prog >= 1.0:
            r = 30 + 60 * ((t - 2.35) % 1.0)
            d.ellipse((px - r, py - r, px + r, py + r), outline=base, width=6)
        ux = min(pe, 0.94)
        x, y = bezier(ux, self.P0, self.P1, self.P2)
        x2, y2 = bezier(min(ux + 0.01, 1.0), self.P0, self.P1, self.P2)
        ang = math.degrees(math.atan2(x2 - x, -(y2 - y))) if (x2, y2) != (x, y) else 90
        rot = self.plane.rotate(-ang, resample=Image.BICUBIC, expand=True)
        canvas.paste(rot, (int(x - rot.width / 2), int(y - rot.height / 2)), rot)
        if label and t > 2.1:
            size = self.fit(label, 380, 70)
            self.put(canvas, label, size, 1175, t, 2.1, base, slam=True, cx=800)

    def g_smoke(self, canvas: Image.Image, t: float) -> None:
        for x0, speed, sc, ph in self.puffs:
            y = 1750 - ((t * speed + ph * 220) % 1500)
            a = 0.30 * (1 - abs((y - 900) / 900))
            if a <= 0.02:
                continue
            sz = int(360 * sc)
            tile = self.puff.resize((sz, sz), Image.BILINEAR)
            r, g, b, al = tile.split()
            tile = Image.merge("RGBA", (r, g, b, al.point(lambda v: int(v * a))))
            canvas.paste(tile, (int(x0 - sz / 2 + 30 * math.sin(t * 0.9 + ph)), int(y - sz / 2)), tile)

    def g_pulse(self, canvas: Image.Image, t: float) -> None:
        d = ImageDraw.Draw(canvas)
        for k in range(3):
            u = ((t * 0.9) + k / 3) % 1.0
            r = 80 + 520 * u
            alpha = int(150 * (1 - u))
            ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse((540 - r, 1000 - r, 540 + r, 1000 + r), outline=RED + (alpha,), width=10)
            canvas.alpha_composite(ring)

    def g_check(self, canvas: Image.Image, t: float) -> None:
        d = ImageDraw.Draw(canvas)
        cx, cy, r = 540, 800, 250
        p = min(t / 0.55, 1.0)
        d.arc((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * ease_out_cubic(p), fill=WHT, width=30)
        q = min(max((t - 0.45) / 0.45, 0.0), 1.0)
        a, b, c = (cx - 120, cy + 10), (cx - 30, cy + 105), (cx + 135, cy - 85)
        if q > 0:
            seg1 = min(q / 0.45, 1.0)
            mid = (a[0] + (b[0] - a[0]) * seg1, a[1] + (b[1] - a[1]) * seg1)
            d.line([a, mid], fill=WHT, width=40, joint="curve")
            d.ellipse((a[0] - 20, a[1] - 20, a[0] + 20, a[1] + 20), fill=WHT)
            if q > 0.45:
                seg2 = (q - 0.45) / 0.55
                end = (b[0] + (c[0] - b[0]) * seg2, b[1] + (c[1] - b[1]) * seg2)
                d.line([b, end], fill=WHT, width=40, joint="curve")
                d.ellipse((end[0] - 20, end[1] - 20, end[0] + 20, end[1] + 20), fill=WHT)

    def g_icon(self, canvas: Image.Image, t: float, name: str, base: tuple, accent: tuple) -> None:
        tile = icon_image(name, 420, base, accent, self.font_path)
        bob = 10 * math.sin(2 * math.pi * t * 0.8)
        k = min(t / 0.25, 1.0)
        scale = 0.8 + 0.2 * ease_out_back(k)
        tile = tile.resize((max(int(420 * scale), 1), max(int(420 * scale), 1)), Image.BILINEAR)
        canvas.paste(tile, (int(540 - tile.width / 2), int(1180 - tile.height / 2 + bob)), tile)

    # ---------------------------------------------------------------- background fx
    def fx(self, canvas: Image.Image, t: float, palette: str) -> None:
        """Depth: drifting particles on dark scenes, a slowly moving grid on light ones."""
        if palette in ("sun",):
            d = ImageDraw.Draw(canvas)
            off = (t * 30) % 120
            for k in range(-1, 18):
                y = int(k * 120 + off)
                d.line([(0, y), (W, y)], fill=(0, 0, 0, 22), width=2)
            for k in range(0, 10):
                d.line([(k * 120, 0), (k * 120, H)], fill=(0, 0, 0, 16), width=2)
            return
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for x0, sp, r, ph in self.particles:
            y = (H + 100) - ((t * sp + ph * 900) % (H + 200))
            x = x0 + 26 * math.sin(t * 0.7 + ph * 6)
            a = int(70 * (0.5 + 0.5 * math.sin(t * 1.3 + ph * 9)))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, a))
        canvas.alpha_composite(layer)

    # ------------------------------------------------------------ data graphics
    @staticmethod
    def _fmt(value: float, decimals: int) -> str:
        return f"{value:,.{decimals}f}" if decimals else f"{int(round(value)):,}"

    def g_counter(self, canvas: Image.Image, t: float, data: dict, base: tuple, accent: tuple) -> None:
        value = float(data.get("value", 0))
        decimals = int(data.get("decimals", 0 if float(value).is_integer() else 1))
        k = ease_out_cubic(min(max((t - 0.25) / 1.7, 0.0), 1.0))
        shown = value * k
        prefix, suffix = str(data.get("prefix", "")), str(data.get("suffix", ""))
        text = f"{prefix}{self._fmt(shown, decimals)}{suffix}"
        cx, cy = 540, 1010
        ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        r = 330 + 18 * math.sin(t * 3)
        ImageDraw.Draw(ring).ellipse((cx - r, cy - r, cx + r, cy + r), outline=accent + (110,), width=10)
        canvas.alpha_composite(ring)
        size = self.fit(f"{prefix}{self._fmt(value, decimals)}{suffix}", 780, 330)
        self.put(canvas, text, size, cy, t, 0.0, accent if accent != base else base)
        unit = str(data.get("unit", "")).upper()
        if unit:
            self.put(canvas, unit, self.fit(unit, 860, 84), cy + size * 0.72, t, 0.5, base, alpha_mul=0.95)

    def g_bars(self, canvas: Image.Image, t: float, data: dict, base: tuple, accent: tuple) -> None:
        values = [float(v) for v in data.get("values", [])][:6]
        labels = [str(l).upper() for l in data.get("labels", [])][:6]
        if not values:
            return
        d = ImageDraw.Draw(canvas)
        n, top, base_y, area_w = len(values), max(values), 1420, 800
        gap = 36
        bw = int((area_w - gap * (n - 1)) / n)
        x0 = int((W - (bw * n + gap * (n - 1))) / 2)
        d.rounded_rectangle((x0 - 20, base_y + 6, x0 + area_w + 20, base_y + 14), radius=4, fill=base + (150,))
        for i, v in enumerate(values):
            k = ease_out_back(min(max((t - 0.25 - i * 0.28) / 0.8, 0.0), 1.0))
            h = int(max(top, 1e-9) and (v / top) * 560 * max(k, 0))
            x = x0 + i * (bw + gap)
            last = i == n - 1
            d.rounded_rectangle((x, base_y - h, x + bw, base_y), radius=18, fill=(accent if last else base) + (255,))
            if k > 0.8:
                label_val = self._fmt(v * min(k, 1), int(data.get("decimals", 0 if float(v).is_integer() else 1)))
                self.put(canvas, label_val, 64, base_y - h - 52, t, 0.0, accent if last else base, cx=x + bw / 2)
            if i < len(labels):
                self.put(canvas, labels[i], 46, base_y + 56, t, 0.2 + i * 0.28, base, alpha_mul=0.9, cx=x + bw / 2)

    def g_flow(self, canvas: Image.Image, t: float, data: dict, base: tuple, accent: tuple) -> None:
        nodes = (data.get("nodes") or [{"icon": "user", "label": "YOU"}, {"icon": "phone", "label": "UPI"},
                                       {"icon": "shop", "label": "SHOP"}])[:3]
        n = len(nodes)
        xs = [int(W * (i + 0.5) / n) for i in range(n)]
        y = 1100
        d = ImageDraw.Draw(canvas)
        for i in range(n - 1):
            a, b = xs[i] + 135, xs[i + 1] - 135
            d.line([(a, y), (b, y)], fill=base + (120,), width=8)
            d.polygon([(b, y), (b - 28, y - 20), (b - 28, y + 20)], fill=base + (200,))
            for k in range(3):                               # coins travelling along the arrow
                u = ((t * 0.9) + k / 3 + i * 0.17) % 1.0
                cx = a + (b - 28 - a) * u
                d.ellipse((cx - 14, y - 14, cx + 14, y + 14), fill=accent + (255,))
        for i, node in enumerate(nodes):
            k = ease_out_back(min(max((t - 0.15 - i * 0.25) / 0.5, 0.0), 1.0))
            size = max(int(230 * k), 1)
            tile = icon_image(str(node.get("icon", "spark")), size, base, accent, self.font_path)
            canvas.paste(tile, (xs[i] - size // 2, y - size // 2), tile)
            label = str(node.get("label", "")).upper()
            if label:
                self.put(canvas, label, 52, y + 175, t, 0.3 + i * 0.25, base, cx=xs[i])

    def g_phone(self, canvas: Image.Image, t: float, data: dict, base: tuple, accent: tuple) -> None:
        w, h = 470, 800
        cx, cy = 540, 1110 + 8 * math.sin(t * 2.0)
        x0, y0 = int(cx - w / 2), int(cy - h / 2)
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle((x0 - 16, y0 - 16, x0 + w + 16, y0 + h + 16), radius=70, fill=(20, 22, 30, 255), outline=base, width=7)
        d.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=56, fill=(24, 30, 48, 255))
        d.rounded_rectangle((cx - 60, y0 + 16, cx + 60, y0 + 34), radius=9, fill=(8, 9, 14, 255))
        amount = str(data.get("amount", "") or "")
        self.put(canvas, "SENDING", 40, y0 + 130, t, 0.1, (170, 180, 210), alpha_mul=0.9)
        if amount:
            self.put(canvas, amount, self.fit(amount, 380, 130), y0 + 240, t, 0.2, WHT)
        p = min(max((t - 1.0) / 0.6, 0.0), 1.0)
        gcx, gcy, r = cx, y0 + 470, 110
        d.ellipse((gcx - r, gcy - r, gcx + r, gcy + r), outline=(40, 200, 130, 255), width=12) if p > 0 else None
        if p > 0:
            d.arc((gcx - r, gcy - r, gcx + r, gcy + r), -90, -90 + 360 * ease_out_cubic(p), fill=(40, 200, 130, 255), width=12)
        q = min(max((t - 1.35) / 0.4, 0.0), 1.0)
        if q > 0:
            a, b, c = (gcx - 48, gcy + 4), (gcx - 12, gcy + 42), (gcx + 52, gcy - 36)
            m = (a[0] + (b[0] - a[0]) * min(q / 0.5, 1), a[1] + (b[1] - a[1]) * min(q / 0.5, 1))
            d.line([a, m], fill=(40, 200, 130, 255), width=20, joint="curve")
            if q > 0.5:
                e = (b[0] + (c[0] - b[0]) * ((q - 0.5) / 0.5), b[1] + (c[1] - b[1]) * ((q - 0.5) / 0.5))
                d.line([b, e], fill=(40, 200, 130, 255), width=20, joint="curve")
        status = str(data.get("status", "PAYMENT SENT")).upper()
        self.put(canvas, status, self.fit(status, 400, 52), y0 + 640, t, 1.7, (40, 200, 130))

    # ------------------------------------------------------------------ frame
    def frame(self, i: int, fps: int = 30) -> np.ndarray:
        t = min(i / fps, self.total - 1e-3)
        idx = max(k for k, sc in enumerate(self.scenes) if sc.start <= t + 1e-9)
        sc = self.scenes[idx]
        ts, pal = t - sc.start, PALETTES[sc.palette]
        base, accent, pop = pal["base"], pal["accent"], pal["pop"]
        canvas = self.bgs[idx].copy()
        self.fx(canvas, ts, sc.palette)
        graphic = sc.graphic
        if graphic == "smoke":
            self.g_smoke(canvas, ts)
        elif graphic == "pulse":
            self.g_pulse(canvas, ts)
        elif graphic == "check":
            self.g_check(canvas, ts)
        elif graphic == "route":
            self.g_route(canvas, ts, sc.label, base)
        elif graphic.startswith("icon:"):
            self.g_icon(canvas, ts, graphic.split(":", 1)[1], base, accent)
        elif graphic == "counter":
            self.g_counter(canvas, ts, sc.data, base, pop)
        elif graphic == "bars":
            self.g_bars(canvas, ts, sc.data, base, pop)
        elif graphic == "flow":
            self.g_flow(canvas, ts, sc.data, base, pop)
        elif graphic == "phone":
            self.g_phone(canvas, ts, sc.data, base, accent)
        zone = ("top" if (graphic in ("route", "flow", "bars", "counter") or graphic.startswith("icon:"))
                else "top_small" if graphic == "phone" else ("bottom" if graphic == "check" else ""))
        lines, sub_y = self.layout(sc, zone)
        for n, (text, size, cy) in enumerate(lines):
            color = accent if (n == sc.accent and accent != base) else base
            self.put(canvas, text, size, cy, ts, sc.line_t[n], color, slam=sc.slam[n])
        if sc.sub and sub_y is not None:
            self.put(canvas, sc.sub, self.fit(sc.sub, 900, 80), sub_y, ts, sc.sub_t, base, alpha_mul=0.92)
        if sc.kind == "closer":
            if self.source_line:
                self.put(canvas, self.source_line, self.fit(self.source_line, 900, 38), 1540, ts, 1.0, base, alpha_mul=0.65)
            if self.handle:
                self.put(canvas, self.handle, 46, 1600, ts, 1.0, base, alpha_mul=0.85)
        # punch-in zoom + shake on hits
        zoom = 1.0 + 0.045 * (ts / sc.dur)
        dx = dy = 0.0
        for h in self.hits:
            dtt = t - h
            if 0 <= dtt < 0.3:
                amp = 16 * math.exp(-dtt * 14)
                dx += amp * math.sin(dtt * 95)
                dy += amp * math.cos(dtt * 80)
        img = canvas.convert("RGB").transform((W, H), Image.AFFINE, (1 / zoom, 0, (W - W / zoom) / 2 + dx, 0,
                                              1 / zoom, (H - H / zoom) / 2 + dy), resample=Image.BILINEAR)
        arr = np.asarray(img, dtype=np.int16) + self.noise[i % 6]
        for h in self.flashes:
            dtt = t - h
            if 0 <= dtt < 0.10:
                f = 0.5 * (1 - dtt / 0.10)
                arr = (arr * (1 - f) + 255 * f).astype(np.int16)
        return np.clip(arr, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- parallel encode
_ACTIVE: dict[str, Any] = {}


def _job(i: int) -> tuple[int, bytes]:
    return i, _ACTIVE["renderer"].frame(i, _ACTIVE["fps"]).tobytes()


def encode_frames(renderer: Renderer, out: Path, fps: int, workers: int) -> int:
    n = int(round(renderer.total * fps))
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                             "-crf", "24", "-maxrate", "7M", "-bufsize", "14M", "-pix_fmt", "yuv420p", str(out)],
                            stdin=subprocess.PIPE)
    try:
        if workers > 1 and "fork" in multiprocessing.get_all_start_methods():
            _ACTIVE.update(renderer=renderer, fps=fps)
            with multiprocessing.get_context("fork").Pool(workers) as pool:
                for _, data in pool.imap(_job, range(n), chunksize=6):
                    proc.stdin.write(data)
        else:
            for i in range(n):
                proc.stdin.write(renderer.frame(i, fps).tobytes())
    finally:
        proc.stdin.close()
        code = proc.wait()
    if code != 0:
        raise MediaError(f"ffmpeg encoder exited with {code}")
    return n


@dataclass
class KineticResult:
    video: Path
    duration: float
    music: str
    scenes: int
    voiced: bool = False
    voice_provider: str = "none"


def _narrate(raw_scenes: list[dict[str, Any]], settings: Settings, work_dir: Path):  # noqa: ANN202
    """Synthesise one narration clip per scene. Returns (audios, providers) or None (then the Reel is text-only)."""
    enabled, reason = voice_status(settings)
    if not enabled:
        log.info("no voice-over: %s", reason)
        return None
    texts = [str(r.get("say") or "").strip() for r in raw_scenes]
    if not all(texts):
        log.warning("some scenes have no narration; rendering text-only")
        return None
    tts = TTS(settings)
    basic_ok = str(settings.get("voice.mode", "auto")).lower() == "on" or bool(settings.get("voice.allow_basic", False))
    audios = []
    for i, text in enumerate(texts):
        audio = tts.synthesize(text, work_dir / f"voice{i}.mp3", previous=texts[i - 1] if i else None,
                               next_=texts[i + 1] if i + 1 < len(texts) else None, premium_only=not basic_ok)
        if audio.path is None:
            log.warning("voice synthesis failed for scene %d; rendering text-only", i)
            return None
        audios.append(audio)
    return audios, sorted(tts.used)


def _voice_track(scenes: list[Scene], audios: list[Any], work_dir: Path) -> Path:
    inputs: list[str] = []
    filters: list[str] = []
    for k, sc in enumerate(scenes):
        inputs += ["-i", str(audios[sc.src].path)]
        lead = int(sc.lead * 1000)
        filters.append(f"[{k}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={lead}|{lead},"
                       f"apad=whole_dur={sc.dur:.3f}[a{k}]")
    graph = ";".join(filters) + ";" + "".join(f"[a{k}]" for k in range(len(scenes))) + f"concat=n={len(scenes)}:v=0:a=1[out]"
    out = work_dir / "voice_track.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]",
         "-c:a", "pcm_s16le", str(out)], cwd=work_dir)
    return out


def sound_events(scenes: list[Scene]) -> list[tuple[float, str, float]]:
    """Sound effects placed on what the viewer SEES: (time, effect, gain-or-argument)."""
    ev: list[tuple[float, str, float]] = [(0.0, "boom", 1.0)]
    for k, sc in enumerate(scenes):
        if k > 0:
            ev.append((sc.start - 0.35, "whoosh", 0.6))
            if sc.kind in ("alert", "closer"):
                ev.append((sc.start - 1.25, "riser", 0.7))
        for n, t_line in enumerate(sc.line_t):
            t = sc.start + t_line
            if sc.slam[n]:
                if not (k == 0 and n == 0):
                    ev.append((t, "thud", 0.85 if sc.kind in ("hook", "alert", "twist", "stat") else 0.6))
            else:
                ev.append((t + 0.02, "pop", 0.45))
        g = sc.graphic
        if g == "counter":
            steps = 22
            for i in range(steps):
                ev.append((sc.start + 0.25 + i * (1.7 / steps), "tick", i / (steps - 1)))
            ev.append((sc.start + 1.97, "ding", 0.8))
        elif g == "bars":
            nb = len(sc.data.get("values", [])) or 2
            for i in range(nb):
                ev.append((sc.start + 0.25 + i * 0.28, "bwip", 0.7))
            ev.append((sc.start + 0.25 + (nb - 1) * 0.28 + 0.6, "sparkle", 0.5))
        elif g == "phone":
            ev.append((sc.start + 0.95, "click", 0.8))
            ev.append((sc.start + 1.4, "notify", 0.8))
        elif g == "check":
            ev.append((sc.start + 0.55, "chime_up", 0.9))
        elif g == "flow":
            nn = len((sc.data.get("nodes") or [1, 2, 3])[:3])
            for i in range(nn):
                ev.append((sc.start + 0.15 + i * 0.25 + 0.12, "pop", 0.6))
            ev.append((sc.start + 0.15 + (nn - 1) * 0.25 + 0.7, "kaching", 0.6))
        elif g == "route":
            ev.append((sc.start + 0.35, "whoosh", 0.5))
            ev.append((sc.start + 2.1, "pop", 0.7))
        if sc.kind == "alert" or g == "smoke":
            ev.append((sc.start, "rumble", 0.8))
        if sc.kind == "closer":
            ev.append((sc.start + sc.line_t[-1] - 0.45, "stinger", 0.8))
    return ev


def render_kinetic(raw_scenes: list[dict[str, Any]], source_line: str, work_dir: Path, settings: Settings,
                   story_id: str = "story") -> KineticResult:
    if not have_ffmpeg():
        raise MediaError("ffmpeg/ffprobe not found on PATH")
    work_dir.mkdir(parents=True, exist_ok=True)
    cfg = settings.get("kinetic", {}) or {}
    bpm = float(cfg.get("bpm", 108))
    fps = int(cfg.get("fps", settings.get("media.fps", 30)))
    narration = _narrate(raw_scenes, settings, work_dir)
    audio_durs = [a.duration for a in narration[0]] if narration else None
    scenes = build_timeline(raw_scenes, bpm, float(settings.get("content.max_seconds", 30)), audio_durs)
    renderer = Renderer(settings, scenes, source_line, bpm)
    workers = int(os.environ.get("KINETIC_WORKERS") or cfg.get("workers") or min(os.cpu_count() or 1, 4))

    silent_video = work_dir / "kinetic_video.mp4"
    encode_frames(renderer, silent_video, fps, workers)

    track = music.pick_track(settings, story_id)
    mode = str(settings.get("media.music_mode", "auto")).lower()
    with_music = mode != "off" and track is None
    voice_wav = _voice_track(scenes, narration[0], work_dir) if narration else None
    raw = sound.compose(renderer.total, bpm, [(sc.start, sc.end, sc.kind) for sc in scenes], sound_events(scenes),
                        music._seed(story_id), with_music, 0.6 if narration else 1.0, work_dir / "kinetic.raw")
    wav = music.finish_audio(raw, renderer.total, track, work_dir, work_dir / "kinetic.wav",
                             float(settings.get("media.loudness", -14)), voice=voice_wav)
    final = work_dir / "reel.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", silent_video.name, "-i", wav.name, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", final.name],
        cwd=work_dir)
    label = track.name if track else ("synth-beat" if with_music else "sfx-only")
    return KineticResult(final, probe_duration(final), label, len(scenes), bool(narration),
                         ",".join(narration[1]) if narration else "none")
