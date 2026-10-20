"""Settings, paths and logging for the newsroom pipeline.

Configuration precedence (lowest to highest):
  1. DEFAULT_CFG in this file
  2. config/settings.yaml
  3. environment variables (secrets and the DRY_RUN switch live here)
"""
from __future__ import annotations

import copy
import json
import logging
import logging.handlers
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
TRUE_VALUES = {"1", "true", "yes", "on", "y"}


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_bool(name: str, default: bool) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.lower() in TRUE_VALUES


def env_int(name: str, default: int) -> int:
    value = env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


DEFAULT_CFG: dict[str, Any] = {
    "brand": {
        "name": "The Daily Byte India",
        "handle": "@dailybyteindia",
        "tagline": "AI, tech and business in 30 seconds",
        "language": "en",
        "accent": "#FFB800",
        "bg_top": "#0B1220",
        "bg_bottom": "#1B2B4B",
        "text": "#FFFFFF",
    },
    "collect": {"max_age_hours": 36, "max_items_per_feed": 25},
    "dedupe": {
        "cluster_jaccard": 0.40,
        "cluster_overlap": 0.65,
        "cluster_min_shared": 3,
        "history_days": 14,
        "posted_block_days": 7,
        "posted_jaccard": 0.50,
    },
    "research": {"max_sources": 3, "max_chars_per_source": 6000, "min_chars": 250, "max_facts": 6},
    "verify": {
        "min_independent_sources": 2,
        "min_facts": 3,
        "max_age_hours": 36,
        "allow_single_tier1": True,
        "rumor_patterns": [
            r"\brumou?rs?\b", r"\breportedly\b", r"\ballegedly\b", r"\bunconfirmed\b",
            r"\bsources? (?:say|said|claim|claimed)\b", r"\bleak(?:ed|s)?\b", r"\bspeculat\\w+\b",
        ],
        "sensitive_keywords": [
            "death", "dies", "killed", "murder", "arrest", "riot", "communal", "religion", "religious",
            "election", "vote", "war", "terror", "rape", "suicide", "lawsuit", "court", "scam", "fraud",
            "vaccine", "cure", "cancer", "outbreak",
        ],
        "mascot_block_keywords": [
            "fire", "crash", "crashed", "blast", "explosion", "injured", "injury", "dead", "death", "died", "killed",
            "attack", "shooting", "collapse", "accident", "hospital", "emergency", "terror", "flood", "earthquake",
            "war", "arrest", "scam", "fraud", "suicide", "protest", "riot", "layoffs", "bankrupt", "smoke", "diverted",
        ],
        "reject_patterns": [
            r"\btarget price\b", r"\bmultibagger\b", r"\bstock tips?\b", r"\bbuy rating\b",
            r"\bsell rating\b", r"\bshould you (?:buy|sell|invest)\b", r"\bbest stocks?\b",
            r"\bcrypto price prediction\b",
        ],
    },
    "score": {
        "half_life_hours": 12.0,
        "epsilon": 0.2,
        "weights": {
            "recency": 0.25,
            "cross_source": 0.25,
            "tier": 0.15,
            "fit": 0.15,
            "trend": 0.10,
            "potential": 0.10,
        },
    },
    "content": {
        "target_seconds": 24,
        "min_seconds": 15,
        "max_seconds": 30,
        "max_words": 68,
        "target_words": 58,
        "max_rewrites": 1,
        "max_hook_seconds": 3.4,
        "cta_options": [
            "Send this to someone who works in tech.",
            "Save this for later.",
            "Follow for the daily 30 second brief.",
            "Share this with a friend who follows business news.",
        ],
    },
    "gates": {
        "hook_min": 55.0,
        "human_min": 60.0,
        "upstream_strict": False,
        "humanize_mode": "builtin",
    },
    "media": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "tts_provider": "auto",
        "voice_en": "en-IN-NeerjaNeural",
        "voice_hi": "hi-IN-SwaraNeural",
        "google_voice": "en-IN-Chirp3-HD-Charon",
        "speech_rate": "+5%",
        "beat_gap_s": 0.18,
        "end_pad_s": 0.5,
        "music_mode": "auto",          # auto (your tracks, else synthesised pad) | files | synth | off
        "music_dir": "assets/music",   # put royalty-free .mp3/.wav tracks here
        "music_volume": 0.11,
        "sfx": True,
        "sfx_volume": 0.45,
        "loudness": -15,
        "broll": True,                 # needs the free PEXELS_API_KEY; otherwise animated gradient + icons
        "use_pexels": True,
        "allow_silent": True,
        "font_bold": "",
        "font_regular": "",
    },
    "qa": {"min_seconds": 12.0, "max_seconds": 60.0, "max_mb": 90, "min_luma": 12.0, "min_fps": 23},
    "publish": {
        "max_posts_per_day": 5,
        "min_gap_minutes": 90,
        "queue_target": 4,
        "queue_ttl_hours": 36,
        "publish_per_run": 1,
        "first_poll_wait_s": 15,
        "poll_interval_s": 60,
        "max_wait_s": 300,
        "api_version": "v25.0",
        "max_attempts": 3,
        "upload_in_dry_run": False,
    },
    "analytics": {
        "checkpoints_hours": [24, 72, 168],
        "metrics": [
            "reach",
            "views",
            "likes",
            "comments",
            "shares",
            "saved",
            "total_interactions",
            "ig_reels_avg_watch_time",
        ],
    },
    "optimize": {"shrink_k": 4.0, "min_mult": 0.7, "max_mult": 1.4, "min_posts": 5},
    "disclosure": {"enabled": True, "ai_voice_line": "AI-generated voice."},
    "mascot": {"enabled": True, "probability": 0.6, "max_beats": 3},
    "voice": {                              # voice-over for the kinetic style
        "mode": "on",                       # on = best available voice (premium if configured, else free edge voice) | auto = premium only | off
        "allow_basic": False,               # allow the free/basic voices (edge-tts, Azure) as a fallback
        "elevenlabs_model": "eleven_multilingual_v2",   # eleven_flash_v2_5 costs half
        "stability": 0.5, "similarity": 0.8, "style": 0.2, "speed": 1.0,
        "voice_max_words": 62, "voice_min_words": 30,
    },
    "video": {"style": "kinetic"},          # kinetic (text + beat, no voice, default) | classic (voice-over)
    "kinetic": {"bpm": 108, "fps": 30, "max_words": 60, "min_words": 20, "closer_sub": "Tell me below."},
    "pillars_default": "tech",
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data or {}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass
class Settings:
    root: Path
    dry_run: bool
    approval_mode: str
    db_path: Path
    out_dir: Path
    log_dir: Path
    cfg: dict
    sources: dict
    pillars: dict
    news_hooks: dict
    offline: bool = False
    extra: dict = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.cfg
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def secret(self, name: str) -> str | None:
        return env(name)


