"""Sound design for kinetic Reels: an arranged, original track plus effects placed on the visual events.

Everything is synthesised with numpy (no samples, no licences). The track follows the story:
hook = pad + impact, details = full groove, alert = low and tense, relief = drums drop out, closer = lifts on the question.
Effects are tied to what is on screen: counter ticks and a bell at the end, chime on the check mark, tap + notification
on the phone, coins arriving at the shop, risers into the alert and the final question, whooshes on every cut.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

SR = 44100
PROG = [  # Am - F - C - G (i - VI - III - VII): bright but serious, the classic tech/news bed
    dict(root=55.00, tones=[220.00, 261.63, 329.63]),
    dict(root=43.65, tones=[174.61, 220.00, 261.63]),
    dict(root=65.41, tones=[261.63, 329.63, 392.00]),
    dict(root=49.00, tones=[196.00, 246.94, 293.66]),
]


def _t(d: float) -> np.ndarray:
    return np.arange(max(int(d * SR), 1)) / SR


def _lowpass(x: np.ndarray, cutoff: float, order: int = 4) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(spec / (1 + (f / cutoff) ** order), len(x))


def _highpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(spec * (1 - 1 / (1 + (f / cutoff) ** order)), len(x))


def _saw(freq: float, n: int, cents: float = 0.0) -> np.ndarray:
    ph = np.cumsum(np.full(n, freq * 2 ** (cents / 1200) / SR))
    return 2 * (ph % 1.0) - 1


def _conv(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    size = 1 << int(math.ceil(math.log2(len(x) + len(ir))))
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]


class Mixer:
    def __init__(self, total: float, seed: int) -> None:
        self.n = int((total + 2.5) * SR)
        self.rng = np.random.default_rng(seed)
        z = lambda: np.zeros(self.n, dtype=np.float32)  # noqa: E731
        self.pad_l, self.pad_r, self.arp, self.bass = z(), z(), z(), z()
        self.drums, self.sfx_l, self.sfx_r, self.send = z(), z(), z(), z()
        self.duck = np.ones(self.n, dtype=np.float32)

    @staticmethod
    def _place(bus: np.ndarray, t0: float, sig: np.ndarray, gain: float) -> None:
        i0 = int(t0 * SR)
        if i0 < 0 or i0 >= len(bus):
            return
        seg = sig[: len(bus) - i0]
        bus[i0:i0 + len(seg)] += (seg * gain).astype(np.float32)

    def add_kick_duck(self, t0: float, depth: float) -> None:
        i0 = int(t0 * SR)
        if 0 <= i0 < self.n:
            m = min(int(0.35 * SR), self.n - i0)
            env = 1 - depth * np.exp(-np.arange(m) / SR / 0.11)
            self.duck[i0:i0 + m] = np.minimum(self.duck[i0:i0 + m], env.astype(np.float32))


# ----------------------------------------------------------------------- instruments
def kick(rng) -> np.ndarray:  # noqa: ANN001
    t = _t(0.38)
    f = 46 + 120 * np.exp(-t * 32)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 10)
    click = np.sin(2 * np.pi * 2100 * t) * np.exp(-t * 400) * 0.25
    return body + click


def clap(rng) -> np.ndarray:  # noqa: ANN001
    t = _t(0.22)
    n = _highpass(rng.normal(0, 1, len(t)), 1100)
    env = np.exp(-t * 24) + 0.6 * np.exp(-((t - 0.012) / 0.006) ** 2) + 0.5 * np.exp(-((t - 0.026) / 0.006) ** 2)
    return n * env * 0.45


def hat(rng, open_: bool = False) -> np.ndarray:  # noqa: ANN001
    t = _t(0.22 if open_ else 0.05)
    return _highpass(rng.normal(0, 1, len(t)), 7000) * np.exp(-t * (14 if open_ else 80)) * 0.5


def pluck(freq: float, dur: float = 0.55) -> np.ndarray:
    t = _t(dur)
    s = np.sin(2 * np.pi * freq * t) + 0.45 * np.sin(2 * np.pi * 2 * freq * t) + 0.2 * np.sin(2 * np.pi * 3 * freq * t)
    return s * np.exp(-t * 9) * (1 - np.exp(-t * 400))


def bass_note(freq: float, dur: float, growl: float = 0.0) -> np.ndarray:
    t = _t(dur)
    s = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * 2 * freq * t) + growl * _lowpass(_saw(freq, len(t)), 400)
    return s * np.minimum(t / 0.012, 1) * np.exp(-t * (2.0 if dur > 1 else 3.2))


def pad_chord(tones: list[float], dur: float, bright: float) -> tuple[np.ndarray, np.ndarray]:
    n = int(dur * SR)
    left = np.zeros(n)
    right = np.zeros(n)
    for i, f in enumerate(tones):
        for cents, side in ((-9, 0), (0, 2), (9, 1)):
            v = _saw(f, n, cents) / (3 * len(tones))
            if side == 0:
                left += v
            elif side == 1:
                right += v
            else:
                left += 0.7 * v
                right += 0.7 * v
    t = np.arange(n) / SR
    env = np.minimum(t / 0.45, 1) * np.minimum((dur - t) / 0.6 + 0.0, 1).clip(0, 1)
    return _lowpass(left * env, 900 + 1500 * bright), _lowpass(right * env, 900 + 1500 * bright)


# ----------------------------------------------------------------------- effects
def sfx_signal(kind: str, rng, arg: float = 0.0) -> np.ndarray:  # noqa: ANN001
    if kind == "boom":
        t = _t(1.3)
        f = 33 + 36 * np.exp(-t * 6)
        n = _lowpass(rng.normal(0, 1, len(t)), 600)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.0) + n * np.exp(-t * 18) * 0.5
    if kind == "thud":
        t = _t(0.28)
        f = 48 + 60 * np.exp(-t * 28)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 15) + _highpass(rng.normal(0, 1, len(t)), 900) * np.exp(-t * 90) * 0.25
    if kind == "whoosh":
        t = _t(0.5)
        n = rng.normal(0, 1, len(t))
        sweep = _highpass(n, 400) * ((t / 0.5) ** 2.2)
        return sweep * 0.7
    if kind == "riser":
        d = 1.25
        t = _t(d)
        n = _highpass(rng.normal(0, 1, len(t)), 900) * (t / d) ** 2.5 * 0.5
        f = 180 * (2 ** (t / d * 3.5))
        return n + np.sin(2 * np.pi * np.cumsum(f) / SR) * (t / d) ** 2 * 0.35
    if kind == "tick":
        t = _t(0.035)
        f = 1500 + 1700 * arg
        return np.sin(2 * np.pi * f * t) * np.exp(-t * 130) * 0.6
    if kind in ("ding", "bell"):
        t = _t(1.0)
        s = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((1568, 1), (2350, 0.5), (3136, 0.35), (4180, 0.2)))
        return s * np.exp(-t * 4.2) * 0.45
    if kind == "chime_up":
        out = np.zeros(int(1.3 * SR))
        for i, f in enumerate((659.3, 987.8)):
            t = _t(1.0)
            s = (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * 2 * f * t)) * np.exp(-t * 5)
            i0 = int(i * 0.16 * SR)
            out[i0:i0 + len(s)] += s[: len(out) - i0] * 0.5
        return out
    if kind == "notify":
        out = np.zeros(int(0.9 * SR))
        for i, f in enumerate((1319, 1760)):
            t = _t(0.5)
            s = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 3 * f * t)) * np.exp(-t * 7)
            i0 = int(i * 0.11 * SR)
            out[i0:i0 + len(s)] += s[: len(out) - i0] * 0.45
        return out
    if kind == "pop":
        t = _t(0.09)
        f = 720 * np.exp(-t * 18) + 220
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 38) * 0.55
    if kind == "bwip":
        t = _t(0.2)
        f = 260 + 900 * (t / 0.2) ** 1.5
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.minimum(t / 0.01, 1) * np.exp(-t * 9) * 0.5
    if kind == "sparkle":
        out = np.zeros(int(0.6 * SR))
        for f, off in ((3136, 0.0), (4186, 0.05), (5274, 0.1), (6272, 0.15)):
            t = _t(0.35)
            s = np.sin(2 * np.pi * f * t) * np.exp(-t * 14) * 0.25
            i0 = int(off * SR)
            out[i0:i0 + len(s)] += s[: len(out) - i0]
        return out
    if kind == "click":
        t = _t(0.03)
        return (rng.normal(0, 1, len(t)) * np.exp(-t * 420) * 0.5 + np.sin(2 * np.pi * 1300 * t) * np.exp(-t * 160) * 0.5)
    if kind == "kaching":
        out = np.zeros(int(1.0 * SR))
        t = _t(0.9)
        out[: len(t)] += (np.sin(2 * np.pi * 2093 * t) + 0.5 * np.sin(2 * np.pi * 3136 * t)) * np.exp(-t * 6) * 0.4
        t2 = _t(0.06)
        out[: len(t2)] += _highpass(rng.normal(0, 1, len(t2)), 3000) * np.exp(-t2 * 60) * 0.5
        return out
    if kind == "rumble":
        t = _t(1.2)
        n = _lowpass(rng.normal(0, 1, len(t)), 110, 4)
        return n * 14 * (np.minimum(t / 0.15, 1)) * np.exp(-t * 2.0) * (0.7 + 0.3 * np.sin(2 * np.pi * 9 * t))
    if kind == "stinger":
        t = _t(0.75)
        f = 300 * (2 ** (t / 0.75 * 2.0))
        rise = np.sin(2 * np.pi * np.cumsum(f) / SR) * (t / 0.75) ** 2 * 0.4
        hit = np.zeros(len(t))
        t3 = _t(0.25)
        hit[-len(t3):] += np.sin(2 * np.pi * 392 * t3) * np.exp(-t3 * 9) * 0.4
        return rise + hit
    raise ValueError(f"unknown sfx {kind}")


SFX_PAN = {"whoosh": 0.0, "sparkle": 0.0}


# ----------------------------------------------------------------------- composition
def compose(total: float, bpm: float, sections: list[tuple[float, float, str]], events: list[tuple[float, str, float]],
            seed: int, with_music: bool, sfx_gain: float, path: Path) -> Path:
    mix = Mixer(total, seed)
    rng = mix.rng
    beat = 60.0 / bpm
    ir_l = _lowpass(rng.normal(0, 1, int(1.7 * SR)), 5000) * np.exp(-_t(1.7) * 3.4)
    ir_r = _lowpass(rng.normal(0, 1, int(1.7 * SR)), 5000) * np.exp(-_t(1.7) * 3.4)

    def kind_at(t: float) -> str:
        for a, b, k in sections:
            if a <= t < b:
                return k
        return sections[-1][2] if sections else "detail"

    if with_music:
        n_beats = int(total / beat) + 2
        n_bars = n_beats // 4 + 1
        # ---- harmony: pad per bar (bright on the closer, dark on the alert)
        for bar in range(n_bars):
            t0 = bar * 4 * beat
            if t0 >= total + 0.5:
                break
            k = kind_at(t0)
            chord = PROG[bar % 4]
            bright = {"closer": 1.0, "relief": 0.7, "alert": 0.0}.get(k, 0.4)
            gain = {"alert": 0.18, "relief": 0.34}.get(k, 0.30)
            pl, pr = pad_chord(chord["tones"], 4 * beat + 0.7, bright)
            Mixer._place(mix.pad_l, t0, pl, gain)
            Mixer._place(mix.pad_r, t0, pr, gain)
            Mixer._place(mix.send, t0, (pl + pr) * 0.5, gain * 0.55)
        # ---- rhythm, bass, arp per beat
        for b in range(n_beats):
            t0 = b * beat
            if t0 >= total:
                break
            k = kind_at(t0)
            bar, pos = divmod(b, 4)
            chord = PROG[bar % 4]
            drums_on = k not in ("relief",)
            if drums_on:
                kick_here = (pos in (0, 2)) if k in ("hook", "alert", "closer") else True
                if k == "closer" and pos != 0 and bar * 4 + pos < n_beats - 6:
                    kick_here = pos == 0
                if kick_here:
                    Mixer._place(mix.drums, t0, kick(rng), 0.85 if k != "alert" else 1.0)
                    mix.add_kick_duck(t0, 0.55)
                if k not in ("hook",) and pos in (1, 3):
                    c = clap(rng)
                    Mixer._place(mix.drums, t0, c, 0.5)
                    Mixer._place(mix.send, t0, c, 0.35)
                if k not in ("hook",):
                    step = 0.25 if k == "alert" else 0.5
                    for j in range(int(1 / step)):
                        tt = t0 + j * step * beat
                        is_off = (j % 2 == 1) if step == 0.5 else True
                        if is_off:
                            Mixer._place(mix.drums, tt, hat(rng, open_=(k != "alert" and pos == 3 and j == 1)), 0.20 if k != "alert" else 0.11)
            # bass
            if k in ("hook", "detail", "stat", "twist", "alert", "closer"):
                growl = 0.8 if k == "alert" else 0.0
                if pos in (0, 2):
                    note = chord["root"] * (2 if (pos == 2 and k in ("detail", "stat", "twist")) else 1)
                    Mixer._place(mix.bass, t0, bass_note(note, 2 * beat if k == "alert" else beat * 1.6, growl), 0.40)
            # arp
            if k in ("detail", "stat", "twist", "closer", "relief"):
                tones = chord["tones"]
                if k == "relief":
                    seq = [tones[1] * 2] if pos in (0, 2) else []
                    sub = [0.0]
                    for f in seq:
                        Mixer._place(mix.arp, t0, pluck(f, 0.9), 0.20)
                else:
                    pattern = [tones[0], tones[1], tones[2], tones[1]] if k != "closer" else [tones[0], tones[1], tones[2], tones[2] * 2]
                    for j in range(2):
                        f = pattern[(pos * 2 + j) % 4] * 2
                        Mixer._place(mix.arp, t0 + j * 0.5 * beat, pluck(f), 0.17)
    # ---- effects tied to the visuals
    for t0, kind, g in events:
        sig = sfx_signal(kind, rng, g if kind == "tick" else 0.0)
        gain = (g if kind != "tick" else 0.8) * sfx_gain
        pan = 0.5 + 0.35 * math.sin(t0 * 2.3)
        if kind in ("whoosh",):
            Mixer._place(mix.sfx_l, t0, sig, gain * (1 - 0.35 * math.sin(t0)))
            Mixer._place(mix.sfx_r, t0, sig, gain * (1 + 0.35 * math.sin(t0)))
        else:
            Mixer._place(mix.sfx_l, t0, sig, gain * (1 - pan * 0.6))
            Mixer._place(mix.sfx_r, t0, sig, gain * (0.4 + pan * 0.6))
        if kind in ("boom", "ding", "chime_up", "notify", "kaching", "sparkle", "stinger"):
            Mixer._place(mix.send, t0, sig, gain * 0.45)
    # ---- mix down with sidechain pumping and reverb
    d = mix.duck
    music_l = mix.pad_l * d + mix.arp * (0.4 + 0.6 * d) + mix.bass * d + mix.drums
    music_r = mix.pad_r * d + mix.arp * (0.4 + 0.6 * d) + mix.bass * d + mix.drums
    wet_l = _conv(mix.send, ir_l.astype(np.float32)) * 0.35
    wet_r = _conv(mix.send, ir_r.astype(np.float32)) * 0.35
    left = music_l + mix.sfx_l + wet_l
    right = music_r + mix.sfx_r + wet_r
    out = np.stack([left, right], axis=1)[: int(total * SR)]
    peak = float(np.max(np.abs(out))) or 1.0
    out = np.tanh(out / peak * 1.5) * 0.9
    path.write_bytes((out * 32767).astype("<i2").tobytes())
    return path
