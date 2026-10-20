"""Turns written text into text that a voice reads correctly (acronyms, rupees, percentages)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

DEFAULT_WORDS = {
    "UPI": "U P I", "RBI": "R B I", "NPCI": "N P C I", "GST": "G S T", "SEBI": "Sebi", "ISRO": "Isro",
    "IPO": "I P O", "AI": "A I", "GPU": "G P U", "CEO": "C E O", "EV": "E V", "API": "A P I", "DGCA": "D G C A",
    "6E": "six E", "Cr": "crore", "bn": "billion", "mn": "million", "Rs": "rupees",
}
_UNITS = r"(crore|lakh|billion|million|trillion|thousand)"


def load_words(root: Path | None) -> dict[str, str]:
    words = dict(DEFAULT_WORDS)
    if root:
        path = Path(root) / "config" / "pronunciation.yaml"
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                words.update({str(k): str(v) for k, v in (data.get("words") or {}).items()})
            except (OSError, yaml.YAMLError):
                pass
    return words


def speakable(text: str, words: dict[str, str] | None = None) -> str:
    out = text or ""
    # unit abbreviations that follow a number: "5 bn" -> "5 billion", "200 Cr" -> "200 crore"
    for abbr, full in (("bn", "billion"), ("mn", "million"), ("Cr", "crore")):
        out = re.sub(r"(?<=\d)\s?" + abbr + r"\b", " " + full, out)
    out = re.sub(r"\bRs\.?\s?([\d,]+(?:\.\d+)?)", r"\1 rupees", out)
    # money: "₹2,000 crore" -> "2,000 crore rupees";  "₹500" -> "500 rupees"
    out = re.sub(r"\u20b9\s?([\d,]+(?:\.\d+)?)\s?" + _UNITS + r"\b", r"\1 \2 rupees", out, flags=re.IGNORECASE)
    out = re.sub(r"\u20b9\s?([\d,]+(?:\.\d+)?)", r"\1 rupees", out)
    out = re.sub(r"\$\s?([\d,]+(?:\.\d+)?)\s?" + _UNITS + r"\b", r"\1 \2 dollars", out, flags=re.IGNORECASE)
    out = re.sub(r"\$\s?([\d,]+(?:\.\d+)?)", r"\1 dollars", out)
    out = re.sub(r"(\d)\s?%", r"\1 percent", out)
    for written, spoken in sorted((words or DEFAULT_WORDS).items(), key=lambda kv: -len(kv[0])):
        out = re.sub(r"(?<![\w])" + re.escape(written) + r"(?![\w])", spoken, out)
    out = out.replace("&", " and ").replace("\u2014", ", ").replace("\u2013", "-")
    return re.sub(r"\s+", " ", out).strip()
