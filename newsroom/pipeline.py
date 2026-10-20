"""Stage orchestration. Each stage is idempotent and isolates failures per story."""
from __future__ import annotations

import logging
import random
import shutil
from types import SimpleNamespace
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from .analytics import collector, optimizer
from .content import ContentFactory, ContentRejected
from .kinetic_content import KineticContentFactory
from .dedupe import find_duplicate, ingest
from .gates import UpstreamTools
from .llm import LLMError, get_llm
from .media.qa import check_video
from .media.kinetic import render_kinetic
from .media.render import render_reel
from .media.tts import TTS
from .models import Fact
from .net import Http
from .publish.hosting import HostError, MediaHost, get_host
from .publish.meta import InstagramClient, MetaError, refresh_long_lived_token
from .publish.telegram import TelegramClient
from .research import extract_facts, gather_texts
from .score import newest_published, score_story, select_stories
from .settings import Settings, env
from .sources import collect_all
from .store import Store, iso, parse_iso, utcnow
from .textutil import tokens
from .verify import verify_story

log = logging.getLogger(__name__)

PIPELINE_STATUSES = ("candidate", "verified", "scripted", "queued")


class PipelineError(Exception):
    """A systemic failure (for example the LLM being unavailable) that must turn the run red."""


class StopPublishing(Exception):
    """Raised for conditions that must halt all publishing (invalid token, rate limit)."""


@dataclass
class Context:
    settings: Settings
    store: Store
    http: Http
    llm: Any
    tools: UpstreamTools
    rng: random.Random = field(default_factory=random.Random)
    telegram: TelegramClient | None = None
    host: MediaHost | None = None

    def notify(self, text: str) -> None:
        prefix = "[DRY RUN] " if self.settings.dry_run else ""
        if self.telegram:
            try:
                self.telegram.send_message(prefix + text)
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram notify failed: %s", exc)


def build_context(settings: Settings, llm: Any | None = None, seed: int | None = None) -> Context:
    http = Http(offline=settings.offline)
    store = Store(settings.db_path)
    tools = UpstreamTools(settings.root, strict=bool(settings.get("gates.upstream_strict", False)))
    return Context(settings=settings, store=store, http=http, llm=llm or get_llm(), tools=tools,
                   rng=random.Random(seed), telegram=None if settings.offline else TelegramClient.from_env(http),
                   host=None if settings.offline else get_host(http))


def _learned(ctx: Context) -> dict[str, float]:
    return ctx.store.get_weights("pillar")


# --------------------------------------------------------------------------- collect
def stage_collect(ctx: Context) -> dict[str, int]:
    items, counts = collect_all(ctx.settings, ctx.http)
    stats = ingest(ctx.store, items, ctx.settings)
    stats.update({f"src_{k}": v for k, v in counts.items() if v})
    return stats


# --------------------------------------------------------------------------- process
def _reject(ctx: Context, story_id: str, reason: str, status: str = "rejected") -> None:
    ctx.store.update_story(story_id, status=status, last_error=reason[:500])
    log.info("story %s -> %s: %s", story_id, status, reason)


