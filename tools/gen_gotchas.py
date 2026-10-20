"""Regenerate skills/circuit-simulation/references/gotchas.md from the lint rules and the workflow lessons.

    python tools/gen_gotchas.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from circuit_tools.api import OTHER_GOTCHAS  # noqa: E402
from circuit_tools.lint import rules_table  # noqa: E402


def main() -> None:
    lines = ["# Circuit-simulation gotchas (generated from circuit_tools.lint; do not edit by hand)", "",
             "Each rule below is checked by `circuit-tools lint`. Rules marked *reference* run the scikit-rf "
             "reference solver. Measured behaviour is for ngspice 46 (dev), Xyce 7.10 (dev), qucsator_rf 1.0.7, "
             "circuitRF 1.0.9 and scikit-rf 2.2.", "",
             "| id | gotcha |", "|---|---|"]
    lines += [f"| {r['id']} | {r['title']} |" for r in rules_table()]
    lines.append("")
    for r in rules_table():
        lines.append(f"## {r['id']}: {r['title']}" + (" *(reference)*" if r["needs"] == "reference" else ""))
        lines += ["", r["lesson"], "", f"Source: {r['source']}.", ""]
    lines += ["# Workflow lessons (not lint rules)", ""]
    for g in OTHER_GOTCHAS:
        lines += [f"## {g['title']}", "", g["lesson"], "", f"Source: {g['source']}.", ""]
    out = ROOT / "skills" / "circuit-simulation" / "references" / "gotchas.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out, len(rules_table()), "rules", len(OTHER_GOTCHAS), "lessons")


if __name__ == "__main__":
    main()
