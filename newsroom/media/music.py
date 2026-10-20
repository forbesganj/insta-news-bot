"""Original beat + sound effects, synthesised with numpy (no samples, no licences).

Used by the kinetic Reel style. Cuts land on the beat; hits and whooshes are placed from the scene timeline.
If you put royalty-free tracks in assets/music/, they are used instead of the synthesised drums and bass
(the sound effects are still added on top).
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import numpy as np

from ..settings import Settings
from .util import MediaError, run

log = logging.getLogger(__name__)
SR = 44100
PROGRESSIONS = [
    [55.00, 55.00, 43.65, 49.00],
    [55.00, 49.00, 43.65, 49.00],
    [61.74, 55.00, 43.65, 49.00],
]
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}


def _seed(text: str) -> int:
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)


class _Synth:
    def __init__(self, total: float, seed: int) -> None:
        self.n = int(total * SR) + SR
        self.out = np.zeros(self.n, dtype=np.float32)
        self.rng = np.random.default_rng(seed)

    def add(self, t0: float, sig: np.ndarray, gain: float = 1.0) -> None:
        i0 = int(t0 * SR)
        if i0 < 0 or i0 >= self.n:
            return
        seg = sig[: self.n - i0]
        self.out[i0:i0 + len(seg)] += seg * gain

    def t(self, d: float) -> np.ndarray:
        return np.arange(int(d * SR)) / SR

    def kick(self) -> np.ndarray:
        t = self.t(0.35)
        f = 45 + 90 * np.exp(-t * 30)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 11)

    def clap(self) -> np.ndarray:
        t = self.t(0.18)
        n = np.diff(self.rng.normal(0, 1, len(t)), prepend=0)
        return n * np.exp(-t * 26) * 0.5

    def hat(self) -> np.ndarray:
        t = self.t(0.06)
        n = np.diff(self.rng.normal(0, 1, len(t)), prepend=0)
        return n * np.exp(-t * 70) * 0.35

    def bass(self, f: float, d: float = 0.9) -> np.ndarray:
        t = self.t(d)
        env = np.minimum(t / 0.01, 1) * np.exp(-t * 3.2)
        return (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t)) * env

    def boom(self) -> np.ndarray:
        t = self.t(1.2)
        f = 34 + 30 * np.exp(-t * 6)
        n = np.diff(self.rng.normal(0, 1, len(t)), prepend=0)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.2) + n * np.exp(-t * 22) * 0.35

    def whoosh(self, d: float = 0.42) -> np.ndarray:
        t = self.t(d)
        n = np.diff(self.rng.normal(0, 1, len(t)), prepend=0)
        return n * ((t / d) ** 2.2) * 0.55

    def blip(self) -> np.ndarray:
        t = self.t(0.05)
        return np.sin(2 * np.pi * 1500 * t) * np.exp(-t * 60) * 0.5


def synth_audio(total: float, bpm: float, cut_times: list[float], hit_times: list[float], blip_times: list[float],
                calm_beats: list[tuple[int, int]], seed_text: str, with_music: bool, path: Path,
                sfx_gain: float = 1.0) -> Path:
    """Write a stereo 16-bit raw file (s16le, 44.1 kHz) of exactly `total` seconds."""
    s = _Synth(total, _seed(seed_text))
    beat = 60.0 / bpm
    notes = PROGRESSIONS[_seed(seed_text) % len(PROGRESSIONS)]
    if with_music:
        for b in range(int(total / beat) + 1):
            t0 = b * beat
            calm = any(a <= b < z for a, z in calm_beats)
            if not calm:
                s.add(t0, s.kick(), 0.85)
                if b % 4 in (1, 3):
                    s.add(t0, s.clap(), 0.55)
            s.add(t0 + 0.5 * beat, s.hat(), 0.3 if calm else 0.5)
            if b % 2 == 0:
                s.add(t0, s.bass(notes[(b // 4) % 4]), 0.3 if calm else 0.45)
    for t in hit_times:
        s.add(t, s.boom(), 0.85 * sfx_gain)
    for t in cut_times:
        s.add(t - 0.40, s.whoosh(), 0.5 * sfx_gain)
    for t in blip_times:
        s.add(t, s.blip(), 0.35 * sfx_gain)
    peak = float(np.max(np.abs(s.out))) or 1.0
    out = np.tanh(s.out / peak * 1.4) * 0.85
    stereo = np.stack([out, out * 0.985], axis=1)[: int(total * SR)]
    path.write_bytes((stereo * 32767).astype("<i2").tobytes())
    return path


def pick_track(settings: Settings, seed_text: str) -> Path | None:
    mode = str(settings.get("media.music_mode", "auto")).lower()
    if mode in ("off", "synth"):
        return None
    folder = settings.root / str(settings.get("media.music_dir", "assets/music"))
    tracks = sorted(p for p in folder.glob("*") if p.suffix.lower() in AUDIO_EXT) if folder.exists() else []
    return tracks[_seed(seed_text) % len(tracks)] if tracks else None


VOICE_FX = ("highpass=f=70,acompressor=threshold=0.125:ratio=3:attack=5:release=80:makeup=2,"
            "equalizer=f=3200:t=q:w=1:g=2,aformat=channel_layouts=stereo")


def finish_audio(raw: Path, total: float, track: Path | None, work_dir: Path, out: Path, loudness: float = -14.0,
                 voice: Path | None = None) -> Path:
    """Loudness-normalise into the final WAV. With a voice: broadcast-style voice polish, and the beat/music is
    ducked under it so speech is always clear."""
    fade = f"afade=t=out:st={max(total - 0.7, 0):.2f}:d=0.7"
    base = ["-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", str(raw)]
    if voice is not None:
        inputs = [*base, "-i", str(voice)]
        if track is None:
            bed = "[0:a]volume=0.5[bed0]"
        else:
            inputs += ["-stream_loop", "-1", "-i", str(track)]
            bed = ("[0:a]volume=0.4[b0];[2:a]aformat=channel_layouts=stereo,volume=0.35[m];"
                   "[b0][m]amix=inputs=2:normalize=0:duration=first[bed0]")
        graph = (f"[1:a]{VOICE_FX},asplit=2[v][vs];{bed};"
                 f"[bed0][vs]sidechaincompress=threshold=0.02:ratio=9:attack=15:release=350[duck];"
                 f"[v][duck]amix=inputs=2:normalize=0:duration=first,loudnorm=I={loudness}:TP=-1.5:LRA=9,"
                 f"alimiter=limit=0.89,{fade}[o]")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[o]",
               "-t", f"{total:.3f}", str(out)]
        try:
            run(cmd, cwd=work_dir)
        except MediaError as exc:
            log.warning("voice mix failed (%s); using the plain voice track", str(exc)[:160])
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice), "-af", f"loudnorm=I={loudness}:TP=-1.5:LRA=9",
                 "-t", f"{total:.3f}", str(out)], cwd=work_dir)
        return out
    if track is None:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", *base, "-af",
               f"loudnorm=I={loudness}:TP=-1.5:LRA=9,alimiter=limit=0.89,{fade}", str(out)]
    else:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", *base, "-stream_loop", "-1", "-i", str(track),
               "-filter_complex",
               f"[1:a]aformat=channel_layouts=stereo,volume=0.55[m];[0:a][m]amix=inputs=2:normalize=0:duration=first,"
               f"loudnorm=I={loudness}:TP=-1.5:LRA=9,alimiter=limit=0.89,{fade}[o]",
               "-map", "[o]", "-t", f"{total:.3f}", str(out)]
    try:
        run(cmd, cwd=work_dir)
    except MediaError as exc:
        log.warning("audio finishing failed (%s); using the raw mix", str(exc)[:160])
        run(["ffmpeg", "-y", "-loglevel", "error", *base, str(out)], cwd=work_dir)
    return out
