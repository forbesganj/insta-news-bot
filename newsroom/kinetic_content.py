"""Content intelligence for the kinetic style: on-screen scenes (no voice-over) with all the usual gates."""
from __future__ import annotations

import logging
import re
from typing import Any

from . import prompts
from .content import FIRST_PERSON, ContentFactory, ContentRejected
from .gates import clean_text, gate_beats, gate_hooks, gate_human
from .media.icons import ICON_NAMES
from .media.tts import voice_status
from .models import GateResult
from .textutil import extract_numbers, word_count

log = logging.getLogger(__name__)

KINETIC_KINDS = {"detail", "alert", "relief", "stat", "twist"}

KINETIC_SYSTEM = """You write the on-screen text for a ~20 second Instagram Reel from "{brand}", a faceless Indian AI/tech/business news account. The Reel has NO voice-over: the text on screen IS the content. It is shown one idea per screen with hard cuts, the way a sharp human news creator would make it.
HARD RULES
- Use ONLY the facts in FACTS. Every number, name, date and place must appear in FACTS exactly. Never round, convert or add anything. Write numbers as digits.
- Third person only. Never use "I", "me", "my", "we" or "our": this is a news brand, not a person.
- Original wording. No financial advice, no buy/sell calls, no predictions presented as fact. Attribute anything unconfirmed ("reportedly").
STYLE (this is what makes it feel human, not AI)
- Plain words, specific nouns, short punchy fragments. Contractions are fine.
- Never use: "here's why", "let's dive in", "game-changer", "in today's world", "it's not X, it's Y", lists of three adjectives, hype words, emojis, hashtags, exclamation marks.
- Each LINE is a fragment of 1 to 4 words. Big lines are written in CAPITALS. An optional "sub" is a small caption under the big lines (max 6 words, normal case).
- The whole Reel has at most {max_words} words in total. Cut everything that is not needed.
STRUCTURE
- hook: max 8 words over 2-3 lines. Within 2 seconds it must create curiosity, stakes or a question. Never start with only a company name, "Breaking" or "Today".
- then 3 to 5 scenes in a natural story order. kind is one of: detail, alert (only for a real shock or problem), relief (only for a genuine good outcome), stat (one big number), twist (the surprising detail or what it means for the viewer).
- closer: a short, genuine question to the viewer about the story (max 8 words over 2-3 lines) that makes people want to comment, plus sub "Tell me below."
- "accent" is the index (0-based) of the line to emphasise (it is shown larger and in colour).
GRAPHICS (optional per scene; use at least two different graphics across the Reel when FACTS allow, because variety keeps people watching):
- none
- route: only for a flight or journey; set "label" to the destination city exactly as written in FACTS
- check: only with kind relief
- smoke: only if FACTS mention smoke or fire
- pulse: alerts
- icon:<plane|battery|phone|rupee|chart|chip|shield|rocket|card|globe|shop|user|spark>
- counter: the key number counts up on screen. Put it in "data": {{"value": <a number that appears in FACTS>, "unit": "billion payments", "prefix": "", "suffix": ""}}
- bars: 2 to 5 comparable numbers from FACTS grow as a bar chart. "data": {{"labels": ["2025", "2026"], "values": [<numbers from FACTS>]}}
- flow: 2 or 3 steps showing how something works. "data": {{"nodes": [{{"icon": "user", "label": "YOU"}}, {{"icon": "phone", "label": "APP"}}, {{"icon": "shop", "label": "SHOP"}}]}}
- phone: a phone completing a payment, only for payment or app stories. "data": {{"amount": "<amount from FACTS>", "status": "PAYMENT SENT"}}
Never put a number in "data" that is not in FACTS.
Return JSON only. No markdown, no commentary.
{narration}
{guidance}"""

NARRATION_RULES = """NARRATION (a real newsreader will speak this; it is what viewers mainly hear)
- The hook, every scene and the closer each get a "say": what the anchor says over that screen. It carries the same facts as the on-screen text, in natural spoken sentences.
- Write for the ear, like a senior TV news anchor: calm, confident, conversational. Sentences of 5 to 14 words, varied rhythm, contractions are fine. No filler, no clichés, no exclamation marks.
- The hook "say" is at most 9 words. Total narration across all scenes is 36 to 62 words (about 20 seconds).
- Write numbers as digits. Avoid abbreviations a voice could misread, except UPI, RBI, GST, AI, IPO, EV, GPU, CEO which are handled. Spell out other abbreviations.
- The closer "say" is the question to the viewer."""