def stage_process(ctx: Context, max_new: int | None = None) -> list[str]:
    """candidate -> research -> verify -> select -> script/gates. Returns ids that reached 'scripted'."""
    s, store = ctx.settings, ctx.store
    queue_target = int(s.get("publish.queue_target", 4))
    open_slots = queue_target - store.count_status("queued") - store.count_status("scripted")
    want = min(max_new if max_new is not None else open_slots, max(open_slots, 0))
    if want <= 0:
        log.info("queue is full (target %d); nothing to process", queue_target)
        return []
    now = utcnow()
    multipliers = _learned(ctx)
    max_age = float(s.get("verify.max_age_hours", 36))

    # 1. cheap pre-scoring, drop stale
    candidates = store.list_stories("candidate", limit=300)
    for story in candidates:
        published = newest_published(story)
        if published and (now - published) > timedelta(hours=max_age):
            _reject(ctx, story["id"], "verify: stale story")
            story["status"] = "rejected"
            continue
        score, detail = score_story(story, s, multipliers, now)
        store.update_story(story["id"], score=score)
        store.merge_meta(story["id"], score_detail=detail)
        story["score"] = score
    pool = sorted((c for c in candidates if c["status"] == "candidate"), key=lambda c: -c["score"])

    # 2. research + verify the best few
    llm_ok = llm_failed = 0
    last_llm_error = ""
    for story in pool[: max(want * 3, 3)]:
        sid = story["id"]
        try:
            texts = gather_texts(story, ctx.http, s)
            if sum(len(t["text"]) for t in texts) < int(s.get("research.min_chars", 250)):
                _reject(ctx, sid, "verify: not enough readable source text")
                continue
            facts, notes = extract_facts(story, texts, ctx.llm, s)
            llm_ok += 1
            result = verify_story(story, facts, s, now)
            store.merge_meta(sid, research_notes=notes[:10], text_origins=[t["origin"] for t in texts])
            if result.passed:
                store.update_story(sid, status="verified", facts=[f.to_dict() for f in facts],
                                   verification=result.to_dict(), sensitivity=result.sensitivity, last_error=None)
            else:
                store.update_story(sid, facts=[f.to_dict() for f in facts], verification=result.to_dict())
                _reject(ctx, sid, next((r for r in result.reasons if r.startswith("verify:")), "verify: failed"))
        except Exception as exc:  # noqa: BLE001
            attempts = int(story.get("attempts") or 0) + 1
            store.update_story(sid, attempts=attempts, last_error=f"research: {exc}"[:300])
            if attempts >= 3:
                store.update_story(sid, status="failed")
            if isinstance(exc, LLMError):
                llm_failed += 1
                last_llm_error = str(exc)[:300]
                log.error("LLM failure while researching %s: %s", sid, last_llm_error)
            else:
                log.exception("research failed for %s", sid)
    if llm_failed and not llm_ok:
        ctx.notify(f"The AI provider failed on every story ({last_llm_error}). No videos were produced.")
        raise PipelineError(f"LLM unavailable: {last_llm_error}")

    # 3. duplicate prevention + selection
    verified = store.list_stories("verified", limit=100)
    fresh = []
    for story in verified:
        dup = find_duplicate(store, set(story["tokens"]), s.get("dedupe", {}),
                             statuses=("published", "dry_published", "queued", "scripted", "publishing"),
                             exclude_id=story["id"])
        if dup:
            _reject(ctx, story["id"], f"duplicate of {dup['id']}")
            continue
        score, _ = score_story(story, s, multipliers, now)
        story["score"] = score
        store.update_story(story["id"], score=score)
        fresh.append(story)
    chosen = select_stories(fresh, want, float(s.get("score.epsilon", 0.2)), ctx.rng)

    # 4. content intelligence (existing skills + gates)
    kinetic = str(s.get("video.style", "kinetic")) == "kinetic"
    factory = (KineticContentFactory if kinetic else ContentFactory)(s, ctx.llm, ctx.tools, store)
    ai_voice = (not kinetic) and bool(TTS(s).chain())     # kinetic factories decide this themselves (voice_status)
    scripted: list[str] = []
    for story in chosen:
        sid = story["id"]
        try:
            package = factory.build(story, ai_voice)
        except ContentRejected as exc:
            _reject(ctx, sid, str(exc))
            continue
        except Exception as exc:  # noqa: BLE001
            attempts = int(story.get("attempts") or 0) + 1
            store.update_story(sid, attempts=attempts, last_error=f"content: {exc}"[:300])
            if attempts >= 3:
                store.update_story(sid, status="failed")
            log.exception("content generation failed for %s", sid)
            continue
        store.update_story(sid, status="scripted", script={"style": package.get("style", "classic"),
                                                            "scenes": package.get("scenes", []),
                                                            "beats": package["beats"], "gates": package["gates"],
                                                            "hook_rows": package["hook_rows"],
                                                            "visual_keywords": package["visual_keywords"]},
                           caption=package["caption"], hook_formula=package["hook_formula"])
        scripted.append(sid)
        log.info("story %s scripted (hook formula %s)", sid, package["hook_formula"])
    return scripted


# --------------------------------------------------------------------------- render
def _approval_for(ctx: Context, story: dict[str, Any]) -> str:
    mode = ctx.settings.approval_mode
    needs_review = bool((story.get("verification") or {}).get("needs_review"))
    if mode == "auto":
        return "auto"
    if mode == "auto_low_risk" and not needs_review:
        return "auto"
    return "pending"


def _source_line(story: dict[str, Any]) -> str:
    names = [s.get("source") or s.get("domain", "") for s in sorted(story["sources"], key=lambda s: s.get("tier", 3))]
    return "Source: " + ", ".join(list(dict.fromkeys(n for n in names if n))[:2])


