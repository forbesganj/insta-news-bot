"""Prompt templates. Output is always a single JSON object."""
from __future__ import annotations

import json
from typing import Any

SCRIPT_SYSTEM = """You write 30-second Instagram Reel scripts for "{brand}", a faceless Indian AI/tech/business news explainer.
HARD RULES
- Use ONLY the facts in FACTS. Every number, name, date and quantity you write must appear in FACTS exactly. Never round, convert or add numbers. Write numbers as digits.
- Third person only. Never use "I", "me", "my", "we" or "our": the account is a news brand, not a person.
- Original wording. Do not copy sentences from the sources.
- No financial advice, no buy/sell calls, no predictions presented as fact. Attribute anything unconfirmed ("reportedly", "according to X").
- Simple spoken English, short sentences, no filler. No greetings, no "stop scrolling", no "in this video", no "game-changer".
- Total spoken words across hook + context + main + twist + cta must be at most {max_words}.
- The hook is the first sentence a viewer hears: at most 10 words, concrete (a number, a name or a consequence).
- Return JSON only. No markdown, no commentary.
{guidance}"""

SCRIPT_USER = """STORY: {title}
PILLAR: {pillar}

FACTS (the only allowed source of information):
{facts}

HOOK FORMULAS you may use (pick the best fit for each candidate; give the formula id):
{formulas}

CTA OPTIONS (choose one, or write a similar short one): {ctas}
{mascot_rule}
Optional "icon" per beat, one of: plane, battery, phone, rupee, chart, chip, shield, rocket, card, globe, spark.
{feedback}
Return exactly this JSON shape:
{{
  "language": "en",
  "hook_candidates": [
    {{"formula_id": 23, "spoken": "<=9 words", "on_screen": "<=5 words", "mood": "shocked"}},
    {{"formula_id": 24, "spoken": "...", "on_screen": "...", "mood": "thinking"}},
    {{"formula_id": 26, "spoken": "...", "on_screen": "...", "mood": "shocked"}}
  ],
  "context": {{"say": "one short sentence: who, what, when", "screen": "<=5 words", "mood": "none"}},
  "main": [
    {{"say": "fact 1 in <=16 words", "screen": "<=5 words", "mood": "none", "icon": "plane"}},
    {{"say": "fact 2", "screen": "...", "mood": "thinking"}}
  ],
  "twist": {{"say": "the payoff / what it means for the viewer", "screen": "<=5 words", "mood": "shocked"}},
  "cta": {{"say": "<=9 words", "screen": "<=4 words", "mood": "celebrating"}},
  "visual_keywords": ["2-3 generic stock-video search words (e.g. airport, smartphone), no brands, no people"],
  "claims": [{{"text": "claim made in the script", "fact_index": 0}}]
}}"""

CAPTION_SYSTEM = """You write Instagram captions for "{brand}", a faceless news explainer.
Rules: the first_line is the hook for the feed (max 110 characters, concrete). The body is at most two short sentences using only facts supplied. Use plain words, no hype, no emojis spam, no questions to the reader except in the hook. Include 1-2 of the search keywords naturally. At most 4 hashtags (no #fyp, no #viral). Return JSON only.
{guidance}"""

CAPTION_USER = """HOOK: {hook}
CONTEXT: {context}
FACTS: {facts}
CTA: {cta}
SEARCH KEYWORDS: {keywords}
SUGGESTED HASHTAGS: {hashtags}

Return: {{"first_line": "...", "body": "...", "cta": "...", "hashtags": ["a","b"], "keywords": ["..."]}}"""


def format_facts(facts: list[dict[str, Any]]) -> str:
    return "\n".join(f"[{i}] {f['text']}" for i, f in enumerate(facts))


def format_formulas(formulas: list[dict[str, Any]]) -> str:
    return "\n".join(f"- id {f['id']} {f['name']}: {f['template']} | e.g. \"{f['example']}\"" for f in formulas)


def feedback_block(problems: list[str]) -> str:
    if not problems:
        return ""
    return "\nPREVIOUS ATTEMPT FAILED THESE CHECKS. Fix all of them:\n" + "\n".join(f"- {p}" for p in problems) + "\n"


MASCOT_ON = ('MASCOT: a friendly robot reacts on screen. For each beat set "mood" to one of shocked, thinking, '
             'laughing, tired, celebrating or none. Use it on at most 3 beats (the hook, the twist and the cta are good '
             'places); keep the tone light and never mock anyone.')
MASCOT_OFF = 'MASCOT: disabled for this story. Set "mood" to "none" on every beat.'


def script_prompt(title: str, pillar: str, facts: list[dict[str, Any]], formulas: list[dict[str, Any]],
                  ctas: list[str], problems: list[str], mascot_allowed: bool = False) -> str:
    return SCRIPT_USER.format(title=title, pillar=pillar, facts=format_facts(facts),
                              formulas=format_formulas(formulas), ctas=json.dumps(ctas),
                              mascot_rule=MASCOT_ON if mascot_allowed else MASCOT_OFF,
                              feedback=feedback_block(problems))
