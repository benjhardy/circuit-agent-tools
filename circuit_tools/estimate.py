"""Cost estimate for circuit runs: wall time per simulator and peak memory, before running anything.

Circuit runs are small, so the model is simple: wall = startup + runs x (per_run + points x unknowns x per_unit).
The constants were measured on the 8-thread / 32 GB desktop (WSL2) these tools were built on, under a full CPU
load from another job (an EM build), so they are pessimistic for an idle machine. Startup dominates for small
circuits (circuitRF is a .NET program: ~1 s per call).
"""
from __future__ import annotations

from .spec import analysis as pick, frequencies, normalize, ports

# measured (see README "Cost"): startup s, per-run s, per (point x unknown) s
CAL = {
    "ngspice": {"startup": 0.25, "per_run": 0.05, "per_unit": 2e-7},
    "xyce": {"startup": 0.35, "per_run": 0.25, "per_unit": 1e-6},
    "qucsator": {"startup": 0.30, "per_run": 0.05, "per_unit": 5e-7},
    "circuitrf": {"startup": 1.0, "per_run": 0.3, "per_unit": 5e-7},
    "scikit-rf": {"startup": 0.0, "per_run": 0.01, "per_unit": 2e-6},
}
MEM_GB = {"ngspice": 0.05, "xyce": 0.15, "qucsator": 0.05, "circuitrf": 0.35, "scikit-rf": 0.1}


def estimate(spec, which=None, sims=None) -> dict:
    s = normalize(spec)
    a = pick(s, which)
    nodes = {n for c in s["components"] for n in c["nodes"]} - {"0"}
    sblock_ports = sum(len(c["nodes"]) for c in s["components"] if c["type"] == "sblock")
    unknowns = len(nodes) + sum(1 for c in s["components"] if c["type"] in ("L", "vsource")) + sblock_ports
    if a["type"] == "sparam":
        points = len(frequencies(a))
    elif a["type"] == "tran":
        points = int((a["stop"] - a["start"]) / a["step"]) + 1
    elif a["type"] == "hb":
        points = (a["harmonics"] + 1) * 20  # Newton iterations x harmonics, rough
    else:
        points = 20
    out = {}
    for sim in sims or CAL:
        c = CAL[sim]
        runs = len(ports(s)) if (sim == "ngspice" and a["type"] == "sparam") else 1
        if sim == "ngspice" and a["type"] == "hb":
            points = int(a.get("settle_periods", 30) + a.get("fft_periods", 10)) * max(64, 16 * a["harmonics"])
        wall = c["startup"] * runs + c["per_run"] * runs + points * max(unknowns, 1) * c["per_unit"]
        out[sim] = {"wall_s": round(wall, 3), "runs": runs, "peak_ram_gb": MEM_GB[sim]}
    return {"analysis": a["type"], "unknowns_approx": unknowns, "points": points, "per_sim": out,
            "total_wall_s": round(sum(v["wall_s"] for v in out.values()), 2),
            "ask_user_first": any(v["wall_s"] > 600 for v in out.values()),
            "note": "simple calibrated model; startup dominates small circuits"}
