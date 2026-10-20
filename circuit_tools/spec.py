"""The neutral circuit spec: one JSON object every translator reads.

    {"name": "lpf5", "z0": 50, "temp_c": 27,
     "components": [
       {"type": "port", "name": "P1", "nodes": ["in", "0"], "num": 1, "z0": 50},
       {"type": "C", "name": "C1", "nodes": ["in", "0"], "value": "1.46pF"},
       {"type": "L", "name": "L2", "nodes": ["in", "n2"], "value": 4.365e-9},
       {"type": "tline", "name": "TL1", "nodes": ["a", "b"], "z0": 70.7, "length_deg": 90, "f_ref": "2.4GHz"},
       {"type": "mline", "name": "ML1", "nodes": ["a", "b"], "w": "1.1mm", "l": "19mm", "substrate": "ro4003c"},
       {"type": "sblock", "name": "EM", "nodes": ["p1", "p2", "p3"], "file": "em.s3p"},
       {"type": "vsource", "name": "V1", "nodes": ["s", "0"], "dc": 0, "pulse": {"v1": 0, "v2": 1, "td": 0, "tr": "10p"}},
       {"type": "diode", "name": "D1", "nodes": ["a", "0"], "model": {"is": 1e-12, "n": 1.05, "rs": 5, "cj0": "0.5pF"}}],
     "substrates": {"ro4003c": {"er": 3.55, "h": "0.508mm", "t": "35um", "tand": 0.0027, "sigma": 5.8e7}},
     "analyses": [{"type": "sparam", "start": "0.1GHz", "stop": "8GHz", "points": 317}]}

Ground is node "0" ("gnd"/"GND" are accepted and mapped to "0"). Values are SI numbers or SI strings (see units).
``normalize`` returns a copy with every value as a float, the ground mapped, defaults filled, and a list of notes.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

import numpy as np

from .units import UnitError, parse_value

GROUND_ALIASES = {"0", "gnd", "GND", "Gnd", "ground", "GROUND"}
C0 = 299792458.0

COMPONENTS = {
    "R": {"nodes": 2, "params": {"value": "Ohm"}, "doc": "resistor; value in ohm"},
    "L": {"nodes": 2, "params": {"value": "H"}, "doc": "inductor; value in H"},
    "C": {"nodes": 2, "params": {"value": "F"}, "doc": "capacitor; value in F"},
    "tline": {"nodes": 2, "params": {"z0": "Ohm", "length_deg": "deg", "f_ref": "Hz", "length": "m", "eeff": ""},
              "doc": "ideal lossless TEM line referenced to ground: z0 and either length_deg at f_ref or a physical "
                     "length with eeff (default 1)"},
    "mline": {"nodes": 2, "params": {"w": "m", "l": "m", "substrate": None},
              "doc": "microstrip line (Hammerstad-Jensen Z0/eeff, Kirschning-Jansen dispersion, conductor and "
                     "dielectric loss) on a named substrate"},
    "sblock": {"nodes": None, "params": {"file": None, "ref": None},
               "doc": "N-port Touchstone block: nodes = one node per port in file order, ref = common reference "
                      "node (default ground)"},
    "port": {"nodes": (1, 2), "params": {"num": "", "z0": "Ohm"},
             "doc": "S-parameter port: nodes [signal] or [signal, reference]; num 1..N; z0 (default spec z0)"},
    "vsource": {"nodes": 2, "params": {"dc": "V", "ac": "V", "pulse": None, "sin": None},
                "doc": "voltage source + to -: dc, ac magnitude, pulse {v1, v2, td, tr, tf, pw, per} or sin "
                       "{offset, amp, freq, phase_deg} (cosine reference: v = offset + amp cos(2 pi f t + phase))"},
    "isource": {"nodes": 2, "params": {"dc": "A", "ac": "A", "pulse": None, "sin": None},
                "doc": "current source, flowing from node 1 through the source to node 2; same waveforms as vsource"},
    "diode": {"nodes": 2, "params": {"model": None},
              "doc": "junction diode anode, cathode; model {is, n, rs, cj0, vj, m, tt, bv, ibv} (SPICE level 1)"},
}
DIODE_DEFAULTS = {"is": 1e-14, "n": 1.0, "rs": 0.0, "cj0": 0.0, "vj": 1.0, "m": 0.5, "tt": 0.0, "bv": 0.0,
                  "ibv": 1e-3, "fc": 0.5}
DIODE_UNITS = {"is": "A", "rs": "Ohm", "cj0": "F", "vj": "V", "tt": "s", "bv": "V", "ibv": "A"}
ANALYSES = {
    "sparam": "S-parameters: start, stop, points, scale lin|log (default lin)",
    "dc": "DC operating point: node voltages",
    "tran": "transient: stop, step (output step), start (default 0), outputs = node list",
    "hb": "harmonic balance: f0, harmonics (K, default 8), outputs = node list; sources carry sin {amp, freq=f0}",
}
SUBSTRATE_KEYS = {"er": "", "h": "m", "t": "m", "tand": "", "sigma": "S", "rough": "m"}
SPEC_DOC = {
    "name": "case name (used for file names)",
    "z0": "default port reference impedance, ohm (50)",
    "temp_c": "simulation temperature for every simulator, Celsius (27); circuitRF and qucsator default to 26.85",
    "components": {k: v["doc"] for k, v in COMPONENTS.items()},
    "substrates": "name -> {er, h, t (default 0), tand (0), sigma S/m (5.8e7), rough rms m (0)}",
    "analyses": ANALYSES,
    "values": "plain SI numbers (1.46e-12) or SI strings ('1.46pF', '2.4 GHz', '50 Ohm', '10mm'); 'M' is mega, "
              "'m' milli, 'meg' mega. Translators write plain numbers, so SPICE never re-reads a prefix.",
    "ground": "node '0' (aliases gnd, GND, ground)",
}


class SpecError(ValueError):
    pass


def load(spec) -> dict:
    """dict, JSON text or path -> dict (a path's folder becomes base_dir for relative S-block files)."""
    import json

    if isinstance(spec, dict):
        return spec
    p = Path(str(spec))
    if len(str(spec)) < 1000 and p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        d.setdefault("base_dir", str(p.parent.resolve()))
        return d
    return json.loads(spec)


def _val(x, unit, where, notes):
    try:
        return parse_value(x, unit)
    except UnitError as e:
        raise SpecError(f"{where}: {e}") from None


def normalize(spec: dict) -> dict:
    """Validated copy with float values, ground '0', defaults; adds 'notes' (list of strings)."""
    s = copy.deepcopy(load(spec))
    notes: list[str] = []
    s.setdefault("name", "circuit")
    s["z0"] = _val(s.get("z0", 50.0), "Ohm", "z0", notes)
    s["temp_c"] = float(s.get("temp_c", 27.0))
    base = Path(s.get("base_dir") or ".")
    subs = {}
    for name, sub in (s.get("substrates") or {}).items():
        d = {"er": None, "h": None, "t": 0.0, "tand": 0.0, "sigma": 5.8e7, "rough": 0.0}
        for k, u in SUBSTRATE_KEYS.items():
            if k in sub:
                d[k] = _val(sub[k], u, f"substrate {name}.{k}", notes)
        if d["er"] is None or d["h"] is None:
            raise SpecError(f"substrate {name}: er and h are required")
        subs[name] = d
    s["substrates"] = subs
    comps = []
    for i, c in enumerate(s.get("components") or []):
        c = dict(c)
        t = c.get("type")
        if t not in COMPONENTS:
            raise SpecError(f"component {i}: unknown type {t!r}; known: {sorted(COMPONENTS)}")
        c.setdefault("name", f"{t}{i + 1}")
        where = f"{t} {c['name']}"
        nodes = [str(n) for n in c.get("nodes") or []]
        mapped = ["0" if n in GROUND_ALIASES else n for n in nodes]
        if any(n != m for n, m in zip(nodes, mapped)):
            c["_ground_alias"] = [n for n in nodes if n in GROUND_ALIASES and n != "0"]
        c["nodes"] = mapped
        need = COMPONENTS[t]["nodes"]
        if t == "port" and len(mapped) == 1:
            c["nodes"] = mapped + ["0"]
        elif isinstance(need, int) and len(mapped) != need:
            raise SpecError(f"{where}: needs {need} nodes, got {len(mapped)}")
        if t in ("R", "L", "C"):
            if "value" not in c:
                raise SpecError(f"{where}: missing value")
            c["_raw_value"] = c["value"]
            c["value"] = _val(c["value"], COMPONENTS[t]["params"]["value"], where, notes)
        elif t == "tline":
            c["z0"] = _val(c.get("z0", 50), "Ohm", where, notes)
            if "length_deg" in c:
                c["length_deg"] = _val(c["length_deg"], "deg", where, notes)
                c["f_ref"] = _val(c.get("f_ref", 1e9), "Hz", where, notes)
                c["eeff"] = 1.0
                c["length"] = c["length_deg"] / 360.0 * C0 / c["f_ref"]
            elif "length" in c:
                c["length"] = _val(c["length"], "m", where, notes)
                c["eeff"] = _val(c.get("eeff", 1.0), "", where, notes)
                c["f_ref"] = _val(c.get("f_ref", 1e9), "Hz", where, notes)
                c["length_deg"] = 360.0 * c["length"] * math.sqrt(c["eeff"]) * c["f_ref"] / C0
            else:
                raise SpecError(f"{where}: give length_deg + f_ref, or length (+ eeff)")
            c["delay_s"] = c["length"] * math.sqrt(c["eeff"]) / C0
        elif t == "mline":
            c["w"] = _val(c["w"], "m", where, notes)
            c["l"] = _val(c["l"], "m", where, notes)
            if "f_ref" in c:
                c["f_ref"] = _val(c["f_ref"], "Hz", where, notes)
            if c.get("substrate") not in subs:
                raise SpecError(f"{where}: substrate {c.get('substrate')!r} not in substrates")
        elif t == "sblock":
            f = Path(c["file"])
            c["file"] = str(f if f.is_absolute() else (base / f).resolve())
            c["ref"] = "0" if c.get("ref", "0") in GROUND_ALIASES else str(c["ref"])
        elif t == "port":
            c["num"] = int(c.get("num", 0))
            c["z0"] = _val(c.get("z0", s["z0"]), "Ohm", where, notes)
        elif t in ("vsource", "isource"):
            u = "V" if t == "vsource" else "A"
            c["dc"] = _val(c.get("dc", 0.0), u, where, notes)
            c["ac"] = _val(c.get("ac", 0.0), u, where, notes)
            if c.get("pulse"):
                p = {"v1": 0.0, "v2": 1.0, "td": 0.0, "tr": 0.0, "tf": 0.0, "pw": None, "per": None}
                for k, v in c["pulse"].items():
                    p[k] = None if v is None else _val(v, u if k in ("v1", "v2") else "s", where, notes)
                c["pulse"] = p
            if c.get("sin"):
                p = {"offset": 0.0, "amp": 1.0, "freq": None, "phase_deg": 0.0}
                for k, v in c["sin"].items():
                    p[k] = _val(v, {"freq": "Hz", "phase_deg": "deg"}.get(k, u), where, notes)
                c["sin"] = p
        elif t == "diode":
            m = dict(DIODE_DEFAULTS)
            for k, v in (c.get("model") or {}).items():
                k2 = k.lower()
                if k2 not in DIODE_DEFAULTS:
                    raise SpecError(f"{where}: unknown diode parameter {k!r}; known {sorted(DIODE_DEFAULTS)}")
                m[k2] = _val(v, DIODE_UNITS.get(k2, ""), where, notes)
            c["model"] = m
        comps.append(c)
    s["components"] = comps
    names = [c["name"] for c in comps]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise SpecError(f"duplicate component names {sorted(dup)}")
    ans = []
    for i, a in enumerate(s.get("analyses") or []):
        a = dict(a)
        t = a.get("type")
        if t not in ANALYSES:
            raise SpecError(f"analysis {i}: unknown type {t!r}; known {sorted(ANALYSES)}")
        if t == "sparam":
            a["start"] = _val(a["start"], "Hz", "sparam.start", notes)
            a["stop"] = _val(a["stop"], "Hz", "sparam.stop", notes)
            a["points"] = int(a.get("points", 101))
            a["scale"] = a.get("scale", "lin")
        elif t == "tran":
            a["stop"] = _val(a["stop"], "s", "tran.stop", notes)
            a["step"] = _val(a.get("step", a["stop"] / 1000), "s", "tran.step", notes)
            a["start"] = _val(a.get("start", 0.0), "s", "tran.start", notes)
            a["outputs"] = [str(n) for n in a.get("outputs") or []]
        elif t == "hb":
            a["f0"] = _val(a["f0"], "Hz", "hb.f0", notes)
            a["harmonics"] = int(a.get("harmonics", 8))
            a["outputs"] = [str(n) for n in a.get("outputs") or []]
        elif t == "dc":
            a["outputs"] = [str(n) for n in a.get("outputs") or []]
        ans.append(a)
    s["analyses"] = ans
    s["notes"] = notes
    return s


def frequencies(a: dict) -> np.ndarray:
    if a["scale"] == "log":
        return np.logspace(math.log10(a["start"]), math.log10(a["stop"]), a["points"])
    return np.linspace(a["start"], a["stop"], a["points"])


def ports(s: dict) -> list[dict]:
    return sorted([c for c in s["components"] if c["type"] == "port"], key=lambda c: c["num"])


def nodes(s: dict) -> dict[str, list[tuple[str, int]]]:
    """node -> [(component name, terminal index)] (S-block reference nodes included)."""
    out: dict[str, list] = {}
    for c in s["components"]:
        for k, n in enumerate(c["nodes"]):
            out.setdefault(n, []).append((c["name"], k))
        if c["type"] == "sblock":
            out.setdefault(c["ref"], []).append((c["name"], len(c["nodes"])))
    return out


def analysis(s: dict, which=None) -> dict:
    """Pick an analysis by index or type (default: the first)."""
    ans = s["analyses"]
    if not ans:
        raise SpecError("the spec has no analyses")
    if which is None:
        return ans[0]
    if isinstance(which, int):
        return ans[which]
    for a in ans:
        if a["type"] == which:
            return a
    raise SpecError(f"no {which!r} analysis in the spec")


def sblock_info(path: str) -> dict:
    """Ports, reference impedances and frequency range of a Touchstone file (via scikit-rf)."""
    import skrf as rf

    n = rf.Network(path)
    z0 = np.asarray(n.z0)
    return {"ports": int(n.nports), "f_min": float(n.f[0]), "f_max": float(n.f[-1]), "points": int(len(n.f)),
            "z0": [complex(z) for z in z0[0]], "z0_uniform": bool(np.allclose(z0, z0.flat[0])), "network": n}
