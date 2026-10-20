"""Feeds the upstream SKILL.md guidance to the LLM so the existing skills drive the content rules.

The upstream skills are written for an interactive Claude session. We reuse their text as guidance and
prepend adaptation rules (no questions, no first-person experience, no {{placeholders}}) so the same
knowledge works inside an unattended pipeline for a faceless news brand.
"""
from __future__ import annotations

import re
from pathlib import Path

ADAPTATION = (
    "ADAPTATION RULES (these override anything in the notes below): you are running unattended for a faceless "
    "third-person NEWS brand. Never ask the user questions. Never write first-person experience, results, "
    "clients, or numbers that are not in FACTS. Never output {{placeholders}}. Return only the requested JSON."
)


def _find_skill(root: Path, skill: str) -> Path | None:
    direct = root / "skills" / skill / "SKILL.md"
    if direct.exists():
        return direct
    for path in sorted(root.rglob("SKILL.md")):
        if path.parent.name == skill and ".git" not in path.parts:
            return path
    return None


def load_skill_guidance(root: Path, skill: str, max_chars: int = 3500) -> str:
    path = _find_skill(Path(root), skill)
    if path is None:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n[...truncated]"
    return f"{ADAPTATION}\n\nUPSTREAM SKILL NOTES ({skill}):\n{text}"
