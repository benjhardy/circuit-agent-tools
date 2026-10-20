"""Transient: a 1 V step (20 ps rise) into a series R-L-C (10 ohm, 10 nH, 1 pF), and a step into a 50 ohm line
with an open end.

  rlc   f0 = 1.59 GHz, Q = 10: ringing capacitor voltage, compared with the closed form (the step response averaged
        over the 20 ps ramp). ngspice, Xyce and qucsator (circuitRF has no transient analysis).
  tline 1 V step behind 50 ohm into a 50 ohm, 100 ps ideal line, open end: the far end jumps to 1 V at 100 ps and
        the near end steps 0.5 -> 1 V at 200 ps. Exercises the ideal line in Xyce's .TRAN (it is a wire in .AC).

    python examples/rlc_transient/run_example.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from circuit_tools import checks, engine, lint  # noqa: E402
from circuit_tools.reference import rlc_series_step  # noqa: E402

R, L, C, TR, TD = 10.0, 10e-9, 1e-12, 20e-12, 0.1e-9
STOP, STEP = 10e-9, 2e-12

RLC = {"name": "rlc_step", "components": [
    {"type": "vsource", "name": "V1", "nodes": ["s", "0"], "pulse": {"v1": 0, "v2": 1, "td": TD, "tr": TR}},
    {"type": "R", "name": "R1", "nodes": ["s", "a"], "value": R},
    {"type": "L", "name": "L1", "nodes": ["a", "c"], "value": L},
    {"type": "C", "name": "C1", "nodes": ["c", "0"], "value": C}],
    "analyses": [{"type": "tran", "stop": STOP, "step": STEP, "outputs": ["c"]}]}

TLINE = {"name": "tline_step", "components": [
    {"type": "vsource", "name": "V1", "nodes": ["s", "0"], "pulse": {"v1": 0, "v2": 1, "td": "50ps", "tr": "10ps"}},
    {"type": "R", "name": "Rs", "nodes": ["s", "near"], "value": 50},
    {"type": "tline", "name": "T1", "nodes": ["near", "far"], "z0": 50, "length": 0.0299792458, "eeff": 1},
    {"type": "R", "name": "Rbig", "nodes": ["far", "0"], "value": 1e9}],
    "analyses": [{"type": "tran", "stop": "600ps", "step": "1ps", "outputs": ["near", "far"]}]}


def closed_form(t):
    # average of ideal-step responses delayed over the ramp = response to the ramp
    taus = np.linspace(0, TR, 201)
    return np.mean([rlc_series_step(t, R, L, C, 1.0, TD + d) for d in taus], axis=0)


def tline_ideal(t):
    """Lossless matched source, open end, ramp 10 ps from 50 ps: far = 2 x incident (0.5 V) delayed 100 ps."""
    ramp = lambda x: np.clip((x - 50e-12) / 10e-12, 0, 1)  # noqa: E731
    return {"near": 0.5 * ramp(t) + 0.5 * ramp(t - 200e-12), "far": 1.0 * ramp(t - 100e-12)}


def run(spec, ref_fn):
    li = lint.lint(spec)
    cc = engine.crosscheck(spec, HERE / "runs" / spec["name"], tol={"tran_rel": 5e-3})
    rows = {}
    a = spec["analyses"][0]
    for r in cc["results"]:
        row = {"status": r["status"], "version": r.get("version"), "wall_s": r.get("wall_s"), "reason": r.get("reason")}
        if r["status"] == "ok":
            d = np.genfromtxt(next(x for x in r["files"] if x.endswith(".csv")), delimiter=",", names=True)
            t = d["t_s"]
            ref = {"t": t, "v": ref_fn(t)}
            res = {"t": t, "v": {n: d[f"v{n}"] for n in a["outputs"]}}
            c = checks.compare_waveforms(res, ref, 5e-3)
            row["vs_closed_form"] = {n: {"max_dv": round(x["max_dv"], 6), "at_ns": round(x["at_s"] * 1e9, 4),
                                         "rel_to_swing": round(x["rel_to_swing"], 6), "rms_dv": round(x["rms_dv"], 7)}
                                     for n, x in c["nodes"].items()}
            if r.get("vs_reference"):
                row["vs_first_simulator"] = r["vs_reference"]["value"]
        rows[r["sim"]] = row
    return {"lint": [x["id"] + ": " + x["message"] for x in li["findings"] if x["severity"] != "info"],
            "reference_sim": cc["reference"], "simulators": rows}


def main():
    out = {"rlc": run(RLC, lambda t: {"c": closed_form(t)}), "tline": run(TLINE, tline_ideal)}
    (HERE / "results.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
