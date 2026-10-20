"""Import Qucs-S schematics and qucsator netlists into the neutral spec.

Qucs-S converts its own schematics headlessly (``qucs-s -n -i x.sch -o x.net``, Qt offscreen); this module parses
that qucsator netlist. Supported: R, L, C, TLIN, MLIN + SUBST, SPfile, Pac ports, Vdc, Diode, GND, and the .SP,
.DC, .TR analyses. Anything else (MTEE, MCROSS, equations, other devices) is reported in ``unsupported`` and
left out, so the agent knows the imported circuit is incomplete.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..units import parse_value

_LINE = re.compile(r'^([.A-Za-z_][A-Za-z0-9_]*):(\S+)\s*(.*)$')


_PROP = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"')


def _props(rest: str) -> tuple[list[str], dict]:
    """'n1 n2 Key="1 GHz" ...' -> (["n1", "n2"], {"Key": "1 GHz"}); values may contain spaces."""
    m = _PROP.search(rest)
    head = rest[: m.start()] if m else rest
    return head.split(), {k: v for k, v in _PROP.findall(rest)}


def _v(x: str, unit: str | None = None) -> float:
    return parse_value(x.replace(" ", ""), unit)


def _node(n: str) -> str:
    return "0" if n == "gnd" else n


def from_qucs_netlist(text: str, name: str = "qucs_import", base_dir: str | None = None) -> dict:
    comps, subs, analyses, unsupported = [], {}, [], []
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        m = _LINE.match(ln)
        if not m:
            unsupported.append(ln[:80])
            continue
        kind, nm, rest = m.groups()
        nodes, p = _props(rest)
        nodes = [_node(n) for n in nodes]
        if kind in ("R", "L", "C"):
            comps.append({"type": kind, "name": nm, "nodes": nodes,
                          "value": _v(p[kind], {"R": "Ohm", "L": "H", "C": "F"}[kind])})
        elif kind == "TLIN":
            if _v(p.get("Alpha", "0 dB").replace("dB", "")) != 0:
                unsupported.append(f"{nm}: TLIN loss (Alpha) not imported")
            comps.append({"type": "tline", "name": nm, "nodes": nodes, "z0": _v(p["Z"], "Ohm"),
                          "length": _v(p["L"], "m"), "eeff": 1.0})
        elif kind == "SUBST":
            subs[nm] = {"er": _v(p["er"]), "h": _v(p["h"], "m"), "t": _v(p.get("t", "0"), "m"),
                        "tand": _v(p.get("tand", "0")), "sigma": 1.0 / _v(p.get("rho", "1.72e-8")),
                        "rough": _v(p.get("D", "0"), "m")}
        elif kind == "MLIN":
            comps.append({"type": "mline", "name": nm, "nodes": nodes, "w": _v(p["W"], "m"), "l": _v(p["L"], "m"),
                          "substrate": p["Subst"]})
            if p.get("Model", "Hammerstad") != "Hammerstad" or p.get("DispModel", "Kirschning") != "Kirschning":
                unsupported.append(f"{nm}: model {p.get('Model')}/{p.get('DispModel')} replaced by "
                                   "Hammerstad/Kirschning")
        elif kind == "SPfile":
            f = p["File"].strip("{}")
            if base_dir and not Path(f).is_absolute():
                f = str(Path(base_dir) / f)
            comps.append({"type": "sblock", "name": nm, "nodes": nodes[:-1], "ref": nodes[-1], "file": f})
        elif kind == "Pac":
            comps.append({"type": "port", "name": nm, "nodes": nodes, "num": int(p["Num"]), "z0": _v(p["Z"], "Ohm")})
        elif kind == "Vdc":
            comps.append({"type": "vsource", "name": nm, "nodes": nodes, "dc": _v(p["U"], "V")})
        elif kind == "Diode":
            comps.append({"type": "diode", "name": nm, "nodes": [nodes[1], nodes[0]],  # qucsator: cathode first
                          "model": {"is": _v(p.get("Is", "1e-15")), "n": _v(p.get("N", "1")),
                                    "rs": _v(p.get("Rs", "0"), "Ohm"), "cj0": _v(p.get("Cj0", "0"), "F"),
                                    "vj": _v(p.get("Vj", "0.7"), "V"), "m": _v(p.get("M", "0.5")),
                                    "tt": _v(p.get("Tt", "0"), "s")}})
        elif kind == "GND":
            continue
        elif kind == ".SP":
            analyses.append({"type": "sparam", "start": _v(p["Start"], "Hz"), "stop": _v(p["Stop"], "Hz"),
                             "points": int(float(p["Points"])), "scale": "log" if p.get("Type") == "log" else "lin"})
        elif kind == ".DC":
            analyses.append({"type": "dc"})
        elif kind == ".TR":
            start, stop, n = _v(p.get("Start", "0"), "s"), _v(p["Stop"], "s"), int(float(p["Points"]))
            analyses.append({"type": "tran", "start": start, "stop": stop, "step": (stop - start) / max(1, n - 1)})
        else:
            unsupported.append(f"{kind}:{nm}")
    return {"name": name, "z0": 50, "substrates": subs, "components": comps, "analyses": analyses,
            "import_notes": {"source": "qucsator netlist", "unsupported": unsupported}}