KINETIC_USER = """STORY: {title}
PILLAR: {pillar}

FACTS (the only allowed source of information):
{facts}

HOOK FORMULAS you may borrow from (give the formula id of the closest one):
{formulas}
{feedback}
Return exactly this JSON shape:
{{
  "hook_candidates": [
    {{"formula_id": 23, "lines": ["A POWER BANK", "TURNED", "A FLIGHT BACK"], "accent": 1}},
    {{"formula_id": 24, "lines": ["...", "..."], "accent": 0}},
    {{"formula_id": 26, "lines": ["...", "..."], "accent": 0}}
  ],
  "scenes": [
    {{"kind": "detail", "lines": ["INDIGO 6E 6115", "SUNDAY"], "sub": "", "accent": 0, "graphic": "route", "label": "Hyderabad"}},
    {{"kind": "alert", "lines": ["SMOKE."], "sub": "from a device", "accent": 0, "graphic": "smoke"}},
    {{"kind": "relief", "lines": ["LANDED SAFELY."], "sub": "", "accent": 0, "graphic": "check"}}
  ],
  "closer": {{"lines": ["DO YOU CARRY", "A POWER BANK", "ON FLIGHTS?"], "sub": "Tell me below.", "accent": 2}}
}}"""


def kinetic_prompt(title: str, pillar: str, facts: list[dict[str, Any]], formulas: list[dict[str, Any]],
                   problems: list[str], voice: bool = False) -> str:
    text = KINETIC_USER.format(title=title, pillar=pillar, facts=prompts.format_facts(facts),
                               formulas=prompts.format_formulas(formulas), feedback=prompts.feedback_block(problems))
    if voice:
        text = (text.replace('"accent": 1},\n    {"formula_id": 24', '"accent": 1, "say": "A power bank forced an IndiGo flight back."},\n    {"formula_id": 24')
                    .replace('"graphic": "route", "label": "Hyderabad"}', '"graphic": "route", "label": "Hyderabad", "say": "Flight 6E 6115 was diverted to Hyderabad on Sunday."}')
                    .replace('"sub": "Tell me below.", "accent": 2}', '"sub": "Tell me below.", "accent": 2, "say": "Do you carry a power bank when you fly?"}'))
    return text


