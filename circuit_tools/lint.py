"""Lint rules for neutral circuit specs. Each rule encodes a mistake that gives a wrong answer, often silently.

``lint(spec, context=None)`` returns findings with an id, severity (error | warning | info), message, fix, the
lesson behind the rule and its source. Severity: ``error`` = a simulator will abort or give a wrong answer;
``warning`` = very likely wrong (or simulators will disagree); ``info`` = worth knowing.
Rules marked ``needs="reference"`` run the scikit-rf reference solver (milliseconds for small circuits).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import microstrip
from .spec import C0, COMPONENTS, GROUND_ALIASES, SpecError, frequencies, load, normalize, ports
from .units import parse_value, prefix_traps

RULES: dict[str, dict] = {}


def rule(rid: str, title: str, lesson: str, source: str, needs: str = ""):
    def deco(fn):
        RULES[rid] = {"id": rid, "title": title, "lesson": lesson, "source": source, "needs": needs, "fn": fn}
        return fn
    return deco


@dataclass
class Ctx:
    raw: dict
    s: dict
    sims: list
    findings: list = field(default_factory=list)

    def comps(self, *types):
        return [c for c in self.s["components"] if not types or c["type"] in types]

    def analyses(self, t):
        return [a for a in self.s["analyses"] if a["type"] == t]


def _f(ctx: Ctx, rid: str, severity: str, message: str, fix: str = "", **data):
    r = RULES[rid]
    ctx.findings.append({"id": rid, "severity": severity, "title": r["title"], "message": message, "fix": fix,
                         "lesson": r["lesson"], "source": r["source"], **data})


def _node_map(s):
    out: dict[str, list] = {}
    for c in s["components"]:
        for n in c["nodes"]:
            out.setdefault(n, []).append(c)
        if c["type"] == "sblock":
            out.setdefault(c["ref"], []).append(c)
    return out


# ============================================================================================ topology

@rule("TOP001", "Floating (dangling) node",
      "A node touched by one terminal only is an open stub. SPICE accepts it in AC but aborts the DC operating "
      "point ('singular matrix') when nothing ties it to ground; in RF circuits it is usually a typo in a node name.",
      "ngspice manual 2.1 (every node needs a DC path to ground); SPICE practice")
def _top001(ctx: Ctx):
    for n, cs in _node_map(ctx.s).items():
        if n != "0" and len(cs) == 1:
            c = cs[0]
            if c["type"] == "sblock":
                continue  # handled by SBK006
            _f(ctx, "TOP001", "warning", f"node {n!r} is connected only to {c['type']} {c['name']}",
               "Check the node name (a typo creates a new node), or delete the dangling part.", node=n)


@rule("TOP002", "No ground reference",
      "Every simulator needs a reference node: SPICE '0', qucsator 'gnd', circuitRF '0'. Without one the matrix "
      "is singular; with a misspelled ground ('GND ' or 'ground1') the circuit floats and AC results depend on "
      "the solver's gmin.",
      "ngspice / Xyce reference guides")
def _top002(ctx: Ctx):
    if "0" not in _node_map(ctx.s):
        _f(ctx, "TOP002", "error", "no component is connected to ground ('0')",
           "Connect the reference terminals (ports, shunt parts, sources) to node '0'.")


@rule("TOP003", "Node without a DC path to ground",
      "Nodes connected only through capacitors (or a series capacitor chain) have no DC solution: ngspice and Xyce "
      "stop with a singular matrix in .op/.tran. S-parameter runs still work, which hides the problem until a "
      "transient or harmonic-balance run.",
      "SPICE practice; ngspice manual (rshunt option)")
def _top003(ctx: Ctx):
    if not (ctx.analyses("dc") or ctx.analyses("tran") or ctx.analyses("hb")):
        return
    # union-find over DC-conducting elements
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for c in ctx.s["components"]:
        nd = c["nodes"]
        for n in nd:
            find(n)
        if c["type"] in ("R", "L", "vsource", "diode", "port", "tline", "mline", "mstep"):
            parent[find(nd[0])] = find(nd[1])
        elif c["type"] == "mcline":   # each strip conducts end to end; the two strips are isolated at DC
            parent[find(nd[0])] = find(nd[1])
            parent[find(nd[2])] = find(nd[3])
        elif c["type"] == "sblock":
            pass  # unknown DC behaviour (qucsator: duringDC=open)
    for n in list(parent):
        if n != "0" and find(n) != find("0"):
            _f(ctx, "TOP003", "error", f"node {n!r} has no DC path to ground (only capacitors, current sources or "
                                      "S-blocks)", "Add a large resistor to ground (1 Gohm) or a DC return path.",
               node=n)


@rule("TOP004", "Loop of voltage sources or inductors",
      "Two ideal voltage sources in parallel, or a loop of inductors and voltage sources, has no unique DC "
      "solution: SPICE aborts with a singular matrix ('voltage source loop').",
      "ngspice manual; Xyce users guide")
def _top004(ctx: Ctx):
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for c in ctx.comps("vsource", "L"):
        a, b = find(c["nodes"][0]), find(c["nodes"][1])
        if a == b:
            _f(ctx, "TOP004", "error", f"{c['type']} {c['name']} closes a loop of voltage sources/inductors",
               "Add a small series resistance (1 mohm) or remove the redundant source.")
        parent[a] = b


@rule("TOP005", "Names that collide in case-insensitive SPICE",
      "SPICE (ngspice, Xyce) folds node and element names to one case: 'Out' and 'out' are one node there but two "
      "in qucsator, circuitRF and scikit-rf, so the simulators solve different circuits and only a cross-check "
      "shows it.",
      "ngspice manual 2.1 (names are case-insensitive)")
def _top005(ctx: Ctx):
    seen: dict[str, str] = {}
    for n in _node_map(ctx.s):
        k = n.lower()
        if k in seen and seen[k] != n:
            _f(ctx, "TOP005", "error", f"nodes {seen[k]!r} and {n!r} are the same node in SPICE",
               "Rename one of them.")
        seen.setdefault(k, n)
    seen = {}
    for c in ctx.s["components"]:
        k = c["name"].lower()
        if k in seen and seen[k] != c["name"]:
            _f(ctx, "TOP005", "error", f"components {seen[k]!r} and {c['name']!r} collide in SPICE", "Rename one.")
        seen.setdefault(k, c["name"])


@rule("TOP006", "Ground aliases",
      "ngspice and Xyce only treat '0' as ground (ngspice also maps 'gnd'); qucsator uses 'gnd'; a node called "
      "'GND' or 'ground' is an ordinary floating node in some tools. The spec maps the aliases to '0' and the "
      "translators write each tool's own ground name.",
      "ngspice manual; Qucs netlist format")
def _top006(ctx: Ctx):
    for c in ctx.s["components"]:
        if c.get("_ground_alias"):
            _f(ctx, "TOP006", "info", f"{c['name']}: {c['_ground_alias']} treated as ground '0'",
               "Write '0' in the spec to make the intent explicit.")


# ============================================================================================ values

@rule("UNI001", "SPICE prefix trap: M, MEG, F",
      "SPICE scale factors are case-insensitive and ignore trailing letters: '1M' is 1 milli (not mega), '1MEG' is "
      "mega, '1F' is 1 femto (not one farad), '10mF' is 10 milli. A value copied from a SPICE deck into another tool "
      "(or the reverse) changes by 1e9 or 1e15 without an error.",
      "ngspice manual 2.3 (scale factors)")
def _uni001(ctx: Ctx):
    for c in ctx.raw.get("components") or []:
        t = c.get("type")
        unit = COMPONENTS.get(t, {}).get("params", {}).get("value")
        for key in ("value",):
            msg = prefix_traps(c.get(key), unit if isinstance(unit, str) else None)
            if msg:
                _f(ctx, "UNI001", "warning", f"{t} {c.get('name')}: {msg}",
                   "Write plain SI numbers (1e6, 1.46e-12) or unambiguous strings ('1Meg' is mega everywhere here, "
                   "'1pF').", component=c.get("name"))


@rule("UNI002", "Implausible component value (unit slip)",
      "RF parts live in narrow ranges: C from ~10 fF to ~10 uF, L from ~0.1 nH to ~100 uH. '1.46' for a capacitor "
      "is 1.46 F (the 'pF' was dropped), '4.365e-6' for a 4.365 nH inductor is 1000x too large.",
      "practice (port-tuning filter values)")
def _uni002(ctx: Ctx):
    lim = {"C": (1e-15, 1e-4), "L": (1e-11, 1e-3), "R": (1e-3, 1e9)}
    for c in ctx.comps("R", "L", "C"):
        lo, hi = lim[c["type"]]
        v = c["value"]
        if v <= 0:
            _f(ctx, "UNI002", "error", f"{c['type']} {c['name']} = {v:g}", "Values must be positive.")
        elif not lo <= v <= hi:
            _f(ctx, "UNI002", "warning", f"{c['type']} {c['name']} = {v:g} is outside {lo:g}-{hi:g}",
               "Check the unit prefix.", component=c["name"])


@rule("UNI003", "Default temperature differs between simulators",
      "circuitRF and qucsator default device temperatures to 26.85 C (300 K), ngspice and Xyce to 27 C. Diode and "
      "BJT currents depend on kT/q, so the same model differs by ~0.05 % in Vt before anything else; the "
      "translators write the spec's temp_c everywhere.",
      "circuitRF component reference (Temp 26.85); ngspice manual (TNOM 27)")
def _uni003(ctx: Ctx):
    if ctx.comps("diode") and "temp_c" not in ctx.raw:
        _f(ctx, "UNI003", "info", "nonlinear devices without an explicit temp_c (27 C is written to every simulator)",
           "Set temp_c in the spec.")


# ============================================================================================ ports

@rule("PRT001", "Port reference impedances differ",
      "S-parameters are defined against each port's z0. Mixed z0 (50 / 100 ohm) is legal, but a Touchstone v1 "
      "file holds one z0 on its option line: circuitRF writes port 1's and a comment, scikit-rf writes one value, "
      "so a re-read file is silently renormalised to the wrong reference.",
      "Touchstone 1.1 spec; circuitRF CLI reference ('Ports with different reference impedances')")
def _prt001(ctx: Ctx):
    z = sorted({p["z0"] for p in ports(ctx.s)})
    if len(z) > 1:
        _f(ctx, "PRT001", "warning", f"ports use z0 = {z} ohm",
           "Keep the per-port z0 with the data (Touchstone 2 [Reference], .npy or CSV) or renormalise before "
           "comparing.", z0=z)


@rule("PRT002", "Port numbering",
      "Port numbers must be 1..N without gaps or duplicates; otherwise simulators renumber them differently "
      "and S21 of one tool is S31 of another.",
      "Qucs/circuitRF/Xyce port conventions")
def _prt002(ctx: Ctx):
    nums = [p["num"] for p in ports(ctx.s)]
    if ctx.analyses("sparam") and not nums:
        _f(ctx, "PRT002", "error", "S-parameter analysis without ports", "Add port components (num 1..N).")
        return
    if nums and sorted(nums) != list(range(1, len(nums) + 1)):
        _f(ctx, "PRT002", "error", f"port numbers {sorted(nums)} are not 1..{len(nums)}", "Renumber the ports.")


@rule("PRT003", "Port with a floating reference",
      "A port between two non-ground nodes is a differential port: SPICE and qucsator accept it, scikit-rf's "
      "Circuit and most Touchstone consumers assume a common ground, so the reference solver cannot check it.",
      "skrf.circuit documentation")
def _prt003(ctx: Ctx):
    for p in ports(ctx.s):
        if p["nodes"][1] != "0":
            _f(ctx, "PRT003", "info", f"port {p['name']} references node {p['nodes'][1]!r}",
               "Use a ground-referenced port where you can, or a balun/mixed-mode conversion afterwards.")


@rule("PRT004", "Source in an S-parameter circuit",
      "Ports are the sources of an S-parameter run. An extra ideal voltage source shorts its node for small "
      "signals (and is dropped by some translators); bias it through a choke or leave it out.",
      "practice")
def _prt004(ctx: Ctx):
    if ctx.analyses("sparam"):
        for c in ctx.comps("vsource"):
            _f(ctx, "PRT004", "warning", f"vsource {c['name']} in a circuit with an S-parameter analysis",
               "A voltage source is a short at AC: make sure that is intended.")


# ============================================================================================ sweeps

@rule("SWP001", "Sweep too coarse for the circuit's resonances",
      "A sweep that steps over a resonance or notch reports a smooth, wrong curve and misses the worst-case "
      "point of a spec. Checked by re-solving the linear circuit on a 4x finer grid (scikit-rf) and comparing "
      "with the coarse sweep interpolated: more than 0.02 in |S| means the grid is too coarse.",
      "port-tuning (-3 dB and stop-band edges); practice", needs="reference")
def _swp001(ctx: Ctx):
    from .reference import solve_sparam

    for a in ctx.analyses("sparam"):
        if a["points"] < 3:
            continue
        fine = dict(a, points=4 * (a["points"] - 1) + 1)
        try:
            r1 = solve_sparam(ctx.s, a)
            r2 = solve_sparam(ctx.s, fine)
        except Exception:  # noqa: BLE001  reference cannot solve it; other rules report why
            return
        s1 = r1["s"]
        f1, f2 = r1["f"], r2["f"]
        worst, at = 0.0, None
        for i in range(s1.shape[1]):
            for j in range(s1.shape[2]):
                y = np.interp(f2, f1, s1[:, i, j].real) + 1j * np.interp(f2, f1, s1[:, i, j].imag)
                d = np.abs(y - r2["s"][:, i, j])
                if d.max() > worst:
                    worst, at = float(d.max()), float(f2[int(d.argmax())])
        if worst > 0.02:
            _f(ctx, "SWP001", "warning", f"linear interpolation of the {a['points']}-point sweep is off by "
               f"{worst:.3f} in |S| near {at / 1e9:.4g} GHz", f"Use >= {fine['points']} points (or a finer "
               "segment around the resonance).", max_interp_error=worst, at_hz=at)


@rule("SWP002", "Sweep starting at 0 Hz",
      "At f = 0 inductors are shorts and capacitors opens: S-parameters exist but Touchstone readers, "
      "transmission-line models (Kirschning-Jansen) and vector fitting misbehave, and some tools refuse 0 Hz in "
      "a log sweep. Start at a small positive frequency.",
      "practice")
def _swp002(ctx: Ctx):
    for a in ctx.analyses("sparam"):
        if a["start"] <= 0:
            _f(ctx, "SWP002", "warning" if a["scale"] == "lin" else "error", "sweep starts at 0 Hz",
               "Start at e.g. 1 MHz (and use a separate DC run).")
        if a["stop"] <= a["start"]:
            _f(ctx, "SWP002", "error", "stop <= start", "Fix the sweep.")


@rule("SWP003", "Electrical length per frequency step",
      "Distributed elements rotate the phase quickly: with more than ~20 degrees of line phase per step a plot "
      "connects points across a ripple and interpolated Touchstone data are wrong between points.",
      "practice")
def _swp003(ctx: Ctx):
    for a in ctx.analyses("sparam"):
        if a["points"] < 2:
            continue
        df = (a["stop"] - a["start"]) / (a["points"] - 1) if a["scale"] == "lin" else None
        if df is None:
            continue
        tot = 0.0
        for c in ctx.comps("tline"):
            tot += c["delay_s"]
        for c in ctx.comps("mline", "mcline"):
            tot += c["l"] * math.sqrt(ctx.s["substrates"][c["substrate"]]["er"]) / C0
        dphi = 360 * df * tot
        if dphi > 20:
            _f(ctx, "SWP003", "warning", f"{dphi:.0f} degrees of total line phase per frequency step",
               f"Use a step below {20 / (360 * tot) / 1e6:.3g} MHz.", deg_per_step=dphi)


# ============================================================================================ S-blocks

def _sinfo(c):
    from .spec import sblock_info

    try:
        return sblock_info(c["file"])
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


@rule("SBK001", "S-block port count vs connected nodes",
      "An N-port Touchstone block needs N signal nodes in file port order. A .s7p connected with 6 nodes is "
      "refused by circuitRF and qucsator, but a wrong order (ports 3 and 4 swapped) is accepted by every tool.",
      "Touchstone spec; circuitRF SnP reference")
def _sbk001(ctx: Ctx):
    for c in ctx.comps("sblock"):
        info = _sinfo(c)
        if "error" in info:
            _f(ctx, "SBK001", "error", f"{c['name']}: cannot read {c['file']}: {info['error']}", "Fix the path.")
            continue
        if info["ports"] != len(c["nodes"]):
            _f(ctx, "SBK001", "error", f"{c['name']}: file has {info['ports']} ports, {len(c['nodes'])} nodes given",
               "One node per port, in the file's port order.")
        c["_info"] = info


@rule("SBK002", "S-block data does not cover the sweep (extrapolation)",
      "Outside the file's band each tool extrapolates differently: circuitRF holds the nearest edge, qucsator "
      "extrapolates its interpolator, scikit-rf refuses, Xyce's YLIN holds the edge. Results then differ between "
      "simulators for a reason that has nothing to do with the circuit.",
      "circuitRF SnP reference (ExtrapMode NearestEdge); skrf.Network.interpolate")
def _sbk002(ctx: Ctx):
    for c in ctx.comps("sblock"):
        info = c.get("_info") or _sinfo(c)
        if "error" in info:
            continue
        for a in ctx.analyses("sparam"):
            if a["start"] < info["f_min"] * (1 - 1e-9) or a["stop"] > info["f_max"] * (1 + 1e-9):
                _f(ctx, "SBK002", "error", f"{c['name']}: sweep {a['start'] / 1e9:g}-{a['stop'] / 1e9:g} GHz, data "
                   f"{info['f_min'] / 1e9:g}-{info['f_max'] / 1e9:g} GHz", "Limit the sweep to the data band.")
        for a in ctx.analyses("dc") + ctx.analyses("tran") + ctx.analyses("hb"):
            if info["f_min"] > 0:
                _f(ctx, "SBK002", "warning", f"{c['name']}: no 0 Hz point in the data, used in a {a['type']} run",
                   "Add a DC point (or a physical DC model); qucsator treats the block as open at DC by default.")


@rule("SBK003", "S-block reference impedance",
      "A Touchstone file carries its own reference impedance (option line, or [Reference] per port in v2). "
      "Every tool renormalises the block to the circuit; a hand-edited option line that says 'R 50' over data "
      "measured at 75 ohm moves every pole of the filter without an error.",
      "Touchstone 1.1/2.0 spec")
def _sbk003(ctx: Ctx):
    for c in ctx.comps("sblock"):
        info = c.get("_info") or _sinfo(c)
        if "error" in info:
            continue
        zs = sorted({round(z.real, 6) for z in info["z0"]})
        if not info["z0_uniform"] or zs != [50.0]:
            _f(ctx, "SBK003", "warning", f"{c['name']}: file reference impedances {zs}",
               "Make sure the option line / [Reference] is what the data were measured or simulated against.")


@rule("SBK004", "Passivity of imported S-parameters",
      "An EM or measured S-matrix with eigenvalues of S^H S above 1 creates energy: harmless in a linear sweep, "
      "but it makes transient and harmonic-balance runs unstable and hides real loss. Seen with coarse meshes, "
      "unresolved vias and port normalisation errors.",
      "palace-agent-tools passivity check; Palace via lesson (S22 = +0.8 dB)")
def _sbk004(ctx: Ctx):
    from .checks import passivity

    for c in ctx.comps("sblock"):
        info = c.get("_info") or _sinfo(c)
        if "error" in info:
            continue
        p = passivity(info["network"].s, info["network"].f, tol=1e-6)
        if p["status"] != "pass":
            _f(ctx, "SBK004", "warning" if p["value"] < 1 + 1e-3 else "error",
               f"{c['name']}: max eigenvalue of S^H S = {p['value']:.6f} at {p['at_hz'] / 1e9:.4g} GHz "
               f"({p['n_violating']} points)", "Enforce passivity (scikit-rf vector fitting) or fix the source data.",
               max_eig=p["value"])


@rule("SBK005", "Reciprocity of imported S-parameters",
      "Passive structures without ferrites are reciprocal; |S_ij - S_ji| well above the solver noise means the "
      "file has transposed data (Touchstone v1 2-port order is S11 S21 S12 S22, unlike the row order of N >= 3) "
      "or inconsistent port normalisation.",
      "Touchstone 1.1 spec (2-port data order)")
def _sbk005(ctx: Ctx):
    from .checks import reciprocity

    for c in ctx.comps("sblock"):
        info = c.get("_info") or _sinfo(c)
        if "error" in info or info["ports"] < 2:
            continue
        r = reciprocity(info["network"].s, info["network"].f, tol=1e-3)
        if r["status"] == "fail":
            _f(ctx, "SBK005", "warning", f"{c['name']}: max |S_ij - S_ji| = {r['value']:.3g}",
               "Check the file's port order and data order.")


@rule("SBK006", "Unconnected S-block port (open, not matched)",
      "A block port whose node touches nothing else is left OPEN, not terminated in z0. To mimic a matched load "
      "(what the S-parameters assume) add a z0 resistor to ground; internal port-tuning ports must be terminated "
      "with their component.",
      "port-tuning-palace (7-port EM matrix, internal ports)")
def _sbk006(ctx: Ctx):
    nm = _node_map(ctx.s)
    for c in ctx.comps("sblock"):
        for k, n in enumerate(c["nodes"]):
            if len(nm.get(n, [])) == 1:
                _f(ctx, "SBK006", "warning", f"{c['name']} port {k + 1} (node {n!r}) is open",
                   "Terminate it (z0 resistor or the intended component) or confirm open is intended.")


# ============================================================================================ lines

@rule("TLN001", "Microstrip model limits",
      "Closed-form microstrip models are fits: Hammerstad-Jensen for 0.01 <= w/h <= 100, Kirschning-Jansen "
      "dispersion for w/h 0.1-100, er 1-20 and h/lambda0 <= 0.13. Above the TM1 surface-wave onset or near the "
      "strip's transverse resonance the line radiates and couples, and no circuit model is right: use EM.",
      "Hammerstad & Jensen 1980; Kirschning & Jansen 1982; Pozar ch. 3")
def _tln001(ctx: Ctx):
    fmax = max([a["stop"] for a in ctx.analyses("sparam")] + [0.0])
    for c in ctx.comps("mline"):
        for x in microstrip.limits(c["w"], ctx.s["substrates"][c["substrate"]], fmax or 1e9):
            _f(ctx, "TLN001", x["level"], f"{c['name']}: {x['message']}", "Check with an EM solver.")


@rule("TLN002", "Microstrip in SPICE is a frozen RLGC line",
      "ngspice has no microstrip model: the translator writes an LTRA line whose R, L, C match Z0, eeff and the "
      "loss at one frequency (f_ref, default mid-band). Dispersion (eeff rising with f), sqrt(f) skin loss and "
      "f-proportional dielectric loss are missing, so phase and loss drift away from f_ref; ngspice's LTRA also "
      "has no G, so dielectric loss is folded into R.",
      "ngspice manual (LTRA); this package's quarter-wave example")
def _tln002(ctx: Ctx):
    if ctx.comps("mline") and (not ctx.sims or "ngspice" in ctx.sims):
        _f(ctx, "TLN002", "info", f"{len(ctx.comps('mline'))} microstrip line(s) will be RLGC lines in ngspice",
           "Expect ngspice to differ from qucsator/circuitRF/scikit-rf away from f_ref; use those for the answer.")


@rule("TLN003", "Ideal line given as electrical length",
      "length_deg is defined at f_ref only. qucsator's TLIN takes a physical length (air), circuitRF accepts "
      "either, SPICE takes a delay: the translators convert, but a forgotten f_ref (default 1 GHz) puts the "
      "quarter wave at the wrong frequency in every tool at once.",
      "practice")
def _tln003(ctx: Ctx):
    for c in ctx.raw.get("components") or []:
        if c.get("type") == "tline" and "length_deg" in c and "f_ref" not in c:
            _f(ctx, "TLN003", "warning", f"tline {c.get('name')}: length_deg without f_ref (1 GHz assumed)",
               "Give f_ref.")


@rule("TLN004", "Thick-strip microstrip correction differs between tools",
      "For a 0.579 mm, 70.7 ohm line on 0.508 mm RO4003C with 35 um copper (t/h = 0.07), scikit-rf and qucsator "
      "(Hammerstad-Jensen thickness correction) gave Z0 70.71 ohm, eeff 2.5986, 1.87 dB/m conductor loss; "
      "circuitRF 1.0.9 gave 69.83 ohm, eeff 2.6675 (+2.7 %) and 2.75 dB/m. With t = 1 nm all three agree "
      "(73.1 ohm, 2.6561), so the difference is the thickness/conductor-loss model, not the substrate. A quarter "
      "wave moves by ~1.3 % in frequency.",
      "this package (quarter_wave_microstrip example, 2026-10-19)")
def _tln004(ctx: Ctx):
    if ctx.sims and "circuitrf" not in ctx.sims:
        return
    for c in ctx.comps("mline"):
        sub = ctx.s["substrates"][c["substrate"]]
        if sub.get("t", 0) / sub["h"] > 0.02:
            _f(ctx, "TLN004", "info", f"{c['name']}: t/h = {sub['t'] / sub['h']:.3f}; circuitRF's MLIN will differ "
               "from qucsator/scikit-rf by ~1-3 % in eeff and Z0", "Compare with t -> 0 to separate the effect.")


@rule("TLN005", "Coupled microstrip model limits",
      "The Kirschning-Jansen coupled-microstrip equations (even/odd Z0 and eeff, and their dispersion) are fits for "
      "0.1 <= w/h <= 10, 0.1 <= s/h <= 10 and 1 <= er <= 18. Outside that range the even/odd impedances drift "
      "without a warning from the simulator. A coupled section is also a single strip for the surface-wave and "
      "transverse-resonance limits of TLN001.",
      "Kirschning & Jansen, IEEE MTT-32 (1984); qucsator mscoupled.cpp")
def _tln005(ctx: Ctx):
    fmax = max([a["stop"] for a in ctx.analyses("sparam")] + [0.0])
    for c in ctx.comps("mcline"):
        sub = ctx.s["substrates"][c["substrate"]]
        for x in microstrip.coupled_limits(c["w"], c["s"], sub) + microstrip.limits(c["w"], sub, fmax or 1e9):
            _f(ctx, "TLN005", x["level"], f"{c['name']}: {x['message']}", "Check with an EM solver.")


@rule("TLN006", "Open-end and width-step models",
      "qucsator's MOPEN (Kirschning-Jansen-Koster) is an end capacitance fitted for 0.01 <= w/h <= 100, "
      "1 <= er <= 50. MSTEP is a shunt C and series L from the Gupta-Garg fits (er <= 10, wide/narrow ratio "
      "1.5-3.5 for C, <= 5 for L) and assumes W1 >= W2: written narrow side first (0.6 -> 1.2 mm on 0.508 mm "
      "RO4350B, 5 GHz) it gave S11 = +j4.6e-3 and a phase advance of 0.22 degrees, wide side first -j0.95e-3 and "
      "a delay of 0.39 degrees. The qucsator translator writes the wide side first. Neither part models the "
      "coupling to a neighbouring strip.",
      "qucsator msopen.cpp, msstep.cpp; Kirschning, Jansen & Koster, Electron. Lett. 1981; Gupta et al., "
      "Microstrip Lines and Slotlines; this package (measured, 2026-10-26)")
def _tln006(ctx: Ctx):
    for c in ctx.comps("mopen"):
        for x in microstrip.open_end_limits(c["w"], ctx.s["substrates"][c["substrate"]]):
            _f(ctx, "TLN006", x["level"], f"{c['name']}: {x['message']}", "Check with an EM solver.")
    for c in ctx.comps("mstep"):
        for x in microstrip.step_limits(c["w1"], c["w2"], ctx.s["substrates"][c["substrate"]]):
            _f(ctx, "TLN006", x["level"], f"{c['name']}: {x['message']}",
               "Check the step with an EM solver if it matters for the result.")


@rule("XYC001", "Xyce frequency-domain distributed and data elements",
      "Measured with Xyce 7.10 (conda unic-cass build): in .AC/.LIN the lossless T line and LTRA behave as a "
      "plain wire and a YLIN Touchstone block as an open circuit, with no warning. YLIN works in .HB; T works in "
      ".TRAN. The Xyce translator refuses these combinations.",
      "this package (measured, 2026-10-19)")
def _xyc001(ctx: Ctx):
    if ctx.sims and "xyce" not in ctx.sims:
        return
    dist = ctx.comps("tline", "mline", "sblock")
    if dist and ctx.analyses("sparam"):
        _f(ctx, "XYC001", "info", f"{len(dist)} distributed/data element(s): Xyce is skipped for the S-parameter run",
           "Cross-check with qucsator, circuitRF and scikit-rf.")


# ============================================================================================ time / HB

def _taus(s):
    """Rough fastest time scales: 1/omega0 of the smallest L and C, else R*C and L/R; line delays."""
    rs = [c["value"] for c in s["components"] if c["type"] == "R"] + [p["z0"] for p in ports(s)] or [50.0]
    ls = [c["value"] for c in s["components"] if c["type"] == "L"]
    cs = [c["value"] for c in s["components"] if c["type"] == "C"]
    out = []
    if ls and cs:
        out.append(math.sqrt(min(ls) * min(cs)))
    elif cs:
        out.append(min(rs) * min(cs))
    elif ls:
        out.append(min(ls) / max(rs))
    out += [c["delay_s"] for c in s["components"] if c["type"] == "tline"]
    return out


@rule("TRN001", "Transient output step vs circuit time constants",
      "An output step coarser than ~1/10 of the fastest time constant (RC, L/R, 1/omega0) or of a line delay "
      "under-samples the edge; simulators then interpolate their internal points differently and the waveforms "
      "disagree at the edges. The translators also cap the internal step at the output step.",
      "SPICE practice")
def _trn001(ctx: Ctx):
    for a in ctx.analyses("tran"):
        taus = [t for t in _taus(ctx.s) if t > 0]
        if taus and a["step"] > min(taus) / 10:
            _f(ctx, "TRN001", "warning", f"step {a['step']:.3g} s > 1/10 of the fastest time constant "
               f"{min(taus):.3g} s", f"Use step <= {min(taus) / 20:.3g} s.")
        if not a.get("outputs"):
            _f(ctx, "TRN001", "info", "no outputs listed: every node is saved", "List the nodes you compare.")


@rule("TRN002", "Edges faster than the time step",
      "A pulse with tr = 0 is an ideal step: SPICE replaces it with its own minimum rise time (ngspice: tstep; "
      "Xyce: its breakpoint logic), so the 'same' source differs between simulators. Give tr explicitly, a few "
      "output steps long.",
      "ngspice manual (PULSE defaults TR = TSTEP)")
def _trn002(ctx: Ctx):
    for a in ctx.analyses("tran"):
        for c in ctx.comps("vsource", "isource"):
            p = c.get("pulse")
            if p and (not p["tr"] or p["tr"] < a["step"]):
                _f(ctx, "TRN002", "warning", f"{c['name']}: rise time {p['tr']:g} s < output step {a['step']:g} s",
                   f"Set tr >= {2 * a['step']:.3g} s.")


@rule("HB001", "Harmonic balance: harmonics and drive",
      "HB truncates the spectrum at K harmonics. For a diode driven by 1 V through 50 ohm, K = 8 put the 7th-8th "
      "harmonics 2.6 dB (Xyce vs circuitRF) to 23 dB (vs a converged transient) off; with K = 32 circuitRF, Xyce "
      "and ngspice transient + DFT agreed to 0.02 dB on harmonics 0-8. qucsator's HB did not converge on that "
      "case at all. Raise K until the harmonics you report stop moving.",
      "this package's diode HB example; circuitRF HB reference")
def _hb001(ctx: Ctx):
    for a in ctx.analyses("hb"):
        drive = max([abs((c.get("sin") or {}).get("amp", 0)) for c in ctx.comps("vsource")] + [0])
        if ctx.comps("diode") and drive > 0.5 and a["harmonics"] < 16:
            _f(ctx, "HB001", "warning", f"{a['harmonics']} harmonics for a {drive:g} V drive into a diode",
               "Use 16-32 harmonics and check that the harmonics you report stop changing.")
        if not ctx.comps("vsource", "isource"):
            _f(ctx, "HB001", "error", "HB without a tone source", "Add a vsource with sin {amp, freq = f0}.")
        for c in ctx.comps("vsource", "isource"):
            f = (c.get("sin") or {}).get("freq")
            if f and abs(f / a["f0"] - round(f / a["f0"])) > 1e-9:
                _f(ctx, "HB001", "error", f"{c['name']}: {f:g} Hz is not a harmonic of f0 = {a['f0']:g} Hz",
                   "Single-tone HB: every source at a multiple of f0.")


@rule("SUP001", "Simulator coverage for this circuit",
      "Not every simulator supports every element/analysis pair (no Touchstone in ngspice, no transient in "
      "circuitRF, no HB in ngspice, Xyce frequency-domain lines). A cross-check that silently drops a tool looks "
      "like agreement; this rule lists who will run.",
      "this package's coverage matrix")
def _sup001(ctx: Ctx):
    from .translate import Unsupported, backends

    for a in ctx.s["analyses"]:
        who, why = [], {}
        for name, b in backends().items():
            if ctx.sims and name not in ctx.sims:
                continue
            try:
                b.build(ctx.s, a)
                who.append(name)
            except Unsupported as e:
                why[name] = str(e)
            except Exception as e:  # noqa: BLE001
                why[name] = f"{type(e).__name__}: {e}"
        sev = "warning" if len(who) < 2 else "info"
        _f(ctx, "SUP001", sev, f"{a['type']}: runs in {who or 'no simulator'}" +
           (f"; skipped: {sorted(why)}" if why else ""), "A cross-check needs at least two independent tools.",
           analysis=a["type"], runs_in=who, skipped=why)


# ============================================================================================ driver

def lint(spec, context: dict | None = None) -> dict:
    """Run every rule. context: {"sims": [...], "skip_reference": bool}."""
    context = context or {}
    raw = load(spec)
    try:
        s = normalize(raw)
    except SpecError as e:
        f = {"id": "SPEC", "severity": "error", "title": "Spec does not validate", "message": str(e),
             "fix": "Fix the spec (circuit-tools spec shows the format).", "lesson": "", "source": ""}
        return {"ok": False, "counts": {"error": 1, "warning": 0, "info": 0}, "findings": [f], "skipped": []}
    ctx = Ctx(raw=raw, s=s, sims=list(context.get("sims") or []))
    skipped = []
    for rid, r in RULES.items():
        if r["needs"] == "reference" and context.get("skip_reference"):
            skipped.append(rid)
            continue
        try:
            r["fn"](ctx)
        except Exception as e:  # noqa: BLE001  a broken rule must not hide the others
            ctx.findings.append({"id": rid, "severity": "info", "title": "rule failed", "message": f"{e}",
                                 "fix": "", "lesson": "", "source": ""})
    sev = {"error": 0, "warning": 1, "info": 2}
    ctx.findings.sort(key=lambda f: (sev[f["severity"]], f["id"]))
    counts = {k: sum(1 for f in ctx.findings if f["severity"] == k) for k in sev}
    for c in s["components"]:
        c.pop("_info", None)
    return {"ok": counts["error"] == 0, "counts": counts, "findings": ctx.findings, "skipped": skipped}


def rules_table() -> list[dict]:
    return [{k: v for k, v in r.items() if k != "fn"} for r in RULES.values()]


__all__ = ["lint", "rules_table", "RULES", "GROUND_ALIASES", "parse_value", "frequencies"]
