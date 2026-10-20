"""Harmonic balance: a junction diode driven through 50 ohm by a 100 MHz tone (open SPICE level-1 model).

  V1 = A cos(2 pi 100 MHz t) -- 50 ohm -- node a -- diode (Is 1e-12 A, N 1.05, Rs 5 ohm, Cj0 0.5 pF, Vj 0.7 V,
  M 0.5) -- ground; outputs: harmonics of v(a). Cases: 0.3 V with 8 harmonics (weakly nonlinear), 1.0 V (hard
  rectification) with 8 and with 32 harmonics: with 8, HB truncation moves the top harmonics by up to 23 dB;
  with 32 every engine agrees on harmonics 0..8.
Engines: circuitRF hb (reference), Xyce .HB, qucsator .HB, and ngspice transient + exact-bin DFT (30 settling
periods, 10 analysed periods, 256 samples per period).

    python examples/diode_hb/run_example.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from circuit_tools import engine, lint  # noqa: E402

DIODE = {"is": 1e-12, "n": 1.05, "rs": 5, "cj0": "0.5pF", "vj": 0.7, "m": 0.5}


def spec(amp: float, k: int = 8) -> dict:
    return {"name": f"diode_hb_{int(amp * 1000)}mV_K{k}", "temp_c": 27, "components": [
        {"type": "vsource", "name": "V1", "nodes": ["src", "0"], "sin": {"amp": amp, "freq": "100MHz"}},
        {"type": "R", "name": "R1", "nodes": ["src", "a"], "value": 50},
        {"type": "diode", "name": "D1", "nodes": ["a", "0"], "model": DIODE}],
        "analyses": [{"type": "hb", "f0": "100MHz", "harmonics": k, "outputs": ["a"],
                      "samples_per_period": max(256, 16 * k), "settle_periods": 30, "fft_periods": 10}]}


def main():
    out = {}
    for amp, k in ((0.3, 8), (1.0, 8), (1.0, 32)):
        s = spec(amp, k)
        li = lint.lint(s)
        cc = engine.crosscheck(s, HERE / "runs" / s["name"], reference="circuitrf", tol={"hb_db": 0.05})
        rows = {}
        for r in cc["results"]:
            row = {"status": r["status"], "version": r.get("version"), "wall_s": r.get("wall_s"),
                   "reason": r.get("reason")}
            if r["status"] == "ok":
                row["dbv_a"] = r["summary"]["dbv"]["a"]
                row["method"] = r["summary"]["method"]
                row["converged"] = r["summary"]["converged"]
                v = r.get("vs_reference")
                if v:
                    row["max_ddb_vs_circuitrf"] = v["value"]
                    row["worst_harmonic"] = v["nodes"]["a"]["worst_harmonic"]
            rows[r["sim"]] = row
        ref = rows.get("circuitrf", {}).get("dbv_a")
        for row in rows.values():
            if ref and row.get("dbv_a"):
                d = [abs(x - y) for x, y in zip(row["dbv_a"][:9], ref[:9]) if y > -100]
                row["max_ddb_h0_8_vs_circuitrf"] = round(max(d), 4)
        out[f"{amp:g}V_K{k}"] = {"lint": [x["id"] + ": " + x["message"] for x in li["findings"] if x["severity"] != "info"],
                            "harmonic_floor_dbv": -60, "simulators": rows}
    # DC operating point of the same diode, forward biased from 5 V through 1 kohm, plus a divider
    dc = {"name": "diode_dc", "temp_c": 27, "components": [
        {"type": "vsource", "name": "V1", "nodes": ["vdd", "0"], "dc": 5},
        {"type": "R", "name": "R1", "nodes": ["vdd", "a"], "value": "1k"},
        {"type": "diode", "name": "D1", "nodes": ["a", "0"], "model": DIODE},
        {"type": "R", "name": "R2", "nodes": ["vdd", "b"], "value": "1k"},
        {"type": "R", "name": "R3", "nodes": ["b", "0"], "value": "3k"}],
        "analyses": [{"type": "dc", "outputs": ["a", "b"]}]}
    cc = engine.crosscheck(dc, HERE / "runs" / "diode_dc", reference="circuitrf", tol={"dc_v": 1e-4})
    out["dc"] = {r["sim"]: {"status": r["status"], "v": (r.get("summary") or {}).get("v"),
                            "max_dV_vs_circuitrf": (r.get("vs_reference") or {}).get("value")} for r in cc["results"]}
    (HERE / "results.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