def _lines(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    return [re.sub(r"\s+", " ", str(l)).strip() for l in (raw or []) if str(l).strip()]


def _accent(value: Any, lines: list[str]) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    return n if 0 <= n < len(lines) else 0


def normalize_kinetic(raw: dict[str, Any], closer_sub: str = "Tell me below.") -> dict[str, Any]:
    hooks = []
    for h in raw.get("hook_candidates") or []:
        if not isinstance(h, dict):
            continue
        lines = _lines(h.get("lines") or h.get("spoken"))[:3]
        if lines:
            hooks.append({"lines": lines, "accent": _accent(h.get("accent"), lines), "formula_id": h.get("formula_id"),
                          "spoken": " ".join(lines), "say": re.sub(r"\s+", " ", str(h.get("say") or "")).strip()})
    if not hooks:
        raise ContentRejected("script has no hook candidates")
    scenes = []
    for sc in raw.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        lines = _lines(sc.get("lines"))[:4]
        if not lines:
            continue
        kind = str(sc.get("kind", "detail")).lower()
        scenes.append({"kind": kind if kind in KINETIC_KINDS else "detail", "lines": lines,
                       "sub": re.sub(r"\s+", " ", str(sc.get("sub") or "")).strip(),
                       "accent": _accent(sc.get("accent"), lines),
                       "graphic": str(sc.get("graphic") or "none").lower(), "label": str(sc.get("label") or "").strip(),
                       "data": sc.get("data") if isinstance(sc.get("data"), dict) else {},
                       "say": re.sub(r"\s+", " ", str(sc.get("say") or "")).strip()})
    closer = raw.get("closer") if isinstance(raw.get("closer"), dict) else {}
    c_lines = _lines(closer.get("lines"))[:3]
    if not c_lines:
        raise ContentRejected("script has no closing question")
    return {"hooks": hooks, "scenes": scenes,
            "closer": {"kind": "closer", "lines": c_lines, "sub": str(closer.get("sub") or closer_sub).strip(),
                       "accent": _accent(closer.get("accent"), c_lines) if closer.get("accent") is not None else len(c_lines) - 1,
                       "graphic": "none", "label": "", "say": re.sub(r"\s+", " ", str(closer.get("say") or "")).strip()}}


def _as_number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _grounded(value: Any, allowed: set[str]) -> bool:
    """A displayed number must appear in the facts (two or more digits, or a decimal)."""
    number = _as_number(value)
    if number is None:
        return False
    return f"{number:g}" in allowed or str(value).replace(",", "").strip() in allowed or f"{number:.1f}" in allowed


def sanitize_data_graphic(sc: dict[str, Any], allowed: set[str], low: str) -> str:
    g, data = sc.get("graphic", "none"), dict(sc.get("data") or {})
    if g == "counter":
        if not _grounded(data.get("value"), allowed):
            return "none"
        data = {"value": _as_number(data["value"]), "unit": str(data.get("unit", ""))[:40],
                "prefix": str(data.get("prefix", ""))[:3], "suffix": str(data.get("suffix", ""))[:3]}
    elif g == "bars":
        values = data.get("values") if isinstance(data.get("values"), list) else []
        labels = data.get("labels") if isinstance(data.get("labels"), list) else []
        if not 2 <= len(values) <= 5 or not all(_grounded(v, allowed) for v in values):
            return "none"
        data = {"values": [_as_number(v) for v in values],
                "labels": [str(l)[:10] for l in labels][: len(values)],
                "decimals": 1 if any(not float(_as_number(v)).is_integer() for v in values) else 0}
    elif g == "flow":
        nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
        clean = [{"icon": n.get("icon") if n.get("icon") in ICON_NAMES else "spark", "label": str(n.get("label", ""))[:10]}
                 for n in nodes if isinstance(n, dict)][:3]
        if len(clean) < 2:
            return "none"
        data = {"nodes": clean}
    elif g == "phone":
        if not re.search(r"payment|\bpay\b|upi|wallet|transfer|\bapp\b|bank", low):
            return "icon:phone"
        amount = str(data.get("amount", "")).strip()
        digits = re.findall(r"\d[\d,]*(?:\.\d+)?", amount)
        if digits and not all(_grounded(d, allowed) for d in digits):
            amount = ""
        data = {"amount": amount, "status": str(data.get("status", "PAYMENT SENT"))[:18]}
    sc["data"] = data
    return g


def sanitize_graphics(scenes: list[dict[str, Any]], facts_blob: str) -> None:
    """Never show a graphic the facts do not support (e.g. smoke for a story with no smoke, or a number not in the facts)."""
    low = facts_blob.lower()
    allowed = extract_numbers(facts_blob)
    for sc in scenes:
        g, kind = sc.get("graphic", "none"), sc["kind"]
        if g in ("counter", "bars", "flow", "phone"):
            sc["graphic"] = sanitize_data_graphic(sc, allowed, low)
            continue
        if g == "check" and kind != "relief":
            g = "none"
        elif g == "smoke" and not re.search(r"smoke|fire|burn|blaze", low):
            g = "pulse" if kind == "alert" else "none"
        elif g == "route" and (not sc.get("label") or sc["label"].lower() not in low):
            g, sc["label"] = "icon:plane", ""
        elif g == "pulse" and kind != "alert":
            g = "none"
        elif g.startswith("icon:") and g.split(":", 1)[1] not in ICON_NAMES:
            g = "none"
        elif g not in ("none", "route", "check", "smoke", "pulse", "counter", "bars", "flow", "phone") and not g.startswith("icon:"):
            g = "none"
        if kind == "relief" and g == "none":
            g = "check"
        if kind == "alert" and g == "none":
            g = "pulse"
        sc["graphic"] = g


class KineticContentFactory(ContentFactory):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from .llm.skill_adapter import load_skill_guidance

        self.voice_on, self.voice_provider = voice_status(self.settings)
        self.script_system = KINETIC_SYSTEM.format(
            brand=self.settings.get("brand.name", "the brand"),
            max_words=int(self.settings.get("kinetic.max_words", 60)),
            narration=NARRATION_RULES if self.voice_on else "",
            guidance="\n" + load_skill_guidance(self.settings.root, "ig-reel", 2500))

    # ------------------------------------------------------------------ cleaning
    def _clean(self, norm: dict[str, Any]) -> None:
        def c(text: str) -> str:
            return clean_text(text, self.tools, self.settings).text if text else ""

        for hook in norm["hooks"]:
            hook["say"] = c(hook.get("say", ""))
            hook["lines"] = [x for x in (c(l) for l in hook["lines"]) if x]
            hook["accent"] = min(hook["accent"], max(len(hook["lines"]) - 1, 0))
            hook["spoken"] = " ".join(hook["lines"])
        norm["hooks"] = [h for h in norm["hooks"] if h["lines"]]
        cleaned = []
        for sc in norm["scenes"] + [norm["closer"]]:
            sc["lines"] = [x for x in (c(l) for l in sc["lines"]) if x]
            sc["sub"] = c(sc["sub"])
            sc["say"] = c(sc.get("say", ""))
            if sc["lines"]:
                sc["accent"] = min(sc["accent"], len(sc["lines"]) - 1)
                cleaned.append(sc)
        norm["closer"] = next((s for s in cleaned if s["kind"] == "closer"), norm["closer"])
        norm["scenes"] = [s for s in cleaned if s["kind"] != "closer"]

    # ------------------------------------------------------------------ checks
    def _problems(self, norm: dict[str, Any], facts_blob: str, title: str):  # noqa: ANN202
        cfg = self.settings.get("kinetic", {}) or {}
        problems: list[str] = []
        gates: list[dict] = []
        if not norm["hooks"]:
            return ["no usable hook"], None, gates, []
        voice = self.voice_on
        hooks = [(h["say"] or h["spoken"]) if voice else h["spoken"] for h in norm["hooks"]]
        best, rows, hook_gate = gate_hooks(hooks, self.tools, self.settings)
        gates.append(hook_gate.to_dict())
        if best is None:
            worst = "; ".join(r["dealbreaker"] for r in rows if r["dealbreaker"])[:200]
            problems.append(f"hooks too weak (best {hook_gate.score}). {worst} Use a number, a name or a consequence "
                            f"in the first 8 words.")
            return problems, None, gates, rows
        hook = norm["hooks"][best]
        scenes = [{"kind": "hook", "lines": hook["lines"], "sub": "", "accent": hook["accent"], "graphic": "none",
                   "label": "", "say": hook.get("say", "")}] + norm["scenes"] + [norm["closer"]]
        texts = [" ".join(s["lines"] + ([s["sub"]] if s["sub"] else [])) for s in scenes]
        text_all = " ".join(texts)
        # the closer's small call-to-action ("Tell me below.") is allowed to address the viewer
        checked = " ".join(" ".join(s["lines"] + ([s["sub"]] if s["sub"] and s["kind"] != "closer" else []))
                           for s in scenes)
        allowed = extract_numbers(facts_blob) | extract_numbers(title)
        data_text = " ".join(str(v) for s in scenes for v in (s.get("data") or {}).values()
                             if isinstance(v, (str, int, float)))
        missing = sorted(extract_numbers(checked + " " + data_text) - allowed)
        if missing:
            problems.append("these numbers are not in FACTS, remove or replace them: " + ", ".join(missing))
        found = FIRST_PERSON.findall(checked)
        if found:
            problems.append(f"first-person words found ({', '.join(sorted(set(found)))}); write in third person only")
        if "!" in text_all:
            problems.append("remove exclamation marks")
        words = word_count(text_all)
        hi, lo = int(cfg.get("max_words", 60)), int(cfg.get("min_words", 20))
        if words > hi:
            problems.append(f"too long: {words} words, maximum {hi}")
        if words < lo:
            problems.append(f"too thin: {words} words, minimum {lo}")
        if not 3 <= len(norm["scenes"]) <= 5:
            problems.append(f"need 3 to 5 middle scenes, got {len(norm['scenes'])}")
        if word_count(hook["spoken"]) > 8:
            problems.append("hook is longer than 8 words")
        for s in scenes:
            for line in s["lines"]:
                if word_count(line) > 5:
                    problems.append(f"line too long (max 4 words): {line}")
        narration = [s.get("say", "") for s in scenes]
        if voice:
            if not all(narration):
                problems.append("every scene (and the hook and closer) needs a 'say' narration line")
            nwords = word_count(" ".join(narration))
            vmax = int(self.settings.get("voice.voice_max_words", 62))
            vmin = int(self.settings.get("voice.voice_min_words", 30))
            if nwords > vmax:
                problems.append(f"narration too long: {nwords} words, maximum {vmax}")
            if nwords < vmin and all(narration):
                problems.append(f"narration too thin: {nwords} words, minimum {vmin}")
            if word_count(narration[0]) > 9:
                problems.append("the hook narration is longer than 9 words")
            spoken_checked = " ".join(n for s, n in zip(scenes, narration) if s["kind"] != "closer")
            found_say = FIRST_PERSON.findall(spoken_checked)
            if found_say:
                problems.append(f"first-person words in narration ({', '.join(sorted(set(found_say)))})")
            missing_say = sorted(extract_numbers(spoken_checked) - allowed)
            if missing_say:
                problems.append("narration has numbers not in FACTS: " + ", ".join(missing_say))
            beats_gate = gate_beats([n for n in narration if n], self.tools, self.settings)
            gates.append(beats_gate.to_dict())
            if not beats_gate.passed:
                problems.extend(beats_gate.details)
        gates.append(GateResult("structure", not problems, float(words), "builtin",
                                [f"{len(scenes)} scenes, {words} words"] + problems).to_dict())
        human = gate_human(". ".join(n for n in narration if n) if voice and all(narration) else ". ".join(texts),
                           self.tools, self.settings)
        gates.append(human.to_dict())
        if not human.passed:
            problems.append("sounds robotic: " + "; ".join(human.details)[:200])
        return problems, hook, gates, rows

    # ------------------------------------------------------------------- build
    def build(self, story: dict[str, Any], ai_voice: bool) -> dict[str, Any]:  # noqa: ARG002 - no voice in this style
        facts = story["facts"]
        facts_blob = " ".join(f["text"] + " " + f.get("evidence", "") for f in facts)
        max_rewrites = int(self.settings.get("content.max_rewrites", 1))
        formulas = self._formulas()
        closer_sub = str(self.settings.get("kinetic.closer_sub", "Tell me below."))
        problems: list[str] = []
        last: list[str] = []
        for attempt in range(max_rewrites + 1):
            prompt = kinetic_prompt(story["title"], story.get("pillar") or "tech", facts, formulas, problems,
                                    self.voice_on)
            payload = {"style": "kinetic", "title": story["title"], "facts": facts, "formulas": formulas,
                       "feedback": problems, "closer_sub": closer_sub, "voice": self.voice_on}
            raw = self.llm.json("write_script", self.script_system, prompt, payload, temperature=0.6, max_tokens=1600)
            norm = normalize_kinetic(raw, closer_sub)
            sanitize_graphics(norm["scenes"], facts_blob)
            self._clean(norm)
            problems, hook, gates, rows = self._problems(norm, facts_blob, story["title"])
            if hook and not problems:
                scenes = ([{"kind": "hook", "lines": hook["lines"], "sub": "", "accent": hook["accent"],
                            "graphic": "none", "label": "", "say": hook.get("say", "")}] + norm["scenes"] + [norm["closer"]])
                sentences = [(s.get("say") if self.voice_on and s.get("say") else
                              " ".join(s["lines"] + ([s["sub"]] if s["sub"] else []))) for s in scenes]
                beats = ([{"kind": "hook", "say": sentences[0], "screen": hook["spoken"]},
                          {"kind": "context", "say": facts[0]["text"], "screen": ""}]
                         + [{"kind": "point", "say": s, "screen": s} for s in sentences[1:-1]]
                         + [{"kind": "cta", "say": sentences[-1], "screen": ""}])
                caption, caption_gate = self.build_caption(story, beats, self.voice_on)
                return {"style": "kinetic", "scenes": scenes, "beats": beats, "caption": caption,
                        "voice": self.voice_on, "hook": hook["spoken"], "hook_formula": hook.get("formula_id"), "visual_keywords": [],
                        "gates": gates + [caption_gate], "hook_rows": rows}
            last = list(problems)
            log.info("story %s kinetic attempt %d failed checks: %s", story["id"], attempt + 1, problems)
        raise ContentRejected("content: " + " | ".join(last)[:400])