def load_settings(root: Path | None = None, **overrides: Any) -> Settings:
    root = Path(root) if root else Path(env("NEWSROOM_ROOT", str(DEFAULT_ROOT)))
    cfg = deep_merge(DEFAULT_CFG, load_yaml(root / "config" / "settings.yaml"))
    env_cfg = env("NEWSROOM_CFG")          # optional JSON string merged on top (handy for tests and quick experiments)
    if env_cfg:
        try:
            cfg = deep_merge(cfg, json.loads(env_cfg))
        except ValueError:
            logging.getLogger(__name__).warning("NEWSROOM_CFG is not valid JSON; ignored")
    if overrides.get("cfg"):
        cfg = deep_merge(cfg, overrides["cfg"])
    sources_path = Path(overrides.get("sources_path") or env("NEWSROOM_SOURCES", str(root / "config" / "sources.yaml")))
    approval = (overrides.get("approval_mode") or env("APPROVAL_MODE", "telegram") or "telegram").lower()
    if approval not in {"telegram", "auto_low_risk", "auto"}:
        approval = "telegram"
    db_path = Path(overrides.get("db_path") or env("NEWSROOM_DB", str(root / "state" / "newsroom.sqlite3")))
    out_dir = Path(overrides.get("out_dir") or env("NEWSROOM_OUT", str(root / "out")))
    log_dir = Path(overrides.get("log_dir") or env("NEWSROOM_LOGS", str(root / "logs")))
    dry_run = overrides["dry_run"] if "dry_run" in overrides else env_bool("DRY_RUN", True)
    offline = overrides["offline"] if "offline" in overrides else env_bool("NEWSROOM_OFFLINE", False)
    return Settings(
        root=root,
        dry_run=bool(dry_run),
        approval_mode=approval,
        db_path=db_path,
        out_dir=out_dir,
        log_dir=log_dir,
        cfg=cfg,
        sources=load_yaml(sources_path),
        pillars=load_yaml(root / "config" / "pillars.yaml"),
        news_hooks=load_json(root / "config" / "news_hooks.json"),
        offline=bool(offline),
    )


def setup_logging(settings: Settings | None = None, level: str | None = None) -> None:
    level_name = (level or env("LOG_LEVEL", "INFO") or "INFO").upper()
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level_name, logging.INFO))
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root_logger.addHandler(console)
    if settings is not None:
        try:
            settings.log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                settings.log_dir / "newsroom.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(fmt)
            root_logger.addHandler(file_handler)
        except OSError:
            root_logger.warning("could not create log file in %s", settings.log_dir)
