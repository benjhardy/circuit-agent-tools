"""5th-order 0.1 dB Chebyshev LC low-pass (port-tuning-palace), cross-checked in every simulator.

Three circuits, all on the port-tuning project's grid (0.1-8 GHz, 25 MHz steps, 317 points):
  ideal      ideal schematic values (C1-L2-C3-L4-C5, shunt-C first)          vs the project's data/ideal.s2p
  as_drawn   EM 7-port (Palace) as an S-block, internal ports 3-7 terminated with the ideal values
                                                                              vs the project's pred_as_drawn.s2p
  e24        the same EM 7-port with the port-tuned E24 values               vs the project's pred_e24.s2p
The project's predictions came from its own numpy termination formula (checked against skrf.connect); here
the same EM data go through qucsator's SPfile, circuitRF's SnP and scikit-rf's Circuit.

    python examples/chebyshev_lpf/run_example.py            (~10 s of simulator time)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from circuit_tools import engine, lint  # noqa: E402

DATA = HERE / "data"
IDEAL = json.loads((DATA / "ideal_values.json").read_text())
E24 = json.loads((DATA / "tuned_e24.json").read_text())
SWEEP = {"type": "sparam", "start": 0.1e9, "stop": 8e9, "points": 317}
SPEC_BANDS = {"pass_band_hz": [0.1e9, 2.5e9], "stop_band_hz": [4.2e9, 6.0e9], "pass_rl_db": 15.0, "stop_rej_db": 25.0}


def ideal_spec(v=IDEAL) -> dict:
    return {"name": "cheby5_ideal", "z0": 50, "components": [
        {"type": "port", "name": "P1", "nodes": ["in", "0"], "num": 1},
        {"type": "C", "name": "C1", "nodes": ["in", "0"], "value": v["C1"]},
        {"type": "L", "name": "L2", "nodes": ["in", "n2"], "value": v["L2"]},
        {"type": "C", "name": "C3", "nodes": ["n2", "0"], "value": v["C3"]},
        {"type": "L", "name": "L4", "nodes": ["n2", "out"], "value": v["L4"]},
        {"type": "C", "name": "C5", "nodes": ["out", "0"], "value": v["C5"]},
        {"type": "port", "name": "P2", "nodes": ["out", "0"], "num": 2}], "analyses": [SWEEP]}


def em_spec(v: dict, name: str) -> dict:
    """EM 7-port: ports 1, 2 external; 3..7 are the C1, L2, C3, L4, C5 footprints (each to the common reference)."""
    comps = [{"type": "sblock", "name": "EM", "nodes": ["p1", "p2", "pC1", "pL2", "pC3", "pL4", "pC5"],
              "file": str(DATA / "em_7port.s7p")},
             {"type": "port", "name": "P1", "nodes": ["p1", "0"], "num": 1},
             {"type": "port", "name": "P2", "nodes": ["p2", "0"], "num": 2}]
    for k in ("C1", "L2", "C3", "L4", "C5"):
        comps.append({"type": k[0], "name": k, "nodes": [f"p{k}", "0"], "value": v[k]})
    return {"name": name, "z0": 50, "components": comps, "analyses": [SWEEP]}


def metrics(f, S) -> dict:
    """The port-tuning project's figures (pnet.metrics)."""
    s11 = 20 * np.log10(np.abs(S[:, 0, 0]))
    s21 = 20 * np.log10(np.abs(S[:, 1, 0]))
    pb = (f >= SPEC_BANDS["pass_band_hz"][0]) & (f <= SPEC_BANDS["pass_band_hz"][1])
    sb = (f >= SPEC_BANDS["stop_band_hz"][0]) & (f <= SPEC_BANDS["stop_band_hz"][1])
    k = int(np.argmax(s21 < -3.0))
    f3 = np.interp(-3.0, [s21[k], s21[k - 1]], [f[k], f[k - 1]]) if k > 0 else float("nan")
    return {"pass_max_s11_db": round(float(s11[pb].max()), 4), "pass_min_s21_db": round(float(s21[pb].min()), 4),
            "stop_max_s21_db": round(float(s21[sb].max()), 4), "f3db_ghz": round(float(f3 / 1e9), 4)}


def project_ref(name: str):
    import skrf as rf

    n = rf.Network(str(DATA / name))
    return {"f": n.f, "s": n.s}


def run_case(spec: dict, ref_file: str, out: Path) -> dict:
    li = lint.lint(spec)
    cc = engine.crosscheck(spec, out / spec["name"])
    ref = project_ref(ref_file)
    rows = {}
    for r in cc["results"]:
        row = {"status": r["status"], "version": r.get("version"), "wall_s": r.get("wall_s"), "reason": r.get("reason")}
        if r["status"] == "ok":
            sp = _load_result(r)
            row["metrics"] = metrics(sp["f"], sp["s"])
            d = np.abs(sp["s"] - ref["s"])
            row["max_dS_vs_project"] = float(d.max())
            if r.get("vs_reference"):
                row["max_dS_vs_scikit_rf"] = r["vs_reference"]["value"]
                row["worst_entry"] = r["vs_reference"].get("entry")
                row["at_ghz"] = r["vs_reference"]["at_hz"] / 1e9
            row["passivity"] = next((c["value"] for c in r.get("checks") or [] if c["name"] == "passivity"), None)
        rows[r["sim"]] = row
    return {"lint": {"counts": li["counts"], "warnings": [f["id"] + ": " + f["message"] for f in li["findings"]
                                                          if f["severity"] != "info"]},
            "project_reference": ref_file, "project_metrics": metrics(ref["f"], ref["s"]), "simulators": rows,
            "overall": cc["overall"]}


def _load_result(row: dict) -> dict:
    import skrf as rf

    p = next(f for f in row["files"] if re.match(r"\.s\d+p$", Path(f).suffix))
    n = rf.Network(p)
    return {"f": n.f, "s": n.s}


def main():
    out = HERE / "runs"
    res = {"ideal": run_case(ideal_spec(), "ideal.s2p", out),
           "as_drawn": run_case(em_spec(IDEAL, "cheby5_em_as_drawn"), "pred_as_drawn.s2p", out),
           "e24": run_case(em_spec(E24, "cheby5_em_e24"), "pred_e24.s2p", out)}
    (HERE / "results.json").write_text(json.dumps(res, indent=1))
    for case, r in res.items():
        print(f"== {case}: project {r['project_metrics']}")
        for sim, row in r["simulators"].items():
            print(f"   {sim:10s} {row['status']:12s} dS_vs_skrf={row.get('max_dS_vs_scikit_rf')} "
                  f"dS_vs_project={row.get('max_dS_vs_project')} {row.get('metrics')} {row.get('reason') or ''}")


if __name__ == "__main__":
    main()