def stage_render(ctx: Context) -> list[str]:
    s, store = ctx.settings, ctx.store
    rendered: list[str] = []
    for story in store.list_stories("scripted", limit=20, order="score DESC"):
        sid = story["id"]
        work = s.out_dir / sid
        source_line = _source_line(story)
        try:
            if story["script"].get("style") == "kinetic":
                kin = render_kinetic(story["script"]["scenes"], source_line, work, s, sid)
                result = SimpleNamespace(video=kin.video, duration=kin.duration, silent=not kin.voiced,
                                         providers=[kin.voice_provider], broll=False, music=kin.music)
            else:
                result = render_reel(story["script"]["beats"], source_line,
                                     story["script"].get("visual_keywords", []), work, s, ctx.http, sid, story["title"])
            caption = story["caption"]
            if result.silent:
                line = str(s.get("disclosure.ai_voice_line", ""))
                caption = caption.replace("\n" + line, "") if line else caption
            qa = check_video(result.video, s, expect_audio=not result.silent, caption=caption, source_line=source_line)
            if not qa.passed:
                _reject(ctx, sid, "media: " + "; ".join(qa.issues)[:400])
                continue
            final = s.out_dir / f"{sid}.mp4"
            final.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(result.video, final)
            video_url, queue_key = None, None
            if ctx.host and (not s.dry_run or s.get("publish.upload_in_dry_run", False)):
                queue_key = ctx.host.new_key(sid)
                video_url = ctx.host.upload(final, queue_key)
            approval = _approval_for(ctx, story)
            store.update_story(sid, status="queued", caption=caption, video_path=str(final), video_url=video_url,
                               queue_key=queue_key, approval=approval, last_error=None)
            store.merge_meta(sid, duration_s=result.duration, tts=result.providers, qa=qa.info,
                             broll=result.broll, music=result.music)
            rendered.append(sid)
            log.info("story %s rendered (%.1fs, approval=%s)", sid, result.duration, approval)
            if approval == "pending":
                _send_preview(ctx, sid, final, caption, story)
        except (HostError, Exception) as exc:  # noqa: BLE001
            attempts = int(story.get("attempts") or 0) + 1
            store.update_story(sid, attempts=attempts, last_error=f"render: {exc}"[:300])
            if attempts >= 3:
                store.update_story(sid, status="failed")
            log.exception("render failed for %s", sid)
    return rendered


