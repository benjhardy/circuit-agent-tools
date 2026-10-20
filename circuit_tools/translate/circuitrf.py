"""circuitRF netlists (.cnl) and its headless verbs (sparam, dc, hb --json).

circuitRF (MIT, https://github.com/potatobeanradio/circuitRF) ships its own MCP server (``circuitrf serve --root``)
for native circuitRF work: workspaces, schematics, layout, EM. This backend only uses the CLI verbs so the same
neutral circuit can be cross-checked against the other simulators.

Format notes (circuitRF 1.0.9): ``Type:Name net... key=value``; nets named 0 are ground; bare numbers are SI
(C=1 is one farad), units are written anyway for readability. Port takes (signal, reference). MLIN takes its
substrate from a technology file (``technology "x.ctech"``), so one substrate per netlist. SnP takes N nets
(+1 optional floating reference). V_1Tone is a cosine source used for HB. There is no transient verb.
Device temperatures default to 26.85 C (300 K): we write Temp/Tnom from the spec.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from ..spec import ports
from ..units import fmt
from . import Job, Unsupported

LUMPED = ["R", "L", "C", "port", "vsource", "isource"]


def _n(n: str) -> str:
    return "0" if n == "0" else re.sub(r"[^A-Za-z0-9_]", "_", n)


def _id(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def ctech(sub: dict, name: str) -> str:
    """Minimal circuitRF technology: one top conductor, the dielectric, a ground plane (1 dbu = 1 nm)."""
    dbu = lambda m: int(round(m * 1e9))  # noqa: E731
    t = max(dbu(sub.get("t") or 0.0), 1)
    sig = sub.get("sigma", 5.8e7)
    d = {"FormatVersion": 1, "Name": name,
         "Layers": [{"Key": {"Layer": 1, "Datatype": 0}, "Name": "Top", "Purpose": "drawing"}],
         "Stackup": {"Top": "Open", "Bottom": "Ground", "Layers": [
             {"Kind": "Conductor", "Name": "Top", "ThicknessDbu": t, "Material": "Metal", "Epsr": 1.0, "TanD": 0.0,
              "Mur": 1.0, "SigmaSm": sig, "DrawingLayers": [{"Layer": 1, "Datatype": 0}], "IsGroundReference": False},
             {"Kind": "Dielectric", "Name": "Core", "ThicknessDbu": dbu(sub["h"]), "Material": "Dielectric",
              "Epsr": sub["er"], "TanD": sub.get("tand", 0.0), "Mur": 1.0, "SigmaSm": 0.0, "DrawingLayers": [],
              "IsGroundReference": False},
             {"Kind": "Conductor", "Name": "Ground", "ThicknessDbu": t, "Material": "Metal", "Epsr": 1.0, "TanD": 0.0,
              "Mur": 1.0, "SigmaSm": sig, "DrawingLayers": [], "IsGroundReference": True}]}}
    return json.dumps(d, indent=1)


class CircuitRFBackend:
    NAME = "circuitrf"
    EXE = "circuitrf"
    SUPPORT = {
        "sparam": {"components": LUMPED + ["tline", "mline", "sblock"],
                   "notes": "sparam verb; MLIN from a generated .ctech (one substrate); SnP cubic-spline interpolation, "
                            "NearestEdge extrapolation"},
        "dc": {"components": LUMPED + ["diode", "tline", "mline", "sblock"], "notes": "dc verb (--json)"},
        "tran": {"components": [], "notes": "no transient analysis in circuitRF"},
        "hb": {"components": LUMPED + ["diode", "sblock"], "notes": "hb verb (--json), V_1Tone cosine sources"},
    }

    def build(self, s: dict, a: dict) -> Job:
        if a["type"] == "tran":
            raise Unsupported("circuitRF has no transient analysis")
        job = Job(self.NAME)
        T = fmt(s["temp_c"])
        L = [f"; {s['name']} ({a['type']}) - written by circuit-agent-tools"]
        subs = sorted({c["substrate"] for c in s["components"] if c["type"] == "mline"})
        if len(subs) > 1:
            raise Unsupported("circuitRF: one technology (substrate) per netlist")
        if subs:
            job.files["sub.ctech"] = ctech(s["substrates"][subs[0]], subs[0])
            L.append('technology "sub.ctech"')
        if a["type"] == "hb":
            L.append(f"F0 = {fmt(a['f0'])} Hz")
        for c in s["components"]:
            t, nd = c["type"], [_n(n) for n in c["nodes"]]
            nm = _id(c["name"])
            if t == "R":
                L.append(f"R:{nm} {nd[0]} {nd[1]} R={fmt(c['value'])} Ohm")
            elif t == "L":
                L.append(f"L:{nm} {nd[0]} {nd[1]} L={fmt(c['value'])} H")
            elif t == "C":
                L.append(f"C:{nm} {nd[0]} {nd[1]} C={fmt(c['value'])} F")
            elif t == "tline":
                L.append(f"TLIN:{nm} {nd[0]} {nd[1]} Z={fmt(c['z0'])} Ohm L={fmt(c['length'] * 1e3)} mm "
                         f"Eeff={fmt(c['eeff'])}")
            elif t == "mline":
                L.append(f"MLIN:{nm} {nd[0]} {nd[1]} W={fmt(c['w'] * 1e3)} mm L={fmt(c['l'] * 1e3)} mm")
            elif t == "sblock":
                fn = Path(c["file"]).name
                job.copies[fn] = c["file"]
                ref = "" if c["ref"] == "0" else " " + _n(c["ref"])
                L.append(f'SnP:{nm} {" ".join(nd)}{ref} NumPorts={len(nd)} File="{fn}"')
            elif t == "port":
                if a["type"] == "sparam":
                    L.append(f"Port:{nm} {nd[0]} {nd[1]} Num={c['num']} Z={fmt(c['z0'])} Ohm")
                else:
                    L.append(f"R:port_{nm} {nd[0]} {nd[1]} R={fmt(c['z0'])} Ohm")
            elif t == "vsource":
                if a["type"] == "sparam":
                    continue
                if a["type"] == "hb" and c.get("sin"):
                    sn = c["sin"]
                    if sn.get("freq") and abs(sn["freq"] - a["f0"]) > 1e-9 * a["f0"]:
                        raise Unsupported("circuitRF backend: tone sources must be at f0")
                    L.append(f"V_1Tone:{nm} {nd[0]} {nd[1]} V={fmt(sn['amp'])} V Freq=F0 "
                             f"Phase={fmt(sn['phase_deg'])} deg Vdc={fmt(c['dc'] + sn['offset'])} V")
                else:
                    L.append(f"Vdc:{nm} {nd[0]} {nd[1]} Vdc={fmt(c['dc'])} V")
            elif t == "isource":
                raise Unsupported("circuitRF backend: current sources not translated")
            elif t == "diode":
                m = c["model"]
                L.append(f"Diode:{nm} {nd[0]} {nd[1]} Is={fmt(m['is'])} N={fmt(m['n'])} Rs={fmt(m['rs'])} "
                         f"Cj0={fmt(m['cj0'] * 1e12)} pF Vj={fmt(m['vj'])} M={fmt(m['m'])} Fc={fmt(m['fc'])} "
                         f"Tt={fmt(m['tt'])} Bv={fmt(m['bv'])} Ibv={fmt(m['ibv'])} Temp={T} Tnom={T}")
            else:
                raise Unsupported(f"circuitRF: {t}")
        if a["type"] == "sparam":
            pts = ports(s)
            if not pts:
                raise Unsupported("sparam analysis without ports")
            if a["scale"] != "lin":
                raise Unsupported("circuitRF backend writes linear sweeps only")
            L.append(f"analysis SP1 type=sparam start={fmt(a['start'])} Hz stop={fmt(a['stop'])} Hz "
                     f"npts={a['points']}")
            job.files["case.cnl"] = "\n".join(L) + "\n"
            job.commands.append(f"{{exe}} sparam case.cnl -o sp.s{len(pts)}p > sp.log 2>&1")
            job.meta = {"n": len(pts), "z0": [p["z0"] for p in pts]}
        elif a["type"] == "dc":
            L.append("analysis DC1 type=dc")
            job.files["case.cnl"] = "\n".join(L) + "\n"
            job.commands.append("{exe} dc case.cnl --json > dc.json 2> dc.log")
        elif a["type"] == "hb":
            L.append(f'analysis HB1 type=hb Tone="F0" MaxHarm={a["harmonics"]} Tol=1e-9 MaxIter=200')
            job.files["case.cnl"] = "\n".join(L) + "\n"
            job.commands.append("{exe} hb case.cnl --json > hb.json 2> hb.log")
        job.meta["outputs"] = a.get("outputs") or sorted({n for c in s["components"] for n in c["nodes"] if n != "0"})
        return job

    def parse(self, case: Path, s: dict, a: dict, job: Job) -> dict:
        case = Path(case)
        if a["type"] == "sparam":
            import skrf as rf

            n = rf.Network(str(case / f"sp.s{job.meta['n']}p"))
            return {"kind": "sparam", "f": n.f, "s": n.s, "z0": np.array(job.meta["z0"], float)}
        d = json.loads((case / f"{a['type']}.json").read_text(encoding="utf-8"))
        if d.get("status") not in ("ok", "succeeded", "success", None) and "result" not in d:
            raise RuntimeError("circuitRF failed: " + "; ".join(x.get("message", "") for x in d.get("diagnostics", [])))
        groups = d["result"]["groups"]
        cube = groups[""]["V"] if "" in groups else next(iter(groups.values()))["V"]
        labels = cube["axes"][0]["labels"]
        vals = cube["values"]
        if a["type"] == "dc":
            flat = [v[0] if isinstance(v, list) else v for v in vals]
            return {"kind": "dc", "v": {n: float(flat[labels.index(_n(n))]) for n in job.meta["outputs"]
                                        if _n(n) in labels}}
        nh = cube["axes"][1]["length"]
        arr = np.array([complex(re_, im) for re_, im in vals]).reshape(len(labels), nh)
        out = {}
        for n in job.meta["outputs"]:
            if _n(n) in labels:
                v = np.zeros(a["harmonics"] + 1, complex)
                row = arr[labels.index(_n(n))]
                v[: min(len(row), len(v))] = row[: len(v)]
                out[n] = v
        return {"kind": "hb", "f0": a["f0"], "method": "hb", "v": out}


CIRCUITRF = CircuitRFBackend()
