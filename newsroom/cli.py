"""Command line entry point:  python -m newsroom.cli <command>"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__
from .analytics import optimizer
from .demo import write_demo_feeds
from .gates import UpstreamTools
from .llm import get_llm
from .media.tts import TTS, voice_status
from .media.util import find_font, have_ffmpeg
from .pipeline import (Context, StopPublishing, build_context, refresh_token, stage_analytics, stage_approvals,
                       stage_collect, stage_optimize, stage_process, stage_publish, stage_render)
from .publish.meta import InstagramClient, MetaError
from .settings import Settings, env, load_settings, setup_logging

log = logging.getLogger("newsroom")


def _with_run(ctx: Context, stage: str, func):  # noqa: ANN001, ANN202
    run_id = ctx.store.start_run(stage)
    try:
        result = func()
    except StopPublishing as exc:
        ctx.store.finish_run(run_id, "halted", str(exc))
        log.error("HALTED: %s", exc)
        ctx.notify(f"Pipeline halted: {exc}")
        raise
    except Exception as exc:
        ctx.store.finish_run(run_id, "error", repr(exc))
        raise
    ctx.store.finish_run(run_id, "ok", json.dumps(result, default=str)[:1500] if result is not None else "")
    return result


def cmd_collect(ctx: Context, args) -> int:  # noqa: ANN001
    print(json.dumps(_with_run(ctx, "collect", lambda: stage_collect(ctx)), indent=2))
    return 0


def cmd_process(ctx: Context, args) -> int:  # noqa: ANN001
    ids = _with_run(ctx, "process", lambda: stage_process(ctx, args.max_new))
    print(f"scripted: {ids}")
    return 0


def cmd_render(ctx: Context, args) -> int:  # noqa: ANN001
    ids = _with_run(ctx, "render", lambda: stage_render(ctx))
    print(f"rendered: {ids}")
    return 0


def cmd_produce(ctx: Context, args) -> int:  # noqa: ANN001
    def go() -> dict:
        collected = stage_collect(ctx)
        scripted = stage_process(ctx, args.max_new)
        rendered = stage_render(ctx)
        return {"collected": collected, "scripted": scripted, "rendered": rendered}

    result = _with_run(ctx, "produce", go)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_approvals(ctx: Context, args) -> int:  # noqa: ANN001
    print(f"decisions applied: {_with_run(ctx, 'approvals', lambda: stage_approvals(ctx))}")
    return 0


def cmd_publish(ctx: Context, args) -> int:  # noqa: ANN001
    def go() -> dict:
        applied = stage_approvals(ctx)
        published = stage_publish(ctx)
        return {"approvals": applied, "published": published}

    try:
        print(json.dumps(_with_run(ctx, "publish", go), indent=2))
    except StopPublishing:
        return 2
    return 0


def cmd_analytics(ctx: Context, args) -> int:  # noqa: ANN001
    def go() -> dict:
        stats = stage_analytics(ctx)
        weights = stage_optimize(ctx)
        return {"collected": stats, "weights": weights}

    try:
        print(json.dumps(_with_run(ctx, "analytics", go), indent=2))
    except MetaError as exc:
        log.error("analytics halted: %s", exc)
        return 2
    print(optimizer.report(ctx.store))
    return 0


def cmd_optimize(ctx: Context, args) -> int:  # noqa: ANN001
    stage_optimize(ctx)
    print(optimizer.report(ctx.store))
    return 0


def cmd_refresh_token(ctx: Context, args) -> int:  # noqa: ANN001
    new = refresh_token(ctx)
    if args.print_token:
        print(new)
    else:
        print("token refreshed (use --print-token inside the workflow to capture it)")
    return 0


def cmd_status(ctx: Context, args) -> int:  # noqa: ANN001
    rows = ctx.store.conn.execute("SELECT status, COUNT(*) c FROM stories GROUP BY status ORDER BY c DESC").fetchall()
    print("stories by status:")
    for r in rows:
        print(f"  {r['status']:<20} {r['c']}")
    print("\nqueue (queued):")
    for s in ctx.store.list_stories("queued", limit=20, order="score DESC"):
        print(f"  {s['id']}  approval={s['approval']:<9} score={s['score']:<6} {s['title'][:70]}")
    print("\nrecent rejections:")
    for s in ctx.store.list_stories(["rejected", "failed"], limit=8, order="updated_at DESC"):
        print(f"  {s['id']}  {str(s['last_error'])[:90]}")
    print("\nrecent runs:")
    for r in ctx.store.recent_runs(8):
        print(f"  {r['started_at']}  {r['stage']:<10} {r['status']}")
    print(f"\nposts: real={len(ctx.store.list_posts(True))} total={len(ctx.store.list_posts(False))}")
    print(f"DRY_RUN={ctx.settings.dry_run}  approval_mode={ctx.settings.approval_mode}")
    return 0


# ----------------------------------------------------------------------------- diagnostics
SAMPLE_HOOKS = ["India processed 20 billion UPI payments in one month.", "Hey guys, today let's talk about payments"]
SAMPLE_SCRIPT = ["India processed 20 billion UPI payments in one month.", "NPCI shared the data on Monday.",
                 "Small shops drove most of the growth.", "Fees stay at zero for most payments.",
                 "Follow for the daily brief."]


def cmd_check_upstream(ctx: Context, args) -> int:  # noqa: ANN001
    tools = ctx.tools
    print("Upstream tools found:")
    for key, path in tools.paths.items():
        print(f"  {key:<10} {path or 'NOT FOUND'}")
    ok = True
    for label, func in (("hookscore", lambda: tools.hookscore(SAMPLE_HOOKS)),
                        ("beats", lambda: tools.beats(SAMPLE_SCRIPT, 30)),
                        ("caption", lambda: tools.caption("UPI crossed 20 billion payments.\n\nSource: Mint\n\n#upi #fintech", ["upi"])),
                        ("detect", lambda: tools.detect("India processed 20 billion UPI payments in one month. NPCI shared the data on Monday."))):
        result = func()
        print(f"\n{label}: {'parsed OK' if result else 'NOT PARSED (built-in fallback will be used)'}")
        if result:
            print("  ", json.dumps(result, default=str)[:300])
        ok = ok and bool(result)
    print("\nHumanize: set gates.humanize_mode in config/settings.yaml. Raw output of upstream humanize.py on a sample:")
    raw = tools.run("humanize", [str(_sample_file('It\u2019s not just a tool \u2014 it\u2019s a revolution.\n'))])
    print(raw if raw else "  (no output)")
    return 0 if ok else 1


def _sample_file(text: str) -> Path:
    path = Path(tempfile.mkdtemp()) / "sample.txt"
    path.write_text(text, encoding="utf-8")
    return path


def cmd_doctor(ctx: Context, args) -> int:  # noqa: ANN001
    s = ctx.settings
    problems = 0

    def line(level: str, text: str) -> None:
        nonlocal problems
        if level in ("FIX",):
            problems += 1
        print(f"[{level:<4}] {text}")

    line("OK" if have_ffmpeg() else "FIX", "ffmpeg + ffprobe on PATH" if have_ffmpeg() else "ffmpeg/ffprobe missing (apt install ffmpeg)")
    font = find_font(str(s.get("media.font_bold", "")), True)
    line("OK" if font else "WARN", f"bold font: {font}" if font else "no TrueType font found; text will look poor (apt install fonts-dejavu-core)")
    found = ctx.tools.available()
    for key, present in found.items():
        line("OK" if present else "WARN", f"upstream {key}: {'found' if present else 'not found (built-in fallback)'}")
    llm = get_llm()
    line("OK" if llm.name != "offline" else "WARN", f"LLM provider: {llm.name}" + (" (set GEMINI_API_KEY for real scripts)" if llm.name == "offline" else ""))
    tts = TTS(s)
    line("OK" if tts.chain() else "WARN", f"TTS provider: {tts.provider} (chain: {tts.chain() or 'silent'})")
    voiced, why = voice_status(s)
    line("OK" if voiced else "WARN", f"voice-over: {'ON via ' + why if voiced else 'OFF, ' + why}")
    line("OK" if ctx.host else "WARN", f"media host: {ctx.host.name}" if ctx.host else "no media host (SUPABASE_URL/SUPABASE_SERVICE_KEY or R2_*): real publishing will be blocked")
    line("OK" if ctx.telegram else "WARN", "Telegram approvals configured" if ctx.telegram else "Telegram not configured (approval_mode=telegram will keep every story pending)")
    token, user = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")
    if token and user:
        try:
            ig = InstagramClient(token, user, ctx.http, str(s.get("publish.api_version", "v25.0")))
            info = ig.me()
            line("OK", f"Instagram token valid for @{info.get('username', '?')} (user_id {info.get('user_id', '?')})")
            try:
                ig.publishing_limit()
                line("OK", "IG_USER_ID accepted by the publishing endpoint (content_publishing_limit readable)")
            except MetaError as exc:
                line("FIX", f"IG_USER_ID rejected by the publishing endpoint: {exc}")
        except MetaError as exc:
            line("FIX", f"Instagram token check failed: {exc}")
    else:
        line("WARN", "IG_ACCESS_TOKEN / IG_USER_ID not set (fine while DRY_RUN=true)")
    line("OK" if s.dry_run else "WARN", f"DRY_RUN={s.dry_run}" + ("" if s.dry_run else "  <- LIVE: posts will be published to Instagram"))
    print(f"\n{problems} blocking problem(s).")
    return 1 if problems else 0


VOICE_SAMPLE = ("Tonight's top story. A power bank just forced an IndiGo flight to turn back. Flight 6E 6115 was "
                "diverted to Hyderabad on Sunday, after smoke from a device. The crew took control, and the plane landed "
                "safely. Meanwhile, the R B I says U P I crossed 20 billion payments, up 31 percent. "
                "So, do you carry a power bank when you fly?")


def cmd_voice_test(ctx: Context, args) -> int:  # noqa: ANN001
    """Synthesize a sample with each configured voice and send it to Telegram, so you can LISTEN before going live."""
    import os
    import shutil
    import subprocess

    if args.voice and (args.provider or "").lower() in ("elevenlabs",):
        os.environ["ELEVENLABS_VOICE_ID"] = args.voice
    elif args.voice and (args.provider or "").lower() in ("google",):
        os.environ["GOOGLE_TTS_VOICE"] = args.voice
    tts = TTS(ctx.settings)
    providers = [args.provider.lower()] if args.provider and args.provider.lower() != "auto" else tts.chain()
    if not providers:
        print("No voice provider is configured. Add ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID, or GOOGLE_TTS_API_KEY.")
        return 1
    out_dir = ctx.settings.out_dir / "voice_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    text = args.text or VOICE_SAMPLE
    from .media.pronounce import speakable
    spoken = speakable(text, tts.words)
    failures = 0
    for provider in providers:
        try:
            raw = out_dir / f"voice_{provider}.mp3"
            audio = getattr(tts, f"_{provider}")(spoken, raw, None, None)
            final = out_dir / f"voice_test_{provider}.mp3"
            if audio.path.suffix != ".mp3":
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio.path), str(final)], check=True)
            else:
                shutil.copy(audio.path, final)
            label = {"elevenlabs": os.environ.get("ELEVENLABS_MODEL") or str(ctx.settings.get("voice.elevenlabs_model")),
                     "google": os.environ.get("GOOGLE_TTS_VOICE") or str(ctx.settings.get("media.google_voice"))}.get(provider, provider)
            print(f"{provider}: {final} ({audio.duration:.1f}s) [{label}]")
            if ctx.telegram:
                ctx.telegram.send_audio(final, f"Voice test: {provider} ({label})\n{text[:300]}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"{provider}: FAILED {exc}")
    if not ctx.telegram:
        print("Telegram is not configured, so nothing was sent; download the files from the workflow artifact.")
    return 1 if failures == len(providers) else 0


def cmd_demo(ctx: Context, args) -> int:  # noqa: ANN001
    """Run the full pipeline on fictional offline feeds. No keys, no network, nothing is published."""
    feeds = write_demo_feeds(ctx.settings.out_dir / "demo_feeds")
    ctx.settings.sources = {"rss": feeds, "tiers": (ctx.settings.sources or {}).get("tiers", {}),
                            "syndication_groups": (ctx.settings.sources or {}).get("syndication_groups", []),
                            "blocklist_domains": []}
    ctx.settings.offline = True
    ctx.http.offline = True
    ctx.settings.dry_run = True
    ctx.settings.approval_mode = "auto"
    ctx.telegram = None
    ctx.host = None
    stats = stage_collect(ctx)
    scripted = stage_process(ctx, 3)
    rendered = stage_render(ctx)
    published = stage_publish(ctx)
    print(json.dumps({"collect": stats, "scripted": scripted, "rendered": rendered, "dry_run_published": published}, indent=2))
    for story in ctx.store.list_stories(["rejected", "failed"], limit=10):
        print(f"rejected {story['id']}: {story['last_error']}")
    for sid in rendered:
        print(f"video: {ctx.settings.out_dir / (sid + '.mp4')}")
    return 0 if rendered else 1


COMMANDS = {
    "collect": cmd_collect, "process": cmd_process, "render": cmd_render, "produce": cmd_produce,
    "approvals": cmd_approvals, "publish": cmd_publish, "analytics": cmd_analytics, "optimize": cmd_optimize,
    "refresh-token": cmd_refresh_token, "status": cmd_status, "check-upstream": cmd_check_upstream,
    "doctor": cmd_doctor, "demo": cmd_demo, "voice-test": cmd_voice_test,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="newsroom", description="Automated news-to-Instagram pipeline")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--max-new", type=int, default=None, help="max stories to script in this run")
    parser.add_argument("--print-token", action="store_true", help="refresh-token: print the new token to stdout")
    parser.add_argument("--db", default=None, help="override SQLite path")
    parser.add_argument("--provider", default=None, help="voice-test: elevenlabs | google | auto")
    parser.add_argument("--voice", default=None, help="voice-test: ElevenLabs voice id or Google voice name")
    parser.add_argument("--text", default=None, help="voice-test: text to read")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    overrides = {}
    if args.db:
        overrides["db_path"] = args.db
    settings: Settings = load_settings(**overrides)
    setup_logging(settings)
    ctx = build_context(settings)
    try:
        return COMMANDS[args.command](ctx, args)
    except StopPublishing:
        return 2
    except Exception:
        log.exception("command %s failed", args.command)
        return 1
    finally:
        ctx.store.close()


if __name__ == "__main__":
    sys.exit(main())
