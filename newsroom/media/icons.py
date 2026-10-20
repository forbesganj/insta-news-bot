"""Original vector-style icons and the 'Byte' mascot, drawn with Pillow (no external images, no licences)."""
from __future__ import annotations

import re

from PIL import Image, ImageDraw, ImageFont

ICON_NAMES = ["plane", "battery", "phone", "rupee", "chart", "chip", "shield", "rocket", "card", "globe", "spark", "shop", "user"]
MOODS = ["shocked", "thinking", "laughing", "tired", "celebrating", "none"]

_KEYWORDS = [
    ("shop", r"shop|store|merchant|retail|kirana|vendor"),
    ("battery", r"power ?bank|battery|charger|charging|smoke|\bfire\b|overheat"),
    ("plane", r"flight|airline|airport|aircraft|plane|indigo|air india|aviation|pilot"),
    ("chip", r"\bchip|gpu|processor|semiconductor|nvidia|\bai\b|openai|model|llm|gemini|chatgpt|neural"),
    ("shield", r"security|hack|cyber|breach|privacy|scam|fraud|encrypt"),
    ("card", r"upi|payment|bank|credit card|debit|rbi|wallet|fintech|loan"),
    ("rupee", r"rupee|\u20b9|crore|lakh|funding|raises|valuation|revenue|price|tax|gst|salary"),
    ("chart", r"stock|market|shares|sensex|nifty|growth|profit|ipo|earnings|inflation"),
    ("rocket", r"launch|startup|rocket|isro|space|satellite|unveil"),
    ("phone", r"phone|iphone|android|smartphone|app\b|samsung|whatsapp|instagram|update"),
    ("globe", r"global|world|india|country|government|policy|law"),
]


def pick_icon(text: str, preferred: str | None = None) -> str:
    if preferred in ICON_NAMES:
        return str(preferred)
    lowered = (text or "").lower()
    for name, pattern in _KEYWORDS:
        if re.search(pattern, lowered):
            return name
    return "spark"


def _poly(draw: ImageDraw.ImageDraw, pts, s: float, fill) -> None:  # noqa: ANN001
    draw.polygon([(x * s, y * s) for x, y in pts], fill=fill)


