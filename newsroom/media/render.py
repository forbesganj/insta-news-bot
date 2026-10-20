"""Reel assembly: TTS per beat -> layers -> (B-roll or animated gradient) segments -> sound mix -> captions -> MP4."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..net import Http
from ..settings import Settings
from .audio import mix_audio, make_pad, pick_music
from .broll import fetch_clips
from .icons import pick_icon
from .subtitles import write_subtitles
from .tts import TTS, BeatAudio
from .util import MediaError, have_ffmpeg, probe_duration, run
from .visuals import Painter, story_icon

log = logging.getLogger(__name__)


@dataclass
class RenderResult:
    video: Path
    srt: Path
    duration: float
    silent: bool
    providers: list[str]
    broll: bool = False
    music: str = "none"


def _static_segment(frame: Path, seconds: float, out: Path, fps: int, w: int, h: int) -> None:
    frames = max(int(round(seconds * fps)), 2)
    zoom = (f"zoompan=z='min(zoom+0.0005,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(frame),
         "-vf", f"scale={int(w * 1.25)}:{int(h * 1.25)},{zoom},format=yuv420p",
         "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps), str(out)])


def _broll_segment(clip: Path, overlay: Path, seconds: float, offset: float, out: Path, fps: int, w: int, h: int) -> None:
    graph = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},"
             f"eq=brightness=-0.10:saturation=0.9[bg];[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-ss", f"{offset:.2f}", "-i", str(clip),
         "-loop", "1", "-framerate", str(fps), "-i", str(overlay), "-t", f"{seconds:.3f}",
         "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-r", str(fps), str(out)])


def render_reel(beats: list[dict[str, str]], source_line: str, keywords: list[str], work_dir: Path,
                settings: Settings, http: Http, story_id: str = "story", title: str = "") -> RenderResult:
    if not have_ffmpeg():
        raise MediaError("ffmpeg/ffprobe not found on PATH")
    work_dir.mkdir(parents=True, exist_ok=True)
    fps = int(settings.get("media.fps", 30))
    w, h = int(settings.get("media.width", 1080)), int(settings.get("media.height", 1920))
    gap = float(settings.get("media.beat_gap_s", 0.18))
    end_pad = float(settings.get("media.end_pad_s", 0.5))

    # 1. voice, one clip per beat so timing is exact
    tts = TTS(settings)
    audios: list[BeatAudio] = []
    for i, beat in enumerate(beats):
        audios.append(tts.synthesize(beat["say"], work_dir / f"beat{i}.mp3"))
    silent = all(a.path is None for a in audios)
    durations = [a.duration + (end_pad if i == len(beats) - 1 else gap) for i, a in enumerate(audios)]
    starts = [sum(durations[:i]) for i in range(len(durations))]
    total = sum(durations)

    # 2. visuals
    clips = fetch_clips(http, keywords, work_dir) if settings.get("media.broll", True) else []
    use_broll = bool(clips)
    if use_broll:
        source_line = (source_line + " | Stock visuals: Pexels")[:90]
    painter = Painter(settings)
    default_icon = story_icon(title, beats)
    segments: list[Path] = []
    clip_use: dict[Path, int] = {}
    for i, beat in enumerate(beats):
        icon = pick_icon(beat.get("say", "") + " " + beat.get("screen", ""), beat.get("icon")) \
            if beat.get("icon") or pick_icon(beat.get("say", "")) != "spark" else default_icon
        layer = painter.layer(beat, i, len(beats), source_line, use_broll, icon, beat.get("mood", "none") not in ("none", ""))
        seg = work_dir / f"seg{i}.mp4"
        if use_broll:
            overlay = work_dir / f"layer{i}.png"
            layer.save(overlay, "PNG")
            clip = clips[i % len(clips)]
            used = clip_use.get(clip, 0)
            clip_use[clip] = used + 1
            try:
                clip_len = probe_duration(clip)
            except MediaError:
                clip_len = 0.0
            offset = (used * durations[i]) % max(clip_len - durations[i], 0.1) if clip_len > durations[i] else 0.0
            try:
                _broll_segment(clip, overlay, durations[i], offset, seg, fps, w, h)
            except MediaError as exc:
                log.warning("b-roll segment failed (%s); using the gradient background", str(exc)[:160])
                frame = work_dir / f"frame{i}.png"
                painter.flat_frame(painter.layer(beat, i, len(beats), source_line, False, icon,
                                                 beat.get("mood", "none") not in ("none", "")), i).save(frame)
                _static_segment(frame, durations[i], seg, fps, w, h)
        else:
            frame = work_dir / f"frame{i}.png"
            painter.flat_frame(layer, i).save(frame, "PNG")
            _static_segment(frame, durations[i], seg, fps, w, h)
        segments.append(seg)

    concat_list = work_dir / "segments.txt"
    concat_list.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    video_raw = work_dir / "video_raw.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", concat_list.name,
         "-c", "copy", video_raw.name], cwd=work_dir)

    # 3. captions
    groups: list[list[tuple[str, float, float]]] = []
    for audio, start in zip(audios, starts):
        groups.append([(word, start + ws, start + we) for word, ws, we in audio.words])
    ass, srt = write_subtitles(groups, work_dir, str(settings.get("brand.accent", "#FFB800")), "DejaVu Sans", w, h)

    # 4. voice track, then sound design
    voice_wav = work_dir / "voice.wav"
    if silent:
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-t", f"{total:.3f}", "-c:a", "pcm_s16le", voice_wav.name], cwd=work_dir)
    else:
        inputs: list[str] = []
        filters: list[str] = []
        labels: list[str] = []
        for audio, dur in zip(audios, durations):
            idx = len(labels)
            if audio.path is not None:
                inputs += ["-i", audio.path.name]
                filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={dur:.3f}[a{idx}]")
            else:
                inputs += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
                filters.append(f"[{idx}:a]aformat=channel_layouts=stereo[a{idx}]")
            labels.append(f"[a{idx}]")
        graph = ";".join(filters) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"
        run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]",
             "-c:a", "pcm_s16le", voice_wav.name], cwd=work_dir)

    mode = str(settings.get("media.music_mode", "auto")).lower()
    music = pick_music(settings, story_id)
    music_label = music.name if music else "none"
    if music is None and mode in ("auto", "synth"):
        music = make_pad(work_dir / "pad.wav", total, story_id)
        music_label = "synth-pad"
    mixed = work_dir / "mix.wav"
    mix_audio(voice_wav, total, starts, music, silent, settings, work_dir, mixed)

    # 5. final mux with burned-in captions
    final = work_dir / "reel.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", video_raw.name, "-i", mixed.name, "-vf", "ass=subs.ass",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
         "-r", str(fps), "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-shortest", "-movflags", "+faststart",
         final.name], cwd=work_dir)
    return RenderResult(final, srt, probe_duration(final), silent, sorted(tts.used) or ["none"], use_broll, music_label)
