"""Research: gather readable source text, extract grounded facts."""
from __future__ import annotations

import logging
from typing import Any

from .models import Fact
from .net import Http, OfflineError
from .settings import Settings
from .textutil import extract_numbers, html_to_text, jaccard, quote_supported, tokens, truncate_words

log = logging.getLogger(__name__)

FACTS_SYSTEM = """You are a careful news researcher. Extract verifiable facts about ONE news story from the SOURCE TEXTS.
Rules:
- Each fact is a single plain sentence in your own words (max 25 words).
- 'evidence' must be an EXACT quote copied from the source text (max 20 words) that supports the fact.
- 'source_url' must be the url of the source the evidence came from.
- Keep every number, name and date exactly as written in the source. Never add information that is not in the sources.
- Prefer concrete facts (who, what, how much, when). Skip opinions and marketing language.
- If sources disagree on a number, skip that fact.
Return JSON only: {"facts":[{"text":"...","evidence":"...","source_url":"..."}]}"""


def fetch_article_text(http: Http, url: str, max_chars: int) -> str | None:
    if http.offline:
        return None
    try:
        if not http.robots_allowed(url):
            log.info("robots.txt disallows %s", url)
            return None
        response = http.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
    except OfflineError:
        return None
    except Exception as exc:  # noqa: BLE001
        log.info("article fetch failed for %s: %s", url, exc)
        return None
    if response.status_code != 200:
        return None
    content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).lower()
    if content_type and "html" not in content_type and "xml" not in content_type:
        return None
    text = html_to_text(response.text, max_chars)
    return text or None


def gather_texts(story: dict[str, Any], http: Http, settings: Settings) -> list[dict[str, Any]]:
    cfg = settings.get("research", {}) or {}
    max_sources = int(cfg.get("max_sources", 3))
    max_chars = int(cfg.get("max_chars_per_source", 6000))
    block = set((settings.sources or {}).get("blocklist_domains", []) or [])
    ranked = sorted(story["sources"], key=lambda s: (int(s.get("tier", 3)), 0 if s.get("fetchable", True) else 1,
                                                       -len(s.get("summary", ""))))
    texts: list[dict[str, Any]] = []
    used_domains: set[str] = set()
    for source in ranked:
        if len(texts) >= max_sources:
            break
        domain = source.get("domain", "")
        if domain in block or domain in used_domains or not source.get("fetchable", True):
            continue
        text = fetch_article_text(http, source["url"], max_chars)
        origin = "article"
        if not text or len(text) < 200:
            summary = source.get("summary", "")
            if len(summary) >= 120:
                text, origin = summary, "summary"
            else:
                continue
        used_domains.add(domain)
        texts.append({"url": source["url"], "domain": domain, "source": source.get("source", domain),
                      "tier": source.get("tier", 3), "text": text[:max_chars], "origin": origin})
    return texts


def extract_facts(story: dict[str, Any], texts: list[dict[str, Any]], llm: Any,
                  settings: Settings) -> tuple[list[Fact], list[str]]:
    """Ask the LLM for facts, then keep only those whose evidence quote is really in the sources."""
    cfg = settings.get("research", {}) or {}
    max_facts = int(cfg.get("max_facts", 6))
    notes: list[str] = []
    prompt_sources = "\n\n".join(f"SOURCE url={t['url']}\n{t['text']}" for t in texts)
    prompt = f"STORY HEADLINE: {story['title']}\n\n{prompt_sources}\n\nReturn up to {max_facts} facts as JSON."
    payload = {"title": story["title"], "sources": texts, "max_facts": max_facts}
    raw = llm.json("extract_facts", FACTS_SYSTEM, prompt, payload, temperature=0.1, max_tokens=1500)
    corpus = {t["url"]: t["text"] for t in texts}
    all_text = " ".join(corpus.values())
    all_numbers = extract_numbers(all_text)
    facts: list[Fact] = []
    seen: list[set[str]] = []
    for entry in raw.get("facts", []) or []:
        text = str(entry.get("text", "")).strip()
        evidence = str(entry.get("evidence", "")).strip()
        url = str(entry.get("source_url", "")).strip()
        if not text or not evidence:
            notes.append("dropped fact without evidence")
            continue
        haystack = corpus.get(url) or all_text
        if not quote_supported(evidence, haystack) and not quote_supported(evidence, all_text):
            notes.append(f"dropped unsupported fact: {text[:60]}")
            continue
        if not extract_numbers(text) <= all_numbers:
            notes.append(f"dropped fact with number not in sources: {text[:60]}")
            continue
        fact_tokens = tokens(text)
        if any(jaccard(fact_tokens, s) > 0.7 for s in seen):
            continue
        seen.append(fact_tokens)
        facts.append(Fact(text=truncate_words(text, 30), evidence=truncate_words(evidence, 25),
                          source_url=url or texts[0]["url"]))
        if len(facts) >= max_facts:
            break
    return facts, notes
