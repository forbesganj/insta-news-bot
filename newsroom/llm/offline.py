"""Deterministic, key-free 'LLM' used for demos, tests and as a last-resort fallback.

It is extractive: it only re-uses sentences already present in the source text, so it can
never hallucinate. Quality is plain, but the whole pipeline (including gates) can run without keys.
"""
from __future__ import annotations

import re
from typing import Any

from ..textutil import split_sentences, tokens, truncate_words, word_count


def _first_words(text: str, limit: int) -> str:
    return truncate_words(re.sub(r"\s+", " ", text).strip().rstrip(".,;:"), limit)


def _screen(text: str, limit: int = 5) -> str:
    words = re.findall(r"[A-Za-z0-9\u20b9$%.]+", text)
    return " ".join(words[:limit]).upper()


class OfflineLLM:
    name = "offline"

    def json(self, task: str, system: str, prompt: str, payload: dict[str, Any] | None = None,
             temperature: float = 0.3, max_tokens: int = 1800) -> dict[str, Any]:
        payload = payload or {}
        handler = getattr(self, f"_{task}", None)
        if handler is None:
            raise NotImplementedError(f"offline provider cannot handle task {task!r}")
        return handler(payload)

    # ------------------------------------------------------------------ tasks
    def _extract_facts(self, p: dict[str, Any]) -> dict[str, Any]:
        candidates: list[tuple[float, str, str]] = []
        for source in p.get("sources", []):
            for sentence in split_sentences(source.get("text", "")):
                n = word_count(sentence)
                if n < 6 or n > 40:
                    continue
                score = 2.0 * len(re.findall(r"\d", sentence)) ** 0.5 + len(re.findall(r"\b[A-Z][a-z]+", sentence)) * 0.3
                candidates.append((score, sentence, source.get("url", "")))
        candidates.sort(key=lambda c: -c[0])
        facts, seen = [], []
        for _, sentence, url in candidates:
            sent_tokens = tokens(sentence)
            if any(len(sent_tokens & s) / max(len(sent_tokens | s), 1) > 0.6 for s in seen):
                continue
            seen.append(sent_tokens)
            text = _first_words(sentence, 25)
            facts.append({"text": text, "evidence": text, "source_url": url})
            if len(facts) >= int(p.get("max_facts", 6)):
                break
        return {"facts": facts}

    def _write_kinetic(self, p: dict[str, Any]) -> dict[str, Any]:
        facts = [f["text"] if isinstance(f, dict) else str(f) for f in p.get("facts", [])]
        if not facts:
            raise ValueError("no facts supplied")

        def split(text: str, max_words: int, per_line: int = 3) -> list[str]:
            words = re.sub(r"[.,;:]+$", "", text).split()[:max_words]
            return [" ".join(words[i:i + per_line]).upper() for i in range(0, len(words), per_line)] or ["UPDATE"]

        hooks = []
        for i in range(3):
            lines = split(facts[i % len(facts)], 8)
            hooks.append({"formula_id": 23, "lines": lines, "accent": min(1, len(lines) - 1),
                          "say": " ".join(facts[i % len(facts)].split()[:9]).rstrip(".,;:") + "."})
        scenes = [{"kind": "detail", "lines": split(f, 6), "sub": "", "accent": 0, "graphic": "none", "label": "",
                   "say": " ".join(f.split()[:12]).rstrip(".,;:") + "."}
                  for f in (facts[1:4] or facts[:1])]
        while len(scenes) < 3:
            scenes.append(dict(scenes[-1]))
        return {"hook_candidates": hooks, "scenes": scenes[:4],
                "closer": {"lines": ["DID YOU", "EXPECT THIS?"], "sub": p.get("closer_sub", "Tell me below."), "accent": 1,
                           "say": "Did you expect this?"}}

    def _write_script(self, p: dict[str, Any]) -> dict[str, Any]:
        if p.get("style") == "kinetic":
            return self._write_kinetic(p)
        facts = [f["text"] if isinstance(f, dict) else str(f) for f in p.get("facts", [])]
        if not facts:
            raise ValueError("no facts supplied")
        title = _first_words(p.get("title", facts[0]), 16)
        formulas = [f["id"] for f in p.get("formulas", [])] or [23]
        hooks = []
        for i in range(3):
            fact = facts[i % len(facts)]
            spoken = _first_words(fact, 9)
            formula = 23 if re.search(r"\d", spoken) else formulas[i % len(formulas)]
            hooks.append({"formula_id": formula, "spoken": spoken + ".", "on_screen": _screen(spoken), "mood": "shocked"})
        rest = facts[1:] or facts
        main = [{"say": _first_words(f, 12) + ".", "screen": _screen(f), "mood": "none"} for f in rest[:2]]
        twist_src = facts[-1]
        ctas = p.get("cta_options") or ["Follow for the daily 30 second brief."]
        script = {
            "language": "en",
            "hook_candidates": hooks,
            "context": {"say": _first_words(title, 10) + ".", "screen": _screen(title), "mood": "none"},
            "main": main,
            "twist": {"say": _first_words(twist_src, 12) + ".", "screen": _screen(twist_src), "mood": "thinking"},
            "cta": {"say": ctas[0], "screen": "FOLLOW", "mood": "celebrating"},
            "visual_keywords": [w for w in sorted(tokens(title))[:3]],
            "claims": [],
        }
        return script

    def _write_caption(self, p: dict[str, Any]) -> dict[str, Any]:
        first = _first_words(p.get("hook", ""), 18) + "."
        body = _first_words(p.get("context", ""), 30) + "."
        tags = [t.lstrip("#") for t in p.get("hashtags", [])][:4]
        return {"first_line": first, "body": body, "cta": p.get("cta", "Follow for the daily brief."),
                "hashtags": tags, "keywords": p.get("keywords", [])}

    def _rewrite_text(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"text": p.get("text", "")}

    def _verify_script(self, p: dict[str, Any]) -> dict[str, Any]:
        from ..textutil import extract_numbers

        missing = sorted(extract_numbers(p.get("script_text", "")) - extract_numbers(p.get("facts_text", "")))
        return {"all_supported": not missing, "issues": [f"number not in facts: {m}" for m in missing]}
