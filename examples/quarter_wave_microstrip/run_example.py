"""Quarter-wave transformer, 50 -> 100 ohm at 2.4 GHz, as one lossy microstrip line on RO4003C.

Line: 70.71 ohm (w = 0.579 mm, Hammerstad-Jensen at 2.4 GHz), lambda_g / 4 = 19.37 mm; h = 0.508 mm, er 3.55,
tan d 0.0027, 35 um copper (5.8e7 S/m), smooth. Ports: 50 ohm and 100 ohm. Compares scikit-rf MLine (reference),
qucsator MLIN, circuitRF MLIN and ngspice (LTRA with R, L, C frozen at 2.4 GHz). Xyce is skipped (XYC001).

    python examples/quarter_wave_microstrip/run_example.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from circuit_tools import engine, lint, microstrip  # noqa: E402

SUB = {"er": 3.55, "h": "0.508mm", "t": "35um", "tand": 0.0027, "sigma": 5.8e7, "rough": 0}
SPEC = {"name": "qwt_ms", "z0": 50,
        "substrates": {"ro4003c": SUB},
        "components": [
            {"type": "port", "name": "P1", "nodes": ["in", "0"], "num": 1, "z0": 50},
            {"type": "mline", "name": "TL1", "nodes": ["in", "out"], "w": "0.579mm", "l": "19.37mm",
             "substrate": "ro4003c", "f_ref": "2.4GHz"},
            {"type": "port", "name": "P2", "nodes": ["out", "0"], "num": 2, "z0": 100}],
        "analyses": [{"type": "sparam", "start": "1GHz", "stop": "4GHz", "points": 121}]}


def main():
    li = lint.lint(SPEC)
    cc = engine.crosscheck(SPEC, HERE / "runs")
    import skrf as rf

    f = np.linspace(1e9, 4e9, 121)
    k0 = int(np.argmin(np.abs(f - 2.4e9)))
    sub = {"er": 3.55, "h": 0.508e-3, "t": 35e-6, "tand": 0.0027, "sigma": 5.8e7, "rough": 0}
    p = microstrip.line_props([2.4e9], 0.579e-3, sub)
    out = {"line_at_2g4": {"z0_ohm": float(p["z0"][0].real), "eeff": float(p["eeff"][0]),
                           "alpha_c_db_per_m": float(8.686 * p["alpha_c"][0]),
                           "alpha_d_db_per_m": float(8.686 * p["alpha_d"][0])},
           "lint": [x["id"] + ": " + x["message"] for x in li["findings"] if x["severity"] != "info"],
           "simulators": {}}
    for r in cc["results"]:
        row = {"status": r["status"], "version": r.get("version"), "wall_s": r.get("wall_s"), "reason": r.get("reason")}
        if r["status"] == "ok":
            S = rf.Network(next(x for x in r["files"] if x.endswith(".s2p"))).s
            s11 = 20 * np.log10(np.abs(S[:, 0, 0]))
            row["s11_min_db"] = round(float(s11.min()), 2)
            row["f_s11_min_ghz"] = round(float(f[int(np.argmin(s11))] / 1e9), 4)
            row["s21_db_at_2g4"] = round(float(20 * np.log10(abs(S[k0, 1, 0]))), 4)
            row["loss_db_at_2g4"] = round(float(-10 * np.log10(abs(S[k0, 0, 0]) ** 2 + abs(S[k0, 1, 0]) ** 2)), 4)
            if r.get("vs_reference"):
                v = r["vs_reference"]
                row["max_dS_vs_scikit_rf"] = v["value"]
                row["worst"] = f"{v.get('entry')} at {v['at_hz'] / 1e9:.3g} GHz"
                d = np.abs(S[k0] - rf.Network(next(x for x in cc["results"][0]["files"] if x.endswith(".s2p"))).s[k0])
                row["dS_at_2g4"] = float(d.max())
        out["simulators"][r["sim"]] = row
    (HERE / "results.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