def _send_preview(ctx: Context, sid: str, video: Path, caption: str, story: dict[str, Any]) -> None:
    if not ctx.telegram:
        log.warning("story %s awaits approval but Telegram is not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)", sid)
        return
    verification = story.get("verification") or {}
    flags = " | ".join(r for r in verification.get("reasons", []) if r.startswith("note:"))
    header = f"{'[DRY RUN] ' if ctx.settings.dry_run else ''}{sid} (confidence {verification.get('confidence', '?')})"
    try:
        ctx.telegram.send_preview(sid, video, f"{header}\n{flags}\n\n{caption}")
        ctx.store.merge_meta(sid, notified=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not send Telegram preview for %s: %s", sid, exc)


# --------------------------------------------------------------------------- approvals + publish
def stage_approvals(ctx: Context) -> int:
    if not ctx.telegram:
        return 0
    applied = 0
    for story_id, decision in ctx.telegram.poll_decisions(ctx.store):
        story = ctx.store.get_story(story_id)
        if not story or story["status"] != "queued":
            continue
        ctx.store.update_story(story_id, approval="approved" if decision == "approve" else "rejected")
        if decision == "reject":
            _release(ctx, story, "rejected")
        applied += 1
        log.info("story %s %sd via Telegram", story_id, decision)
    return applied


def _release(ctx: Context, story: dict[str, Any], status: str) -> None:
    if ctx.host and story.get("queue_key"):
        try:
            ctx.host.delete(story["queue_key"])
        except Exception as exc:  # noqa: BLE001
            log.warning("could not delete hosted video %s: %s", story["queue_key"], exc)
    ctx.store.update_story(story["id"], status=status, queue_key=None, video_url=None)


def _expire_old(ctx: Context) -> None:
    ttl = float(ctx.settings.get("publish.queue_ttl_hours", 36))
    now = utcnow()
    for story in ctx.store.list_stories("queued", limit=200):
        created = parse_iso(story["created_at"])
        if created and (now - created) > timedelta(hours=ttl):
            _release(ctx, story, "expired")
            log.info("story %s expired after %.0fh in queue", story["id"], ttl)
    for story in ctx.store.list_stories("publishing", limit=50):
        updated = parse_iso(story["updated_at"])
        if updated and (now - updated) > timedelta(hours=1):
            ctx.store.update_story(story["id"], status="needs_manual_check")
            ctx.notify(f"Story {story['id']} has been 'publishing' for over an hour. Check Instagram before retrying.")


def stage_publish(ctx: Context) -> list[str]:
    s, store = ctx.settings, ctx.store
    _expire_old(ctx)
    cap = int(s.get("publish.max_posts_per_day", 5))
    if len(store.posts_since(24)) >= cap:
        log.info("daily cap reached (%d); not publishing", cap)
        return []
    last = store.last_post_time()
    gap = float(s.get("publish.min_gap_minutes", 90))
    if last and (utcnow() - last) < timedelta(minutes=gap):
        log.info("last post was %s; waiting for the %.0f minute gap", iso(last), gap)
        return []
    eligible = [st for st in store.list_stories("queued", limit=100, order="score DESC")
                if st["approval"] in ("approved", "auto")]
    if not eligible:
        log.info("nothing approved and queued")
        return []
    published: list[str] = []
    client: InstagramClient | None = None
    for story in eligible[: int(s.get("publish.publish_per_run", 1) or 1)]:
        sid = story["id"]
        meta = story.get("meta") or {}
        domain = min(story["sources"], key=lambda x: x.get("tier", 3)).get("domain") if story["sources"] else None
        if s.dry_run:
            store.add_post(sid, f"dryrun-{sid}", None, True, story.get("pillar"), story.get("hook_formula"), domain,
                           meta.get("duration_s"))
            store.update_story(sid, status="dry_published", published_at=iso(utcnow()))
            log.info("DRY_RUN: would publish %s\n--- caption ---\n%s\n---------------", sid, story["caption"])
            ctx.notify(f"Would publish {sid}: {story['title'][:80]}")
            published.append(sid)
            continue
        token, user_id = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")
        if not token or not user_id:
            raise StopPublishing("IG_ACCESS_TOKEN / IG_USER_ID not set")
        if not story.get("video_url"):
            store.update_story(sid, last_error="publish: no public video_url (configure MEDIA_HOST)")
            raise StopPublishing("no public video_url; configure Supabase or R2 hosting")
        client = client or InstagramClient(token, user_id, ctx.http, str(s.get("publish.api_version", "v25.0")))
        store.update_story(sid, status="publishing")
        try:
            if ctx.host:
                ctx.host.check_public(story["video_url"], ctx.http)
            out = client.publish_reel(story["video_url"], story["caption"],
                                      float(s.get("publish.first_poll_wait_s", 15)),
                                      float(s.get("publish.poll_interval_s", 60)),
                                      float(s.get("publish.max_wait_s", 300)),
                                      on_container=lambda cid, sid=sid: store.merge_meta(sid, container_id=cid))
        except MetaError as exc:
            attempts = int(story.get("attempts") or 0) + 1
            status = "failed" if attempts >= int(s.get("publish.max_attempts", 3)) else "queued"
            store.update_story(sid, status=status, attempts=attempts, last_error=f"publish: {exc}"[:300])
            ctx.notify(f"Publish failed for {sid} ({exc.kind}): {exc}")
            if exc.kind in ("token", "rate_limit"):
                raise StopPublishing(f"{exc.kind}: {exc}") from exc
            continue
        except HostError as exc:
            store.update_story(sid, status="queued", last_error=f"publish: {exc}"[:300])
            continue
        store.add_post(sid, out["media_id"], out.get("permalink"), False, story.get("pillar"),
                       story.get("hook_formula"), domain, meta.get("duration_s"), out["container_id"])
        store.update_story(sid, status="published", published_at=iso(utcnow()))
        if ctx.host and story.get("queue_key"):
            try:
                ctx.host.delete(story["queue_key"])
            except Exception as exc:  # noqa: BLE001
                log.warning("hosted video cleanup failed: %s", exc)
        ctx.notify(f"Published {sid}: {out.get('permalink') or out['media_id']}")
        published.append(sid)
    return published


# --------------------------------------------------------------------------- analytics
def stage_analytics(ctx: Context) -> dict[str, int]:
    token, user_id = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")
    if not token or not user_id:
        log.info("analytics skipped: IG_ACCESS_TOKEN / IG_USER_ID not set")
        return {}
    client = InstagramClient(token, user_id, ctx.http, str(ctx.settings.get("publish.api_version", "v25.0")))
    return collector.collect(ctx.store, ctx.settings, client)


def stage_optimize(ctx: Context) -> dict[str, dict[str, float]]:
    return optimizer.update_weights(ctx.store, ctx.settings)


def refresh_token(ctx: Context) -> str:
    token = env("IG_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("IG_ACCESS_TOKEN is not set")
    data = refresh_long_lived_token(ctx.http, token)
    new = data.get("access_token")
    if not new:
        raise RuntimeError(f"no access_token in refresh response: {list(data)}")
    log.info("token refreshed; expires_in=%s seconds", data.get("expires_in"))
    return new
