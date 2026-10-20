"""9:16 layers drawn with Pillow. Layout (1080x1920, inside Instagram's safe area):

  y 250-340   progress bar + brand
  y 360-640   visual zone: icon (left) and mascot (right)
  y 690-1180  big on-screen text
  y 1230-1380 spoken captions (burned in by ffmpeg)
  y 1500      source line
"""
from __future__ import annotations

import logging

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..settings import Settings
from .icons import icon_image, mascot_image, pick_icon
from .util import find_font

log = logging.getLogger(__name__)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class Painter:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.w = int(settings.get("media.width", 1080))
        self.h = int(settings.get("media.height", 1920))
        self.bold = find_font(str(settings.get("media.font_bold", "")), True)
        self.regular = find_font(str(settings.get("media.font_regular", "")), False)
        self.accent = _rgb(str(settings.get("brand.accent", "#FFB800")))
        self.text_color = _rgb(str(settings.get("brand.text", "#FFFFFF")))
        self.top = _rgb(str(settings.get("brand.bg_top", "#0B1220")))
        self.bottom = _rgb(str(settings.get("brand.bg_bottom", "#1B2B4B")))
        self.dark = (10, 14, 24)

    # ------------------------------------------------------------------ helpers
    def font(self, size: int, bold: bool = True) -> ImageFont.ImageFont:
        path = self.bold if bold else self.regular
        return ImageFont.truetype(path, size) if path else ImageFont.load_default()

    def wrap(self, draw: ImageDraw.ImageDraw, text: str, max_width: int, max_lines: int, start_size: int,
             min_size: int = 52, bold: bool = True):  # noqa: ANN201
        size = start_size
        while True:
            font = self.font(size, bold)
            lines, current = [], ""
            for word in text.split():
                trial = (current + " " + word).strip()
                if draw.textlength(trial, font=font) <= max_width:
                    current = trial
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
            if len(lines) <= max_lines or size <= min_size:
                return lines[:max_lines], font, size
            size -= 6

    def text_block(self, draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.ImageFont, size: int,
                   y: int, fill: tuple, x: int = 90, center: bool = False, shadow: bool = True) -> int:
        line_height = int(size * 1.16)
        for line in lines:
            width = draw.textlength(line, font=font)
            pos_x = (self.w - width) / 2 if center else x
            if shadow:
                draw.text((pos_x + 4, y + 4), line, font=font, fill=(0, 0, 0))
            draw.text((pos_x, y), line, font=font, fill=fill)
            y += line_height
        return y

    # --------------------------------------------------------------- background
    def background(self, index: int) -> Image.Image:
        """Gradient with an accent glow whose position changes per beat, so consecutive beats look different."""
        gradient = Image.linear_gradient("L").resize((self.w, self.h))
        base = Image.composite(Image.new("RGB", (self.w, self.h), self.bottom),
                               Image.new("RGB", (self.w, self.h), self.top), gradient)
        glow = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        positions = [(0.78, 0.22), (0.20, 0.30), (0.70, 0.55), (0.30, 0.62), (0.80, 0.35)]
        px, py = positions[index % len(positions)]
        gd = ImageDraw.Draw(glow)
        r = 420
        gd.ellipse((px * self.w - r, py * self.h - r, px * self.w + r, py * self.h + r), fill=self.accent + (70,))
        glow = glow.filter(ImageFilter.GaussianBlur(160))
        return Image.alpha_composite(base.convert("RGBA"), glow).convert("RGB")

    # -------------------------------------------------------------------- layer
    def layer(self, beat: dict[str, str], index: int, total: int, source_line: str, broll: bool,
              icon_name: str, mascot_on: bool) -> Image.Image:
        """Transparent RGBA overlay with all text and graphics for one beat."""
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        m = 90
        max_w = self.w - 2 * m
        kind = beat.get("kind", "point")
        screen = (beat.get("screen") or " ".join(beat.get("say", "").split()[:5])).strip()

        if broll:  # darken top and bottom bands + a scrim behind the text so it stays readable on footage
            scrim = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
            sd = ImageDraw.Draw(scrim)
            sd.rectangle((0, 0, self.w, 360), fill=(0, 0, 0, 120))
            sd.rounded_rectangle((m - 30, 660, self.w - m + 30, 1200), radius=40, fill=(6, 10, 20, 150))
            sd.rectangle((0, 1440, self.w, self.h), fill=(0, 0, 0, 130))
            img = Image.alpha_composite(img, scrim)
            draw = ImageDraw.Draw(img)

        # progress bar + brand
        bar_w = self.w - 2 * m
        draw.rounded_rectangle((m, 250, m + bar_w, 262), radius=6, fill=(255, 255, 255, 60))
        draw.rounded_rectangle((m, 250, m + int(bar_w * (index + 1) / total), 262), radius=6, fill=self.accent)
        draw.text((m, 285), str(self.s.get("brand.name", "")).upper(), font=self.font(38), fill=self.accent)

        # visual zone
        if not broll and kind != "cta":
            tile = icon_image(icon_name, 270, (255, 255, 255), self.accent, self.bold)
            img.alpha_composite(tile, (m, 365))
        if mascot_on and beat.get("mood", "none") not in ("none", ""):
            mascot = mascot_image(beat["mood"], 270, self.accent)
            img.alpha_composite(mascot, (self.w - m - 270, 365))
        draw = ImageDraw.Draw(img)

        if kind == "hook":
            draw.rounded_rectangle((m, 660, m + 230, 722), radius=14, fill=self.accent)
            draw.text((m + 26, 668), "NEWS", font=self.font(40), fill=self.dark)
            lines, font, size = self.wrap(draw, screen.upper(), max_w, 3, 128)
            y = self.text_block(draw, lines, font, size, 750, self.text_color)
            draw.rounded_rectangle((m, y + 14, m + 220, y + 26), radius=6, fill=self.accent)
        elif kind == "context":
            draw.text((m, 690), "WHAT HAPPENED", font=self.font(40), fill=self.accent)
            lines, font, size = self.wrap(draw, screen, max_w, 3, 112)
            self.text_block(draw, lines, font, size, 760, self.text_color)
        elif kind == "point":
            n = beat.get("n", str(index))
            draw.ellipse((m, 670, m + 120, 790), fill=self.accent)
            nfont = self.font(78)
            nw = draw.textlength(n, font=nfont)
            draw.text((m + (120 - nw) / 2, 683), n, font=nfont, fill=self.dark)
            lines, font, size = self.wrap(draw, screen.upper(), max_w, 3, 116)
            self.text_block(draw, lines, font, size, 830, self.text_color)
        elif kind == "twist":
            draw.rounded_rectangle((m - 30, 690, self.w - m + 30, 1170), radius=40, fill=self.accent)
            draw.text((m + 10, 725), "WHY IT MATTERS", font=self.font(40), fill=self.dark)
            lines, font, size = self.wrap(draw, screen, max_w - 40, 3, 104)
            self.text_block(draw, lines, font, size, 800, self.dark, x=m + 10, shadow=False)
        else:  # cta
            lines, font, size = self.wrap(draw, str(self.s.get("brand.name", "")).upper(), max_w, 3, 110)
            y = self.text_block(draw, lines, font, size, 720, self.accent, center=True)
            y = self.text_block(draw, [str(self.s.get("brand.handle", ""))], self.font(60, False), 60, y + 24,
                                self.text_color, center=True)
            draw.rounded_rectangle((self.w / 2 - 230, y + 40, self.w / 2 + 230, y + 140), radius=50, fill=self.accent)
            label = "FOLLOW"
            lf = self.font(58)
            draw.text(((self.w - draw.textlength(label, font=lf)) / 2, y + 56), label, font=lf, fill=self.dark)

        if source_line:
            sfont = self.font(34, False)
            text = source_line if draw.textlength(source_line, font=sfont) <= max_w else source_line[:58] + "..."
            draw.text((m, 1500), text, font=sfont, fill=(205, 214, 232))
        return img

    def flat_frame(self, layer: Image.Image, index: int) -> Image.Image:
        return Image.alpha_composite(self.background(index).convert("RGBA"), layer).convert("RGB")


def story_icon(title: str, beats: list[dict[str, str]]) -> str:
    return pick_icon(title + " " + " ".join(b.get("say", "") for b in beats[:3]))
