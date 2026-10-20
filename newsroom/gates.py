"""Quality gates. Wraps the upstream instagram-agent-skill CLIs and falls back to built-in heuristics.

Upstream tools are located by file name under the repository (normally skills/ig-*/...). Their text
reports are parsed with the pure functions below (unit-tested against the output formats published in
the upstream README). If a tool is missing or its output cannot be parsed, the built-in heuristic is
used and a warning is logged, unless gates.upstream_strict is true, in which case the gate fails.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .humanize import HumanizeResult, builtin_human_score, humanize_text
from .models import GateResult
from .settings import Settings
from .textutil import normalize_for_match, word_count

log = logging.getLogger(__name__)

UPSTREAM_FILES = {
    "hookscore": "hookscore.py",
    "beats": "beats.py",
    "caption": "caption.py",
    "humanize": "humanize.py",
    "detect": "detect.py",
}

WPM = 165.0


# --------------------------------------------------------------------------- parsers (pure)
_HOOK_ENTRY = re.compile(r"^\s*(?:->)?\s*(\d+(?:\.\d+)?)\s+([A-Z]{2,})\s+(.+?)\s*$")
_BEAT_LINE = re.compile(r"^\s*(\d+):(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)s\s+(?:(HOOK|MID|CTA)\s+)?(.*)$")
_CAPTION_ROW = re.compile(r"^(PASS|WARN|FAIL)\s+([A-Z][A-Z0-9 /'-]+?)\s{2,}(.*)$")
_DETECT_TOTAL = re.compile(r"HUMAN SCORE\s+\S+\s+(\d+(?:\.\d+)?)\s+([A-Z]+)")
_DETECT_CHECK = re.compile(r"^(BURSTINESS|SPECIFICITY|SLOP DENSITY|FINGERPRINT|VOICE)\s+\S+\s+(\d+(?:\.\d+)?)")


def parse_hookscore(output: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_deal = False
    for line in output.splitlines():
        match = _HOOK_ENTRY.match(line)
        if match and not line.strip().startswith(("weakest", "dealbreaker")):
            current = {"score": float(match.group(1)), "label": match.group(2), "text": match.group(3),
                       "weakest": "", "dealbreaker": ""}
            entries.append(current)
            in_deal = False
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("weakest:"):
            current["weakest"] = stripped.split(":", 1)[1].strip()
            in_deal = False
        elif stripped.startswith("dealbreaker:"):
            current["dealbreaker"] = stripped.split(":", 1)[1].strip()
            in_deal = True
        elif in_deal and stripped and not stripped.startswith(("=", "-")):
            current["dealbreaker"] += " " + stripped
        elif not stripped:
            in_deal = False
    return entries


def align_hookscores(entries: list[dict[str, Any]], hooks: list[str]) -> list[dict[str, Any] | None]:
    """Map (possibly truncated) upstream rows back to the input hooks by prefix match."""
    aligned: list[dict[str, Any] | None] = [None] * len(hooks)
    norm = [normalize_for_match(h) for h in hooks]
    for entry in entries:
        prefix = normalize_for_match(entry["text"].rstrip(". "))
        for i, hook_norm in enumerate(norm):
            if aligned[i] is None and prefix and hook_norm.startswith(prefix):
                aligned[i] = entry
                break
    return aligned


def parse_beats(output: str) -> dict[str, Any] | None:
    header = re.search(r"(\d+)\s+words.*?~\s*(\d+(?:\.\d+)?)s at (\d+) wpm", output)
    if not header:
        return None
    beats = []
    for line in output.splitlines():
        match = _BEAT_LINE.match(line)
        if match:
            beats.append({"start": int(match.group(1)) * 60 + float(match.group(2)),
                          "duration": float(match.group(3)), "tag": match.group(4) or "",
                          "text": match.group(5).strip()})
    warnings = [ln.strip()[2:] for ln in output.splitlines() if re.match(r"^\s+-\s+\S", ln)]
    return {"words": int(header.group(1)), "duration": float(header.group(2)), "wpm": int(header.group(3)),
            "beats": beats, "warnings": warnings}


def parse_caption(output: str) -> dict[str, Any] | None:
    checks = []
    for line in output.splitlines():
        match = _CAPTION_ROW.match(line.rstrip())
        if match:
            checks.append({"status": match.group(1), "name": match.group(2).strip(), "detail": match.group(3).strip()})
    if not checks:
        return None
    return {"checks": checks, "fails": sum(c["status"] == "FAIL" for c in checks),
            "warns": sum(c["status"] == "WARN" for c in checks)}


def parse_detect(output: str) -> dict[str, Any] | None:
    total = _DETECT_TOTAL.search(output)
    if not total:
        return None
    checks = {}
    for line in output.splitlines():
        match = _DETECT_CHECK.match(line.strip())
        if match:
            checks[match.group(1)] = float(match.group(2))
    return {"score": float(total.group(1)), "verdict": total.group(2), "checks": checks}


# --------------------------------------------------------------------------- upstream runner
class UpstreamTools:
    def __init__(self, root: Path, strict: bool = False, timeout: int = 30) -> None:
        self.root = Path(root)
        self.strict = strict
        self.timeout = timeout
        self.paths: dict[str, Path | None] = {key: self._find(name) for key, name in UPSTREAM_FILES.items()}
        self._warned: set[str] = set()

    def _find(self, filename: str) -> Path | None:
        for base in (self.root / "skills", self.root):
            if base.exists():
                own = {"newsroom", "tests", "tools", "state", "out", ".git"}
                matches = sorted(p for p in base.rglob(filename)
                                 if not own & set(p.relative_to(self.root).parts))
                if matches:
                    return matches[0]
        return None

    def available(self) -> dict[str, bool]:
        return {key: path is not None for key, path in self.paths.items()}

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning("upstream %s: %s (falling back to built-in heuristic)", key, message)

    def run(self, key: str, args: list[str]) -> str | None:
        path = self.paths.get(key)
        if path is None:
            self._warn_once(key, "script not found under skills/")
            return None
        try:
            result = subprocess.run([sys.executable, str(path), *args], capture_output=True, text=True,
                                    timeout=self.timeout, cwd=str(path.parent))
        except (subprocess.TimeoutExpired, OSError) as exc:
            self._warn_once(key, f"could not run: {exc}")
            return None
        # linters often exit non-zero when they flag something; only empty output is a failure
        output = result.stdout or ""
        if not output.strip():
            self._warn_once(key, f"no output (exit {result.returncode}): {result.stderr.strip()[:160]}")
            return None
        return output

    def hookscore(self, hooks: list[str]) -> list[dict[str, Any] | None] | None:
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "hooks.txt"
            file.write_text("\n".join(h.replace("\n", " ") for h in hooks) + "\n", encoding="utf-8")
            output = self.run("hookscore", [str(file)])
        if output is None:
            return None
        entries = parse_hookscore(output)
        if not entries:
            self._warn_once("hookscore", "output format not recognised")
            return None
        return align_hookscores(entries, hooks)

    def beats(self, lines: list[str], target: int) -> dict[str, Any] | None:
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "script.txt"
            file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            output = self.run("beats", [str(file), "--target", str(target)])
        if output is None:
            return None
        parsed = parse_beats(output)
        if parsed is None:
            self._warn_once("beats", "output format not recognised")
        return parsed

    def caption(self, text: str, keywords: list[str]) -> dict[str, Any] | None:
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "caption.txt"
            file.write_text(text, encoding="utf-8")
            args = [str(file)]
            if keywords:
                args += ["--keywords", ",".join(keywords)]
            output = self.run("caption", args)
        if output is None:
            return None
        parsed = parse_caption(output)
        if parsed is None:
            self._warn_once("caption", "output format not recognised")
        return parsed

    def detect(self, text: str) -> dict[str, Any] | None:
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "draft.txt"
            file.write_text(text, encoding="utf-8")
            output = self.run("detect", [str(file)])
        if output is None:
            return None
        parsed = parse_detect(output)
        if parsed is None:
            self._warn_once("detect", "output format not recognised")
        return parsed

    def humanize(self, text: str, mode: str) -> str | None:
        """mode: upstream_stdout (cleaned text printed) or upstream_inplace (file rewritten)."""
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "draft.txt"
            file.write_text(text, encoding="utf-8")
            output = self.run("humanize", [str(file)])
            if mode == "upstream_inplace":
                changed = file.read_text(encoding="utf-8")
                return changed if changed.strip() else None
        return output.strip() if output and output.strip() else None


# --------------------------------------------------------------------------- built-in heuristics
def builtin_hook_score(hook: str) -> tuple[float, str]:
    text = hook.strip()
    low = text.lower()
    if re.match(r"^(hey|hi|hello|welcome|what'?s up|good (morning|evening))\b", low):
        return 5.0, "opens with a greeting"
    score = 50.0
    note = ""
    if re.search(r"stop scrolling|you won'?t believe|in this video|today i|let me tell you", low):
        score -= 40.0
        note = "attention-begging opener"
    if re.search(r"\d", text):
        score += 15.0
    if re.search(r"[\u20b9$%]|\b(?:crore|lakh|billion|million)\b", text, re.IGNORECASE):
        score += 5.0
    n = len(text.split())
    score += 15.0 if n <= 10 else (5.0 if n <= 14 else (-15.0 if n > 18 else 0.0))
    caps = len(re.findall(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-zA-Z]+", text))
    score += min(10.0, 3.0 * caps)
    return max(0.0, min(100.0, score)), note


def builtin_beats(lines: list[str]) -> dict[str, Any]:
    durations = [word_count(line) / WPM * 60.0 for line in lines]
    return {"duration": sum(durations), "words": sum(word_count(line) for line in lines),
            "hook_seconds": durations[0] if durations else 0.0, "warnings": []}


def builtin_caption_checks(caption: str) -> tuple[list[str], list[str]]:
    fails, warns = [], []
    if len(caption) > 2200:
        fails.append(f"caption is {len(caption)} characters (limit 2200)")
    tags = re.findall(r"(?<!\w)#\w+", caption)
    if len(tags) > 5:
        fails.append(f"{len(tags)} hashtags (limit 5)")
    first_line = caption.strip().split("\n", 1)[0]
    if len(first_line) > 125:
        warns.append(f"first line is {len(first_line)} chars; the feed cuts it at ~125")
    return fails, warns


# --------------------------------------------------------------------------- gates
def gate_hooks(hooks: list[str], tools: UpstreamTools, settings: Settings) -> tuple[int | None, list[dict[str, Any]], GateResult]:
    threshold = float(settings.get("gates.hook_min", 55.0))
    strict = bool(settings.get("gates.upstream_strict", False))
    upstream = tools.hookscore(hooks) if hooks else None
    rows: list[dict[str, Any]] = []
    for i, hook in enumerate(hooks):
        row = upstream[i] if upstream and upstream[i] else None
        if row:
            rows.append({"index": i, "text": hook, "score": row["score"], "label": row["label"],
                         "dealbreaker": row["dealbreaker"], "engine": "upstream"})
        else:
            if strict and tools.paths.get("hookscore") is not None:
                rows.append({"index": i, "text": hook, "score": 0.0, "label": "UNSCORED",
                             "dealbreaker": "upstream hookscore did not score this hook", "engine": "upstream"})
                continue
            score, note = builtin_hook_score(hook)
            rows.append({"index": i, "text": hook, "score": score, "label": "BUILTIN",
                         "dealbreaker": note, "engine": "builtin"})
    ok = [r for r in rows if not r["dealbreaker"]]
    best = max(ok, key=lambda r: r["score"]) if ok else None
    if best is None:
        return None, rows, GateResult("hook", False, None, "none", ["every hook has a dealbreaker"])
    passed = best["score"] >= threshold
    details = [f"best hook scored {best['score']:.1f} ({best['label']}), threshold {threshold:.0f}"]
    return (best["index"] if passed else None), rows, GateResult("hook", passed, best["score"], best["engine"], details)


def gate_beats(lines: list[str], tools: UpstreamTools, settings: Settings) -> GateResult:
    cfg = settings.get("content", {}) or {}
    lo, hi = float(cfg.get("min_seconds", 18)), float(cfg.get("max_seconds", 40))
    target = int(cfg.get("target_seconds", 30))
    upstream = tools.beats(lines, target)
    if upstream:
        duration = upstream["duration"]
        engine = "upstream"
        hook_seconds = next((b["duration"] for b in upstream["beats"] if b["tag"] == "HOOK"),
                            upstream["beats"][0]["duration"] if upstream["beats"] else 0.0)
        extra = upstream["warnings"]
    else:
        built = builtin_beats(lines)
        duration, engine, hook_seconds, extra = built["duration"], "builtin", built["hook_seconds"], []
    problems = []
    if not lo <= duration <= hi:
        problems.append(f"spoken duration {duration:.1f}s outside {lo:.0f}-{hi:.0f}s")
    max_hook = float(cfg.get("max_hook_seconds", 3.6))
    if hook_seconds > max_hook:
        problems.append(f"hook takes {hook_seconds:.1f}s (max ~{max_hook:.1f}s, about 10 words)")
    return GateResult("beats", not problems, duration, engine, problems + [f"note: {w}" for w in extra[:3]])


def gate_caption(caption: str, keywords: list[str], tools: UpstreamTools, settings: Settings) -> GateResult:
    fails, warns = builtin_caption_checks(caption)
    upstream = tools.caption(caption, keywords)
    engine = "builtin"
    details = list(fails) + [f"warn: {w}" for w in warns]
    if upstream:
        engine = "upstream"
        for check in upstream["checks"]:
            if check["status"] == "FAIL":
                fails.append(f"{check['name']}: {check['detail']}")
            elif check["status"] == "WARN":
                details.append(f"warn: {check['name']}: {check['detail']}")
    elif settings.get("gates.upstream_strict", False) and tools.paths.get("caption") is not None:
        fails.append("upstream caption.py output could not be parsed")
    return GateResult("caption", not fails, None, engine, list(dict.fromkeys(fails + details)))


def gate_human(text: str, tools: UpstreamTools, settings: Settings) -> GateResult:
    threshold = float(settings.get("gates.human_min", 60.0))
    upstream = tools.detect(text)
    if upstream:
        passed = upstream["score"] >= threshold
        return GateResult("human", passed, upstream["score"], "upstream",
                          [f"upstream human score {upstream['score']:.1f} ({upstream['verdict']}), threshold {threshold:.0f}"])
    if settings.get("gates.upstream_strict", False) and tools.paths.get("detect") is not None:
        return GateResult("human", False, None, "upstream", ["upstream detect.py output could not be parsed"])
    score, notes = builtin_human_score(text)
    return GateResult("human", score >= threshold, score, "builtin",
                      [f"built-in human score {score:.1f}, threshold {threshold:.0f}"] + notes)


def clean_text(text: str, tools: UpstreamTools, settings: Settings) -> HumanizeResult:
    """Optionally let upstream humanize.py clean first, then ALWAYS apply the deterministic built-in pass."""
    mode = str(settings.get("gates.humanize_mode", "builtin"))
    working = text
    changes: list[str] = []
    if mode in ("upstream_stdout", "upstream_inplace"):
        cleaned = tools.humanize(text, mode)
        if cleaned:
            if cleaned != text:
                changes.append("upstream humanize.py applied")
            working = cleaned
    result = humanize_text(working)
    return HumanizeResult(result.text, changes + result.changes)
