"""L-section match, 50 ohm -> 200 ohm at 2.4 GHz (series L, shunt C), with mixed port impedances.

Q = sqrt(200/50 - 1) = 1.732; X_L = Q * 50 = 86.60 ohm (L = 5.743 nH); X_C = 200 / Q = 115.47 ohm (C = 0.5743 pF).
Port 1 is 50 ohm, port 2 is 200 ohm, so S11 = 0 and |S21| = 1 at 2.4 GHz exactly. Reference: the closed form
(power waves, real references) and scikit-rf; then every simulator.

    python examples/lsection_match/run_example.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from circuit_tools import engine, lint  # noqa: E402

F0, RS, RL = 2.4e9, 50.0, 200.0
Q = math.sqrt(RL / RS - 1)
L = Q * RS / (2 * math.pi * F0)
C = Q / (RL * 2 * math.pi * F0)

SPEC = {"name": "lmatch_2g4", "z0": 50, "components": [
    {"type": "port", "name": "P1", "nodes": ["in", "0"], "num": 1, "z0": RS},
    {"type": "L", "name": "Ls", "nodes": ["in", "out"], "value": L},
    {"type": "C", "name": "Cp", "nodes": ["out", "0"], "value": C},
    {"type": "port", "name": "P2", "nodes": ["out", "0"], "num": 2, "z0": RL}],
    "analyses": [{"type": "sparam", "start": "1.4GHz", "stop": "3.4GHz", "points": 201}]}


def closed_form(f):
    w = 2 * np.pi * f
    zl, yc = 1j * w * L, 1j * w * C
    # ABCD of series L then shunt C, then power-wave S with real references RS, RL
    A, B, Cc, D = 1 + zl * yc, zl, yc, np.ones_like(f, complex)
    den = A * RL + B + Cc * RS * RL + D * RS
    s11 = (A * RL + B - Cc * RS * RL - D * RS) / den
    s21 = 2 * math.sqrt(RS * RL) / den
    s22 = (-A * RL + B - Cc * RS * RL + D * RS) / den
    S = np.zeros((len(f), 2, 2), complex)
    S[:, 0, 0], S[:, 1, 0], S[:, 0, 1], S[:, 1, 1] = s11, s21, s21, s22
    return S


def main():
    li = lint.lint(SPEC)
    cc = engine.crosscheck(SPEC, HERE / "runs")
    f = np.linspace(1.4e9, 3.4e9, 201)
    Sref = closed_form(f)
    k0 = int(np.argmin(np.abs(f - F0)))
    out = {"design": {"L_nH": L * 1e9, "C_pF": C * 1e12, "Q": Q},
           "lint": [x["id"] + ": " + x["message"] for x in li["findings"] if x["severity"] != "info"], "simulators": {}}
    import skrf as rf

    for r in cc["results"]:
        row = {"status": r["status"], "version": r.get("version"), "wall_s": r.get("wall_s"), "reason": r.get("reason")}
        if r["status"] == "ok":
            p = next(x for x in r["files"] if x.endswith(".s2p"))
            S = rf.Network(p).s  # data are referenced to the per-port z0 as solved (not renormalised)
            row["max_dS_vs_closed_form"] = float(np.abs(S - Sref).max())
            row["s11_db_at_2g4"] = round(float(20 * np.log10(abs(S[k0, 0, 0]) + 1e-300)), 2)
            row["s21_db_at_2g4"] = round(float(20 * np.log10(abs(S[k0, 1, 0]))), 6)
            if r.get("vs_reference"):
                row["max_dS_vs_scikit_rf"] = r["vs_reference"]["value"]
        out["simulators"][r["sim"]] = row
    (HERE / "results.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
