"""Built-in text cleaner and light quality heuristics.

This is the always-available fallback layer. When the upstream instagram-agent-skill
`humanize.py` / `detect.py` are present they are used for scoring (see gates.py);
this module guarantees a deterministic clean-up even when they are not.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

INVISIBLE_RE = re.compile("[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad\u180e\U000E0000-\U000E007F]")
SPACE_RE = re.compile("[\u00a0\u2002-\u200a\u202f\u205f\u3000]")

TYPOGRAPHY = [
    ("\u2014", ", "),
    ("\u2013", "-"),
    ("\u2018", "'"),
    ("\u2019", "'"),
    ("\u201c", '"'),
    ("\u201d", '"'),
    ("\u2026", "..."),
]

# phrase -> replacement ("" removes the phrase). Matched case-insensitively on word boundaries.
SLOP = {
    "stop scrolling": "",
    "in today's video": "",
    "in this video": "",
    "follow for more": "Follow for the daily brief",
    "tag someone who needs this": "Send this to someone who needs it",
    "the algorithm loves": "Instagram rewards",
    "run, don't walk": "act soon",
    "you won't believe": "",
    "buckle up": "",
    "let's dive into": "here is",
    "let's dive in": "here is the story",
    "dive into": "look at",
    "delve into": "look at",
    "delve": "dig",
    "game-changing": "major",
    "game changing": "major",
    "game-changer": "big shift",
    "game changer": "big shift",
    "revolutionize": "change",
    "revolutionary": "major",
    "unlock the power of": "use",
    "in the ever-evolving": "in the changing",
    "ever-evolving": "changing",
    "tapestry": "mix",
    "testament to": "proof of",
    "it's important to note that": "",
    "it is important to note that": "",
    "in conclusion": "",
    "at the end of the day": "",
    "in the realm of": "in",
    "navigate the landscape of": "handle",
    "cutting-edge": "new",
    "seamlessly": "smoothly",
    "leverage": "use",
    "harness": "use",
    "elevate": "improve",
}
_SLOP_RES = [
    (re.compile(r"(?<!\w)" + re.escape(k) + r"(?!\w)", re.IGNORECASE), v)
    for k, v in sorted(SLOP.items(), key=lambda kv: -len(kv[0]))
]

STRUCTURAL_TELLS = [
    re.compile(r"\b(?:it'?s|this is|that'?s) not just\b[^.!?]*?,\s*(?:it'?s|but|it is)\b", re.I),
    re.compile(r"\bnot only\b[^.!?]*\bbut also\b", re.I),
    re.compile(r"(?:#\w+\s+){6,}"),
    re.compile(r"!{2,}"),
    re.compile(r"\b[A-Z]{4,}\b(?:\s+[A-Z]{4,}\b){2,}"),
]


@dataclass
class HumanizeResult:
    text: str
    changes: list[str] = field(default_factory=list)


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"([.!?])\s*,", r"\1", text)
    text = re.sub(r"^\s*[,;:]\s*", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)

    def cap_sentences(match: re.Match) -> str:
        return match.group(1) + match.group(2).upper()

    text = re.sub(r"(^|[.!?]\s+)([a-z])", cap_sentences, text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def humanize_text(text: str) -> HumanizeResult:
    changes: list[str] = []
    out = text or ""
    invisible = len(INVISIBLE_RE.findall(out))
    if invisible:
        out = INVISIBLE_RE.sub("", out)
        changes.append(f"removed {invisible} invisible character(s)")
    if SPACE_RE.search(out):
        out = SPACE_RE.sub(" ", out)
        changes.append("normalised exotic spaces")
    for src, dst in TYPOGRAPHY:
        if src in out:
            changes.append(f"typography {src!r} -> {dst!r}")
            out = out.replace(src, dst)
    for pattern, replacement in _SLOP_RES:
        if pattern.search(out):
            changes.append(f"slop phrase -> {replacement!r}" if replacement else "removed slop phrase")
            out = pattern.sub(replacement, out)
    out = _tidy(out)
    return HumanizeResult(out, changes)


def builtin_human_score(text: str) -> tuple[float, list[str]]:
    """Cheap 0-100 'sounds human' heuristic: slop density, tells, sentence-length variety."""
    notes: list[str] = []
    words = re.findall(r"\b\w+\b", text or "")
    if not words:
        return 0.0, ["empty text"]
    score = 100.0
    slop_hits = sum(len(p.findall(text)) for p, _ in _SLOP_RES)
    if slop_hits:
        score -= min(40.0, slop_hits * 10.0)
        notes.append(f"{slop_hits} stock phrase(s)")
    tells = sum(1 for pattern in STRUCTURAL_TELLS if pattern.search(text))
    if tells:
        score -= min(30.0, tells * 12.0)
        notes.append(f"{tells} structural tell(s)")
    dashes = text.count("\u2014")
    if dashes:
        score -= min(15.0, dashes * 5.0)
        notes.append(f"{dashes} em dash(es)")
    if INVISIBLE_RE.search(text):
        score -= 20.0
        notes.append("invisible characters")
    sentences = [s for s in re.split(r"[.!?]+\s+", text) if s.strip()]
    if len(sentences) >= 4:
        lengths = [len(s.split()) for s in sentences]
        mean = statistics.mean(lengths)
        if mean > 0 and statistics.pstdev(lengths) / mean < 0.2:
            score -= 10.0
            notes.append("uniform sentence length")
    return max(0.0, min(100.0, score)), notes