def icon_image(name: str, size: int, main: tuple, accent: tuple, font_path: str | None = None) -> Image.Image:
    k = 3                                   # supersample for smooth edges
    s = size * k
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = max(int(s * 0.045), 3)
    if name == "plane":
        pts = [(0.5, 0.0), (0.56, 0.25), (1.0, 0.62), (1.0, 0.72), (0.56, 0.58), (0.54, 0.82), (0.72, 0.92),
               (0.72, 1.0), (0.5, 0.95), (0.28, 1.0), (0.28, 0.92), (0.46, 0.82), (0.44, 0.58), (0.0, 0.72),
               (0.0, 0.62), (0.44, 0.25)]
        pts = [(0.12 + 0.76 * x, 0.12 + 0.76 * y) for x, y in pts]
        _poly(d, pts, s, main)
        img = img.rotate(-40, resample=Image.BICUBIC, expand=False)
    elif name == "battery":
        d.rounded_rectangle((0.12 * s, 0.30 * s, 0.82 * s, 0.70 * s), radius=0.07 * s, outline=main, width=w)
        d.rounded_rectangle((0.84 * s, 0.42 * s, 0.92 * s, 0.58 * s), radius=0.02 * s, fill=main)
        _poly(d, [(0.52, 0.34), (0.34, 0.54), (0.46, 0.54), (0.40, 0.66), (0.62, 0.45), (0.50, 0.45)], s, accent)
    elif name == "phone":
        d.rounded_rectangle((0.27 * s, 0.05 * s, 0.73 * s, 0.95 * s), radius=0.09 * s, outline=main, width=w)
        d.rounded_rectangle((0.42 * s, 0.10 * s, 0.58 * s, 0.13 * s), radius=0.01 * s, fill=main)
        d.rounded_rectangle((0.33 * s, 0.20 * s, 0.67 * s, 0.78 * s), radius=0.03 * s, fill=accent)
        d.ellipse((0.46 * s, 0.84 * s, 0.54 * s, 0.92 * s), outline=main, width=max(w // 2, 2))
    elif name == "rupee":
        d.ellipse((0.04 * s, 0.04 * s, 0.96 * s, 0.96 * s), fill=accent, outline=main, width=w)
        try:
            font = ImageFont.truetype(font_path, int(0.62 * s)) if font_path else ImageFont.load_default()
        except OSError:
            font = ImageFont.load_default()
        d.text((0.5 * s, 0.5 * s), "\u20b9", font=font, fill=(15, 20, 35), anchor="mm")
    elif name == "chart":
        for i, h in enumerate((0.30, 0.48, 0.70)):
            x0 = (0.12 + i * 0.26) * s
            d.rounded_rectangle((x0, (0.92 - h) * s, x0 + 0.18 * s, 0.92 * s), radius=0.02 * s, fill=accent if i == 2 else main)
        d.line([(0.08 * s, 0.50 * s), (0.36 * s, 0.34 * s), (0.58 * s, 0.42 * s), (0.88 * s, 0.10 * s)], fill=main, width=w)
        _poly(d, [(0.95, 0.04), (0.74, 0.10), (0.90, 0.26)], s, main)
    elif name == "chip":
        for i in range(4):
            p = (0.28 + i * 0.15) * s
            d.line([(p, 0.10 * s), (p, 0.24 * s)], fill=main, width=w)
            d.line([(p, 0.76 * s), (p, 0.90 * s)], fill=main, width=w)
            d.line([(0.10 * s, p), (0.24 * s, p)], fill=main, width=w)
            d.line([(0.76 * s, p), (0.90 * s, p)], fill=main, width=w)
        d.rounded_rectangle((0.22 * s, 0.22 * s, 0.78 * s, 0.78 * s), radius=0.06 * s, outline=main, width=w)
        d.rounded_rectangle((0.36 * s, 0.36 * s, 0.64 * s, 0.64 * s), radius=0.03 * s, fill=accent)
    elif name == "shield":
        _poly(d, [(0.5, 0.04), (0.9, 0.2), (0.86, 0.58), (0.5, 0.96), (0.14, 0.58), (0.1, 0.2)], s, accent)
        d.line([(0.32 * s, 0.50 * s), (0.45 * s, 0.64 * s), (0.70 * s, 0.34 * s)], fill=(15, 20, 35), width=int(w * 1.6))
    elif name == "rocket":
        _poly(d, [(0.5, 0.02), (0.68, 0.28), (0.66, 0.66), (0.34, 0.66), (0.32, 0.28)], s, main)
        _poly(d, [(0.34, 0.50), (0.12, 0.74), (0.34, 0.70)], s, accent)
        _poly(d, [(0.66, 0.50), (0.88, 0.74), (0.66, 0.70)], s, accent)
        d.ellipse((0.42 * s, 0.26 * s, 0.58 * s, 0.42 * s), fill=accent)
        _poly(d, [(0.40, 0.68), (0.60, 0.68), (0.50, 0.98)], s, accent)
    elif name == "card":
        d.rounded_rectangle((0.05 * s, 0.20 * s, 0.95 * s, 0.80 * s), radius=0.07 * s, outline=main, width=w)
        d.rectangle((0.05 * s, 0.34 * s, 0.95 * s, 0.46 * s), fill=main)
        d.rounded_rectangle((0.14 * s, 0.58 * s, 0.34 * s, 0.70 * s), radius=0.02 * s, fill=accent)
    elif name == "globe":
        d.ellipse((0.06 * s, 0.06 * s, 0.94 * s, 0.94 * s), outline=main, width=w)
        d.ellipse((0.30 * s, 0.06 * s, 0.70 * s, 0.94 * s), outline=main, width=max(w // 2, 2))
        d.line([(0.06 * s, 0.5 * s), (0.94 * s, 0.5 * s)], fill=main, width=max(w // 2, 2))
        d.arc((0.06 * s, 0.22 * s, 0.94 * s, 0.78 * s), 0, 360, fill=accent, width=max(w // 2, 2))
    elif name == "shop":
        d.rectangle((0.12 * s, 0.42 * s, 0.88 * s, 0.92 * s), outline=main, width=w)
        for i in range(5):
            x0 = (0.08 + i * 0.168) * s
            d.polygon([(x0, 0.18 * s), (x0 + 0.168 * s, 0.18 * s), (x0 + 0.14 * s, 0.42 * s), (x0 - 0.02 * s, 0.42 * s)],
                      fill=accent if i % 2 == 0 else main)
        d.rectangle((0.40 * s, 0.60 * s, 0.60 * s, 0.92 * s), fill=accent)
    elif name == "user":
        d.ellipse((0.32 * s, 0.10 * s, 0.68 * s, 0.46 * s), fill=main)
        d.pieslice((0.14 * s, 0.52 * s, 0.86 * s, 1.28 * s), 180, 360, fill=accent)
    else:  # spark
        _poly(d, [(0.5, 0.0), (0.60, 0.40), (1.0, 0.5), (0.60, 0.60), (0.5, 1.0), (0.40, 0.60), (0.0, 0.5), (0.40, 0.40)], s, accent)
    return img.resize((size, size), Image.LANCZOS)


def mascot_image(mood: str, size: int, accent: tuple) -> Image.Image:
    """'Byte', an original friendly robot with five expressions."""
    k = 3
    s = size * k
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    body, screen, dark = (236, 242, 252), (14, 20, 36), (14, 20, 36)
    d.line([(0.5 * s, 0.20 * s), (0.5 * s, 0.07 * s)], fill=body, width=int(0.03 * s))
    d.ellipse((0.455 * s, 0.025 * s, 0.545 * s, 0.115 * s), fill=accent)
    d.rounded_rectangle((0.01 * s, 0.40 * s, 0.10 * s, 0.64 * s), radius=0.03 * s, fill=accent)
    d.rounded_rectangle((0.90 * s, 0.40 * s, 0.99 * s, 0.64 * s), radius=0.03 * s, fill=accent)
    d.rounded_rectangle((0.08 * s, 0.18 * s, 0.92 * s, 0.86 * s), radius=0.20 * s, fill=body, outline=accent, width=int(0.02 * s))
    d.rounded_rectangle((0.16 * s, 0.28 * s, 0.84 * s, 0.76 * s), radius=0.12 * s, fill=screen)
    ex_l, ex_r, ey, r = 0.36 * s, 0.64 * s, 0.46 * s, 0.075 * s
    glow = accent
    wd = int(0.03 * s)
    if mood == "shocked":
        for cx in (ex_l, ex_r):
            d.ellipse((cx - 1.3 * r, ey - 1.3 * r, cx + 1.3 * r, ey + 1.3 * r), fill=(255, 255, 255))
            d.ellipse((cx - 0.5 * r, ey - 0.5 * r, cx + 0.5 * r, ey + 0.5 * r), fill=dark)
        d.ellipse((0.44 * s, 0.60 * s, 0.56 * s, 0.72 * s), fill=glow)
        d.ellipse((0.47 * s, 0.63 * s, 0.53 * s, 0.69 * s), fill=dark)
    elif mood == "thinking":
        for cx in (ex_l, ex_r):
            d.ellipse((cx - r, ey - r, cx + r, ey + r), fill=(255, 255, 255))
            d.ellipse((cx - 0.2 * r, ey - 0.9 * r, cx + 0.7 * r, ey - 0.1 * r), fill=dark)
        d.line([(ex_r - 1.2 * r, ey - 1.9 * r), (ex_r + 1.1 * r, ey - 1.5 * r)], fill=glow, width=wd)
        d.line([(0.42 * s, 0.67 * s), (0.60 * s, 0.64 * s)], fill=glow, width=wd)
        for i, rr in enumerate((0.018, 0.026, 0.036)):
            cx, cy = (0.80 + i * 0.045) * s, (0.14 - i * 0.04) * s
            d.ellipse((cx - rr * s, cy - rr * s, cx + rr * s, cy + rr * s), fill=accent)
    elif mood == "laughing":
        for cx in (ex_l, ex_r):
            d.arc((cx - 1.2 * r, ey - 0.6 * r, cx + 1.2 * r, ey + 1.4 * r), 200, 340, fill=glow, width=wd)
        d.pieslice((0.34 * s, 0.50 * s, 0.66 * s, 0.76 * s), 0, 180, fill=glow)
        d.pieslice((0.40 * s, 0.62 * s, 0.60 * s, 0.76 * s), 0, 180, fill=(255, 120, 130))
    elif mood == "tired":
        for cx in (ex_l, ex_r):
            d.pieslice((cx - r, ey - r, cx + r, ey + r), 180, 360, fill=(255, 255, 255))
            d.line([(cx - r, ey), (cx + r, ey)], fill=glow, width=wd)
        d.line([(0.40 * s, 0.66 * s), (0.47 * s, 0.63 * s), (0.54 * s, 0.67 * s), (0.60 * s, 0.64 * s)], fill=glow, width=wd)
        d.ellipse((0.80 * s, 0.30 * s, 0.86 * s, 0.40 * s), fill=(120, 190, 255))
    else:  # celebrating
        for cx in (ex_l, ex_r):
            pts = []
            for i in range(8):
                import math
                ang = math.pi / 4 * i - math.pi / 2
                rad = 1.5 * r if i % 2 == 0 else 0.6 * r
                pts.append((cx + rad * math.cos(ang), ey + rad * math.sin(ang)))
            d.polygon(pts, fill=glow)
        d.pieslice((0.33 * s, 0.50 * s, 0.67 * s, 0.76 * s), 0, 180, fill=glow)
        for (x, y, c) in ((0.06, 0.05, (255, 90, 120)), (0.92, 0.10, (90, 200, 255)), (0.02, 0.30, (140, 255, 140)), (0.97, 0.32, (255, 220, 90))):
            d.rectangle((x * s, y * s, (x + 0.04) * s, (y + 0.04) * s), fill=c)
    return img.resize((size, size), Image.LANCZOS)
