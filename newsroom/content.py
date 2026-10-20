"""Content intelligence: script, hook, caption, humanising and quality gates (drives the upstream skills)."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from . import prompts
from .gates import (UpstreamTools, clean_text, gate_beats, gate_caption, gate_hooks, gate_human)
from .llm.skill_adapter import load_skill_guidance
from .media.icons import ICON_NAMES, MOODS
from .pillars import pillar_hashtags
from .settings import Settings
from .store import Store
from .textutil import extract_numbers, truncate_words, word_count

log = logging.getLogger(__name__)


class ContentRejected(Exception):
    """The story could not be turned into a script that passes every gate."""


FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|I'll|me|my|mine|we|we're|we've|our|ours|us)\b")


def _mood(value: Any) -> str:
    value = str(value or "none").strip().lower()
    return value if value in MOODS else "none"


def _icon(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in ICON_NAMES else ""


def _as_beat(value: Any, default_screen: str = "") -> dict[str, str]:
    if isinstance(value, str):
        return {"say": value.strip(), "screen": default_screen, "mood": "none", "icon": ""}
    if isinstance(value, dict) and str(value.get("say", "")).strip():
        return {"say": str(value["say"]).strip(), "screen": truncate_words(str(value.get("screen", "")).strip(), 6),
                "mood": _mood(value.get("mood")), "icon": _icon(value.get("icon"))}
    raise ContentRejected("script beat missing 'say'")


def mascot_allowed(story: dict[str, Any], settings: Settings) -> bool:
    """The mascot is for light stories only: never for sensitive, flagged or harm-related news."""
    cfg = settings.get("mascot", {}) or {}
    if not cfg.get("enabled", True):
        return False
    verification = story.get("verification") or {}
    if verification.get("meme_blocked") or verification.get("needs_review") or verification.get("sensitivity") == "high":
        return False
    bucket = int(hashlib.sha1(str(story.get("id", "")).encode()).hexdigest()[:6], 16) % 100
    return bucket < int(float(cfg.get("probability", 0.6)) * 100)


def normalize_script(raw: dict[str, Any]) -> dict[str, Any]:
    hooks_in = raw.get("hook_candidates") or []
    hooks = []
    for h in hooks_in:
        if isinstance(h, str):
            h = {"spoken": h, "on_screen": "", "formula_id": None}
        spoken = str(h.get("spoken", "")).strip()
        if spoken:
            hooks.append({"spoken": spoken, "on_screen": truncate_words(str(h.get("on_screen", "")).strip(), 6),
                          "formula_id": h.get("formula_id"), "mood": _mood(h.get("mood"))})
    if not hooks:
        raise ContentRejected("script has no hook candidates")
    main = [_as_beat(m) for m in (raw.get("main") or [])]
    if len(main) < 2:
        raise ContentRejected("script needs at least 2 main beats")
    return {
        "language": raw.get("language", "en"),
        "hook_candidates": hooks,
        "context": _as_beat(raw.get("context") or ""),
        "main": main[:3],
        "twist": _as_beat(raw.get("twist") or ""),
        "cta": _as_beat(raw.get("cta") or ""),
        "visual_keywords": [str(k) for k in (raw.get("visual_keywords") or [])][:4],
    }


def spoken_lines(script: dict[str, Any], hook: str) -> list[str]:
    return [hook, script["context"]["say"], *[m["say"] for m in script["main"]],
            script["twist"]["say"], script["cta"]["say"]]


def trim_first_line(text: str, limit: int = 110) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + ("." if not cut.endswith((".", "!", "?")) else "")


def clean_hashtags(candidates: list[str], extra: list[str], limit: int = 5) -> list[str]:
    out: list[str] = []
    for tag in list(candidates) + list(extra):
        tag = re.sub(r"[^a-z0-9]", "", str(tag).lower().lstrip("#"))
        if tag and tag not in out and tag not in {"fyp", "viral", "foryou", "foryoupage"}:
            out.append(tag)
    return out[:limit]


def assemble_caption(parts: dict[str, Any], source_names: list[str], extra_tags: list[str],
                     settings: Settings, ai_voice: bool) -> str:
    lines = [trim_first_line(str(parts.get("first_line", ""))), "", str(parts.get("body", "")).strip(), "",
             str(parts.get("cta", "")).strip(), "", "Source: " + ", ".join(dict.fromkeys(source_names[:3]))]
    if ai_voice and settings.get("disclosure.enabled", True):
        lines.append(str(settings.get("disclosure.ai_voice_line", "AI-generated voice.")))
    tags = clean_hashtags(parts.get("hashtags") or [], extra_tags)
    if tags:
        lines += ["", " ".join("#" + t for t in tags)]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class ContentFactory:
    def __init__(self, settings: Settings, llm: Any, tools: UpstreamTools, store: Store) -> None:
        self.settings = settings
        self.llm = llm
        self.tools = tools
        self.store = store
        brand = settings.get("brand.name", "the brand")
        self.script_system = prompts.SCRIPT_SYSTEM.format(
            brand=brand, max_words=settings.get("content.max_words", 95),
            guidance="\n" + load_skill_guidance(settings.root, "ig-reel"))
        self.caption_system = prompts.CAPTION_SYSTEM.format(
            brand=brand, guidance="\n" + load_skill_guidance(settings.root, "ig-caption", 2500))

    # ----------------------------------------------------------------- hooks
    def _formulas(self, limit: int = 6) -> list[dict[str, Any]]:
        learned = self.store.get_weights("hook_formula")
        formulas = list((self.settings.news_hooks or {}).get("formulas", []))
        formulas.sort(key=lambda f: -(float(f.get("prior", 1.0)) * learned.get(str(f["id"]), 1.0)))
        return formulas[:limit]

    # ------------------------------------------------------------ checks
    def _problems_for(self, script: dict[str, Any], facts_blob: str, title: str) -> tuple[list[str], str | None, list[dict], list[dict]]:
        """Run every script-level check. Returns (problems, chosen_hook, gate_dicts, hook_rows)."""
        problems: list[str] = []
        gates: list[dict] = []
        cfg = self.settings.get("content", {}) or {}

        hooks = [h["spoken"] for h in script["hook_candidates"]]
        best_index, hook_rows, hook_gate = gate_hooks(hooks, self.tools, self.settings)
        gates.append(hook_gate.to_dict())
        if best_index is None:
            worst = "; ".join(r["dealbreaker"] for r in hook_rows if r["dealbreaker"])[:200]
            problems.append(f"hooks too weak (best {hook_gate.score}). {worst} Use a number, a name or a consequence in the first 12 words.")
            return problems, None, gates, hook_rows
        hook = hooks[best_index]

        lines = spoken_lines(script, hook)
        full_text = " ".join(lines)
        allowed_numbers = extract_numbers(facts_blob) | extract_numbers(title)
        missing = sorted(extract_numbers(full_text) - allowed_numbers)
        if missing:
            problems.append("these numbers are not in FACTS, remove or replace them: " + ", ".join(missing))
        found = FIRST_PERSON.findall(full_text)
        if found:
            problems.append(f"first-person words found ({', '.join(sorted(set(found)))}); write in third person only")
        if word_count(full_text) > int(cfg.get("max_words", 95)):
            problems.append(f"too long: {word_count(full_text)} words, maximum {cfg.get('max_words', 95)}")

        cleaned = [clean_text(line, self.tools, self.settings).text for line in lines]
        beats_gate = gate_beats(cleaned, self.tools, self.settings)
        gates.append(beats_gate.to_dict())
        if not beats_gate.passed:
            problems.extend(beats_gate.details)
        human_gate = gate_human(" ".join(cleaned), self.tools, self.settings)
        gates.append(human_gate.to_dict())
        if not human_gate.passed:
            problems.append("sounds robotic: " + "; ".join(human_gate.details)[:200])
        return problems, hook, gates, hook_rows

    # ------------------------------------------------------------- script
    def build_script(self, story: dict[str, Any]) -> dict[str, Any]:
        facts = story["facts"]
        facts_blob = " ".join(f["text"] + " " + f.get("evidence", "") for f in facts)
        max_rewrites = int(self.settings.get("content.max_rewrites", 1))
        formulas = self._formulas()
        ctas = list(self.settings.get("content.cta_options", []))
        problems: list[str] = []
        last_problems: list[str] = []
        for attempt in range(max_rewrites + 1):
            prompt = prompts.script_prompt(story["title"], story.get("pillar") or "tech", facts, formulas, ctas,
                                           problems, mascot_allowed(story, self.settings))
            payload = {"title": story["title"], "pillar": story.get("pillar"), "facts": facts, "formulas": formulas,
                       "cta_options": ctas, "feedback": problems}
            raw = self.llm.json("write_script", self.script_system, prompt, payload, temperature=0.5, max_tokens=1800)
            script = normalize_script(raw)
            problems, hook, gates, hook_rows = self._problems_for(script, facts_blob, story["title"])
            if hook and not problems:
                chosen = next(h for h in script["hook_candidates"] if h["spoken"] == hook)
                return {"script": script, "hook": hook, "hook_on_screen": chosen["on_screen"],
                        "hook_mood": chosen.get("mood", "none"),
                        "hook_formula": chosen.get("formula_id"), "gates": gates, "hook_rows": hook_rows}
            last_problems = list(problems)
            log.info("story %s attempt %d failed checks: %s", story["id"], attempt + 1, problems)
        raise ContentRejected("content: " + " | ".join(last_problems)[:400])

    def beats_from_script(self, script: dict[str, Any], hook: str, hook_on_screen: str, hook_mood: str = "none",
                          mascot: bool = False) -> list[dict[str, str]]:
        def c(text: str) -> str:
            return clean_text(text, self.tools, self.settings).text

        beats = [{"kind": "hook", "say": c(hook), "screen": hook_on_screen or truncate_words(hook, 5),
                  "mood": hook_mood, "icon": ""},
                 {"kind": "context", "say": c(script["context"]["say"]),
                  "screen": script["context"]["screen"] or truncate_words(script["context"]["say"], 5),
                  "mood": script["context"]["mood"], "icon": script["context"]["icon"]}]
        for i, m in enumerate(script["main"]):
            beats.append({"kind": "point", "say": c(m["say"]), "screen": m["screen"] or truncate_words(m["say"], 5),
                          "n": str(i + 1), "mood": m["mood"], "icon": m["icon"]})
        beats.append({"kind": "twist", "say": c(script["twist"]["say"]),
                      "screen": script["twist"]["screen"] or truncate_words(script["twist"]["say"], 5),
                      "mood": script["twist"]["mood"], "icon": script["twist"]["icon"]})
        beats.append({"kind": "cta", "say": c(script["cta"]["say"]), "screen": script["cta"]["screen"] or "FOLLOW",
                      "mood": script["cta"]["mood"] if script["cta"]["mood"] != "none" else "celebrating", "icon": ""})
        # mechanical shortening if the estimated spoken time is over the maximum
        hi = float(self.settings.get("content.max_seconds", 30))
        while sum(word_count(b["say"]) for b in beats) / 165.0 * 60.0 > hi and sum(b["kind"] == "point" for b in beats) > 2:
            idx = max(i for i, b in enumerate(beats) if b["kind"] == "point")
            beats.pop(idx)
        # mascot policy: off entirely for sensitive stories, otherwise at most N beats
        limit = int((self.settings.get("mascot", {}) or {}).get("max_beats", 3))
        shown = 0
        for beat in beats:
            if not mascot or beat["mood"] == "none" or shown >= limit:
                beat["mood"] = "none"
            else:
                shown += 1
        return beats

    # ------------------------------------------------------------ caption
    def build_caption(self, story: dict[str, Any], beats: list[dict[str, str]], ai_voice: bool) -> tuple[str, dict]:
        hook = beats[0]["say"]
        context = beats[1]["say"]
        keywords = sorted({w for w in re.findall(r"[A-Za-z]{4,}", story["title"])}, key=len, reverse=True)[:3]
        tags = pillar_hashtags(story.get("pillar") or "tech", self.settings.pillars)
        facts = story["facts"]
        prompt = prompts.CAPTION_USER.format(hook=hook, context=context, facts=prompts.format_facts(facts[:4]),
                                             cta=beats[-1]["say"], keywords=", ".join(keywords), hashtags=", ".join(tags))
        payload = {"hook": hook, "context": context, "cta": beats[-1]["say"], "hashtags": tags, "keywords": keywords}
        raw = self.llm.json("write_caption", self.caption_system, prompt, payload, temperature=0.4, max_tokens=700)
        source_names = [s.get("source") or s.get("domain", "") for s in sorted(story["sources"], key=lambda s: s.get("tier", 3))]
        caption = assemble_caption(raw, [n for n in source_names if n], tags, self.settings, ai_voice)
        caption = clean_text(caption, self.tools, self.settings).text
        gate = gate_caption(caption, keywords, self.tools, self.settings)
        if not gate.passed:
            raise ContentRejected("caption: " + "; ".join(gate.details)[:300])
        return caption, gate.to_dict()

    # --------------------------------------------------------------- build
    def build(self, story: dict[str, Any], ai_voice: bool) -> dict[str, Any]:
        result = self.build_script(story)
        beats = self.beats_from_script(result["script"], result["hook"], result["hook_on_screen"],
                                       result.get("hook_mood", "none"), mascot_allowed(story, self.settings))
        caption, caption_gate = self.build_caption(story, beats, ai_voice)
        return {
            "beats": beats,
            "caption": caption,
            "hook": result["hook"],
            "hook_formula": result["hook_formula"],
            "visual_keywords": result["script"]["visual_keywords"],
            "gates": result["gates"] + [caption_gate],
            "hook_rows": result["hook_rows"],
        }
