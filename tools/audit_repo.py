#!/usr/bin/env python3
"""Repository auditor for instagram-agent-skill. Standard library only, no network.

Run from the repository root:
    python3 tools/audit_repo.py --smoke        # writes docs/audit_report.md
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "state", "out", "logs"}
SAMPLES = {
    "hooks.txt": "I lost $18,000 because of one missing contract.\nHey guys, today I wanted to talk about content strategy\n",
    "script.txt": "India's UPI crossed 20 billion transactions in a single month.\nThat is more than every card network combined.\nFees stay at zero for most payments.\nTwenty billion transactions in a single month.\n",
    "caption.txt": "UPI crossed 20 billion transactions in one month.\n\nZero fees means shops keep more.\nSend this to a shop owner.\n\n#upi #fintech\n",
    "draft.txt": "It\u2019s not just a payment tool \u2014 it\u2019s a revolution. In today\u2019s video, let\u2019s delve into UPI.\n",
}
PLAN = [("hookscore.py", ["{hooks.txt}"]), ("beats.py", ["{script.txt}", "--target", "30"]),
        ("caption.py", ["{caption.txt}", "--keywords", "upi"]), ("humanize.py", ["{draft.txt}", "--report"]),
        ("detect.py", ["{draft.txt}"])]


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def run(cmd, cwd, timeout=30):
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as exc:  # noqa: BLE001
        return 1, "", repr(exc)


def analyse_py(path: Path) -> dict:
    src = read(path)
    info = {"path": str(path), "lines": src.count("\n") + 1}
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {**info, "error": str(exc)}
    imports, flags, funcs = set(), [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            flags.append(", ".join(a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            funcs.append(node.name + "(" + ", ".join(a.arg for a in node.args.args) + ")")
    info.update(imports=sorted(imports), flags=flags, functions=funcs, main_guard="__main__" in src,
                json_flag='"--json"' in src or "'--json'" in src,
                home_paths=sorted({n.value[:100] for n in ast.walk(tree) if isinstance(n, ast.Constant)
                                   and isinstance(n.value, str) and (".claude" in n.value or n.value.startswith("~/"))}))
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="docs/audit_report.md")
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    files = [p.relative_to(root) for p in sorted(root.rglob("*")) if p.is_file()
             and not any(part in SKIP_DIRS for part in p.relative_to(root).parts)]
    out = [f"# Audit of {root.name}", "", "## Files"] + [f"- `{f}`" for f in files[:300]] + ["", "## Python tools"]
    for f in files:
        if f.suffix == ".py" and "newsroom" not in f.parts and "tests" not in f.parts and "tools" not in f.parts:
            i = analyse_py(root / f)
            out += [f"### {i['path']}", f"- lines {i['lines']}; imports: {', '.join(i.get('imports', []))}",
                    f"- flags: {i.get('flags')}", f"- functions: {i.get('functions')}",
                    f"- main guard: {i.get('main_guard')}; --json: {i.get('json_flag')}; home paths: {i.get('home_paths')}"]
    out += ["", "## Skills"]
    for f in files:
        if f.name == "SKILL.md":
            text = read(root / f)
            out.append(f"- `{f}` ({text.count(chr(10)) + 1} lines): " + (re.search(r"description:\s*(.+)", text) or [None, ""])[1][:160])
    out += ["", "## JSON data"]
    for f in files:
        if f.suffix == ".json" and "newsroom" not in f.parts and "config" not in f.parts:
            try:
                d = json.loads(read(root / f))
                out.append(f"- `{f}`: " + (", ".join(f"{k}:{type(v).__name__}" for k, v in list(d.items())[:10]) if isinstance(d, dict) else f"array[{len(d)}]"))
            except ValueError as exc:
                out.append(f"- `{f}`: invalid JSON {exc}")
    if args.smoke:
        out += ["", "## Smoke tests"]
        with tempfile.TemporaryDirectory() as tmp:
            for n, c in SAMPLES.items():
                (Path(tmp) / n).write_text(c, encoding="utf-8")
            for script, tpl in PLAN:
                found = sorted(root.rglob(script))
                if not found:
                    out.append(f"### {script}: NOT FOUND")
                    continue
                a = [x.format(**{n: str(Path(tmp) / n) for n in SAMPLES}) if "{" in x else x for x in tpl]
                code, o, e = run([sys.executable, str(found[0])] + a, root)
                jc, jo, _ = run([sys.executable, str(found[0])] + a + ["--json"], root)
                try:
                    json.loads(jo)
                    valid = True
                except ValueError:
                    valid = False
                out += [f"### {found[0].relative_to(root)}", f"exit {code}", "```", (o or e).strip()[:1200], "```",
                        f"--json probe: exit {jc}, valid JSON: {valid}"]
    target = Path(args.out) if Path(args.out).is_absolute() else root / args.out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
