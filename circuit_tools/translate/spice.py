"""SPICE translators: ngspice (batch, .control + wrdata) and Xyce (.LIN / .AC / .TRAN / .HB, CSV output).

S-parameters
  ngspice: one AC run per driven port. Port k is a source (AC 2 on the driven port, 0 elsewhere) behind its z0;
           with real z0, S_jj = V_j - 1 and S_ij = V_i sqrt(z0_j / z0_i).
  Xyce:    native .LIN with P ports (Touchstone 2 output).
Harmonic balance
  Xyce:    .HB (frequency-domain CSV, two-sided spectrum -> single-sided peak phasors).
  ngspice: no HB engine; transient for ``settle_periods`` + ``fft_periods`` periods and an exact-bin DFT of the
           last ``fft_periods`` (method "tran+dft"), so it is a steady-state check, not a second HB solver.
Measured limits of the Xyce 7.10 build used here (silently wrong, so these raise Unsupported in .AC/.LIN):
  T (ideal line) and LTRA behave as a wire, YLIN (Touchstone) as an open circuit. YLIN works in .HB; T in .TRAN.
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path

import numpy as np

from .. import microstrip
from ..spec import frequencies, ports
from ..units import fmt
from . import Job, Unsupported

LUMPED = ["R", "L", "C", "port", "vsource", "isource"]


def _name(prefix: str, name: str) -> str:
    """SPICE element names start with the type letter; keep the user's name after it."""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return clean if clean[:1].upper() == prefix.upper() else prefix + "_" + clean


def _node(n: str) -> str:
    return "0" if n == "0" else re.sub(r"[^A-Za-z0-9_]", "_", n)


def _wave(c: dict, kind: str, f0: float | None = None) -> str:
    parts = []
    if kind == "dc":
        return f"DC {fmt(c['dc'])}"
    parts.append(f"DC {fmt(c['dc'])}")
    if kind == "ac":
        parts.append(f"AC {fmt(c['ac'])}")
        return " ".join(parts)
    if c.get("sin"):
        s = c["sin"]
        f = s.get("freq") or f0
        # spec sinusoids are cosine-referenced: SPICE SIN is a sine, so add 90 degrees
        parts.append(f"SIN({fmt(s['offset'])} {fmt(s['amp'])} {fmt(f)} 0 0 {fmt(s['phase_deg'] + 90)})")
    elif c.get("pulse"):
        p = c["pulse"]
        pw = p["pw"] if p["pw"] is not None else 1e3
        per = p["per"] if p["per"] is not None else 2e3
        parts.append(f"PULSE({fmt(p['v1'])} {fmt(p['v2'])} {fmt(p['td'])} {fmt(p['tr'] or 1e-15)} "
                     f"{fmt(p['tf'] or 1e-15)} {fmt(pw)} {fmt(per)})")
    return " ".join(parts)


class _Spice:
    NAME = "spice"
    EXE = "spice"

    def element_lines(self, s: dict, a: dict, models: list) -> list[str]:
        out = []
        kind = a["type"]
        for c in s["components"]:
            t, nd = c["type"], [_node(n) for n in c["nodes"]]
            if t in ("R", "L", "C"):
                out.append(f"{_name(t, c['name'])} {nd[0]} {nd[1]} {fmt(c['value'])}")
            elif t == "port":
                if kind == "sparam":
                    continue  # written by the analysis
                out.append(f"{_name('R', 'port_' + c['name'])} {nd[0]} {nd[1]} {fmt(c['z0'])}")
            elif t in ("vsource", "isource"):
                letter = "V" if t == "vsource" else "I"
                wk = {"sparam": "ac", "dc": "dc", "tran": "tran", "hb": "tran"}[kind]
                out.append(f"{_name(letter, c['name'])} {nd[0]} {nd[1]} {_wave(c, wk, a.get('f0'))}")
            elif t == "diode":
                m = c["model"]
                mn = "DM_" + re.sub(r"[^A-Za-z0-9_]", "_", c["name"])
                keys = [("IS", m["is"]), ("N", m["n"]), ("RS", m["rs"]), ("CJO", m["cj0"]), ("VJ", m["vj"]),
                        ("M", m["m"]), ("TT", m["tt"]), ("FC", m["fc"])]
                if m["bv"] > 0:  # BV = 0 would put the breakdown at 0 V in SPICE; omit for 'no breakdown'
                    keys += [("BV", m["bv"]), ("IBV", m["ibv"])]
                models.append(f".model {mn} D (" + " ".join(f"{k}={fmt(v)}" for k, v in keys) + ")")
                out.append(f"{_name('D', c['name'])} {nd[0]} {nd[1]} {mn}")
            else:
                out += self.distributed(c, s, a, models)
        return out

    def distributed(self, c, s, a, models):
        raise Unsupported(f"{self.NAME}: {c['type']} {c['name']} not supported")

    def outputs(self, s, a) -> list[str]:
        if a.get("outputs"):
            return a["outputs"]
        return sorted({n for c in s["components"] for n in c["nodes"] if n != "0"})


class NgspiceBackend(_Spice):
    NAME = "ngspice"
    EXE = "ngspice"
    SUPPORT = {
        "sparam": {"components": LUMPED + ["tline", "mline"],
                   "notes": "AC drive per port; mline as an LTRA RLC line frozen at f_ref (no dispersion, R constant, "
                            "dielectric loss folded into R at f_ref); no Touchstone blocks"},
        "dc": {"components": LUMPED + ["diode", "tline"], "notes": "op + print"},
        "tran": {"components": LUMPED + ["diode", "tline", "mline"], "notes": "tran + linearize + wrdata"},
        "hb": {"components": LUMPED + ["diode", "tline"], "notes": "no HB engine: transient + exact-bin DFT"},
    }

    def distributed(self, c, s, a, models):
        nd = [_node(n) for n in c["nodes"]]
        if c["type"] == "tline":
            return [f"{_name('T', c['name'])} {nd[0]} 0 {nd[1]} 0 Z0={fmt(c['z0'])} TD={fmt(c['delay_s'])}"]
        if c["type"] == "mline":
            sub = s["substrates"][c["substrate"]]
            f_ref = c.get("f_ref") or _f_center(a)
            p = microstrip.rlgc(f_ref, c["w"], sub)
            # ngspice's LTRA has no shunt G: put the dielectric loss into R so alpha matches at f_ref
            r_eff = p["R"] + p["G"] * abs(p["z0"]) ** 2
            mn = "LT_" + re.sub(r"[^A-Za-z0-9_]", "_", c["name"])
            models.append(f".model {mn} LTRA R={fmt(r_eff)} L={fmt(p['L'])} G=0 C={fmt(p['C'])} LEN={fmt(c['l'])}")
            return [f"{_name('O', c['name'])} {nd[0]} 0 {nd[1]} 0 {mn}"]
        raise Unsupported(f"ngspice: {c['type']} {c['name']} (Touchstone blocks) not supported; use qucsator, "
                          "circuitRF or scikit-rf")

    def build(self, s: dict, a: dict) -> Job:
        job = Job(self.NAME)
        models: list[str] = []
        head = [f"* {s['name']} ({a['type']}) - written by circuit-agent-tools", f".options temp={fmt(s['temp_c'])} "
                f"tnom={fmt(s['temp_c'])}"]
        body = self.element_lines(s, a, models)
        if a["type"] == "sparam":
            pts = ports(s)
            if not pts:
                raise Unsupported("sparam analysis without ports")
            f = a
            sweep = (f"ac lin {f['points']} {fmt(f['start'])} {fmt(f['stop'])}" if f["scale"] == "lin" else
                     f"ac dec {max(1, round(f['points'] / max(1e-9, math.log10(f['stop'] / f['start']))))} "
                     f"{fmt(f['start'])} {fmt(f['stop'])}")
            vecs = " ".join(f"v({_node(p['nodes'][0])})" if p["nodes"][1] == "0" else
                            f"v({_node(p['nodes'][0])},{_node(p['nodes'][1])})" for p in pts)
            for j, pj in enumerate(pts):
                lines = list(head) + body
                for p in pts:
                    src = f"psrc_{p['num']}"
                    lines.append(f"V_port{p['num']} {src} {_node(p['nodes'][1])} DC 0 AC {2 if p is pj else 0}")
                    lines.append(f"R_port{p['num']} {src} {_node(p['nodes'][0])} {fmt(p['z0'])}")
                lines += models + [".control", "set wr_singlescale", "set wr_vecnames", "set filetype=ascii", sweep,
                                   f"wrdata sp_{j + 1}.txt {vecs}", "quit", ".endc", ".end"]
                job.files[f"sp_{j + 1}.cir"] = "\n".join(lines) + "\n"
                job.commands.append(f"{{exe}} -b sp_{j + 1}.cir > sp_{j + 1}.log 2>&1")
            job.meta = {"ports": [(p["num"], p["z0"]) for p in pts]}
            if a["scale"] != "lin":
                job.notes.append("ngspice 'ac dec' grid differs from a log-spaced grid; results are interpolated")
        elif a["type"] == "dc":
            outs = self.outputs(s, a)
            lines = head + body + models + [".control", "op", "print " + " ".join(f"v({_node(n)})" for n in outs),
                                            "quit", ".endc", ".end"]
            job.files["dc.cir"] = "\n".join(lines) + "\n"
            job.commands.append("{exe} -b dc.cir > dc.log 2>&1")
            job.meta = {"outputs": outs}
        elif a["type"] in ("tran", "hb"):
            outs = self.outputs(s, a)
            if a["type"] == "tran":
                step, stop, start = a["step"], a["stop"], a["start"]
            else:
                T = 1 / a["f0"]
                ns = int(a.get("samples_per_period", max(64, 16 * a["harmonics"])))
                step = T / ns
                start = a.get("settle_periods", 30) * T
                stop = start + a.get("fft_periods", 10) * T
                job.meta.update({"T": T, "ns": ns, "start": start, "stop": stop})
                job.notes.append("ngspice has no harmonic balance: transient to steady state + DFT")
            lines = head + body + models + [".control", "set wr_singlescale", "set wr_vecnames",
                                            f"tran {fmt(step)} {fmt(stop)} {fmt(start)} {fmt(step)}",
                                            "linearize " + " ".join(f"v({_node(n)})" for n in outs),
                                            "wrdata tran.txt " + " ".join(f"v({_node(n)})" for n in outs),
                                            "quit", ".endc", ".end"]
            job.files["tran.cir"] = "\n".join(lines) + "\n"
            job.commands.append("{exe} -b tran.cir > tran.log 2>&1")
            job.meta.update({"outputs": outs, "step": step, "start": start, "stop": stop})
        return job

    def parse(self, case: Path, s: dict, a: dict, job: Job) -> dict:
        case = Path(case)
        if a["type"] == "sparam":
            pts = job.meta["ports"]
            n = len(pts)
            cols = None
            f = None
            for j in range(n):
                d = _read_wrdata(case / f"sp_{j + 1}.txt")
                f = d[:, 0]
                v = d[:, 1::2] + 1j * d[:, 2::2]
                if cols is None:
                    cols = np.zeros((len(f), n, n), complex)
                zj = pts[j][1]
                for i in range(n):
                    zi = pts[i][1]
                    cols[:, i, j] = v[:, i] - 1 if i == j else v[:, i] * math.sqrt(zj / zi)
            return {"kind": "sparam", "f": f, "s": cols, "z0": np.array([p[1] for p in pts])}
        if a["type"] == "dc":
            txt = (case / "dc.log").read_text(encoding="utf-8", errors="replace")
            v = {}
            for n in job.meta["outputs"]:
                m = re.search(r"^\s*v\(" + re.escape(_node(n).lower()) + r"\)\s*=\s*([-+0-9.eE]+)", txt, re.M | re.I)
                if m:
                    v[n] = float(m.group(1))
            return {"kind": "dc", "v": v}
        d = _read_wrdata(case / "tran.txt")
        t = d[:, 0]
        outs = job.meta["outputs"]
        wav = {n: d[:, 1 + k] for k, n in enumerate(outs)}
        if a["type"] == "tran":
            return {"kind": "tran", "t": t, "v": wav}
        return {"kind": "hb", "f0": a["f0"], "method": "tran+dft",
                "v": {n: _dft(t, x, job.meta["T"], job.meta["start"], job.meta["stop"], a["harmonics"])
                      for n, x in wav.items()}}


class XyceBackend(_Spice):
    NAME = "xyce"
    EXE = "Xyce"
    SUPPORT = {
        "sparam": {"components": LUMPED, "notes": ".LIN (Touchstone 2). T, LTRA and YLIN are silently wrong in .AC "
                                                  "in the 7.10 build tested (wire, wire, open), so they are refused"},
        "dc": {"components": LUMPED + ["diode"], "notes": ".DC over a dummy source"},
        "tran": {"components": LUMPED + ["diode", "tline"], "notes": ".TRAN, resampled to the output step"},
        "hb": {"components": LUMPED + ["diode", "sblock"], "notes": ".HB; YLIN Touchstone blocks work here"},
    }

    def distributed(self, c, s, a, models):
        nd = [_node(n) for n in c["nodes"]]
        if c["type"] == "tline" and a["type"] == "tran":
            return [f"{_name('T', c['name'])} {nd[0]} 0 {nd[1]} 0 Z0={fmt(c['z0'])} TD={fmt(c['delay_s'])}"]
        if c["type"] == "sblock" and a["type"] == "hb":
            mn = "YM_" + re.sub(r"[^A-Za-z0-9_]", "_", c["name"])
            fn = Path(c["file"]).name
            models.append(f".MODEL {mn} LIN TSTONEFILE={fn}")
            pairs = " ".join(f"{n} {_node(c['ref'])}" for n in nd)
            return [f"YLIN {re.sub(r'[^A-Za-z0-9_]', '_', c['name'])} {pairs} {mn}"]
        raise Unsupported(f"xyce: {c['type']} {c['name']} in .{a['type'].upper()} is not reliable in Xyce 7.10 "
                          "(measured: T/LTRA act as a wire and YLIN as an open circuit in .AC/.LIN)")

    def build(self, s: dict, a: dict) -> Job:
        job = Job(self.NAME)
        models: list[str] = []
        head = [f"* {s['name']} ({a['type']}) - written by circuit-agent-tools",
                f".OPTIONS DEVICE TEMP={fmt(s['temp_c'])} TNOM={fmt(s['temp_c'])}"]
        body = self.element_lines(s, a, models)
        for c in s["components"]:
            if c["type"] == "sblock":
                job.copies[Path(c["file"]).name] = c["file"]
        if a["type"] == "sparam":
            pts = ports(s)
            if not pts:
                raise Unsupported("sparam analysis without ports")
            for p in pts:
                body.append(f"P{p['num']} {_node(p['nodes'][0])} {_node(p['nodes'][1])} port={p['num']} "
                            f"z0={fmt(p['z0'])}")
            sweep = (f".AC LIN {a['points']} {fmt(a['start'])} {fmt(a['stop'])}" if a["scale"] == "lin" else
                     f".AC DEC {max(1, round(a['points'] / max(1e-9, math.log10(a['stop'] / a['start']))))} "
                     f"{fmt(a['start'])} {fmt(a['stop'])}")
            n = len(pts)
            job.files["sp.cir"] = "\n".join(head + body + models + [sweep, f".LIN SPARCALC=1 FORMAT=TOUCHSTONE2 "
                                                                    f"FILE=sp.s{n}p", ".END"]) + "\n"
            job.commands.append("{exe} sp.cir > sp.log 2>&1")
            job.meta = {"n": n, "z0": [p["z0"] for p in pts]}
        elif a["type"] == "dc":
            outs = self.outputs(s, a)
            body += ["V_xsweep x_sweep 0 DC 0", "R_xsweep x_sweep 0 1k"]
            job.files["dc.cir"] = "\n".join(head + body + models + [
                ".DC V_xsweep 0 0 1", ".PRINT DC FORMAT=CSV FILE=dc.csv " + " ".join(f"V({_node(n)})" for n in outs),
                ".END"]) + "\n"
            job.commands.append("{exe} dc.cir > dc.log 2>&1")
            job.meta = {"outputs": outs}
        elif a["type"] == "tran":
            outs = self.outputs(s, a)
            job.files["tran.cir"] = "\n".join(head + body + models + [
                f".TRAN {fmt(a['step'])} {fmt(a['stop'])} {fmt(a['start'])} {fmt(a['step'])}",
                ".PRINT TRAN FORMAT=CSV FILE=tran.csv " + " ".join(f"V({_node(n)})" for n in outs), ".END"]) + "\n"
            job.commands.append("{exe} tran.cir > tran.log 2>&1")
            job.meta = {"outputs": outs}
        elif a["type"] == "hb":
            outs = self.outputs(s, a)
            job.files["hb.cir"] = "\n".join(head + body + models + [
                f".HB {fmt(a['f0'])}", f".OPTIONS HBINT NUMFREQ={a['harmonics']}",
                ".PRINT HB FORMAT=CSV " + " ".join(f"VR({_node(n)}) VI({_node(n)})" for n in outs), ".END"]) + "\n"
            job.commands.append("{exe} hb.cir > hb.log 2>&1")
            job.meta = {"outputs": outs}
        return job

    def parse(self, case: Path, s: dict, a: dict, job: Job) -> dict:
        case = Path(case)
        if a["type"] == "sparam":
            import skrf as rf

            n = rf.Network(str(case / f"sp.s{job.meta['n']}p"))
            return {"kind": "sparam", "f": n.f, "s": n.s, "z0": np.real(n.z0[0])}
        if a["type"] == "dc":
            rows = _read_csv(case / "dc.csv")
            return {"kind": "dc", "v": {n: float(rows[f"V({_node(n).upper()})"][-1]) for n in job.meta["outputs"]}}
        if a["type"] == "tran":
            rows = _read_csv(case / "tran.csv")
            t = np.asarray(rows["TIME"], float)
            tu = np.arange(a["start"], a["stop"] * (1 + 1e-12), a["step"])
            return {"kind": "tran", "t": tu, "v": {n: np.interp(tu, t, np.asarray(rows[f"V({_node(n).upper()})"], float))
                                                    for n in job.meta["outputs"]}}
        rows = _read_csv(case / "hb.cir.HB.FD.csv")
        f = np.asarray(rows["FREQ"], float)
        out = {}
        for n in job.meta["outputs"]:
            x = np.asarray(rows[f"VR({_node(n).upper()})"], float) + 1j * np.asarray(rows[f"VI({_node(n).upper()})"], float)
            v = np.zeros(a["harmonics"] + 1, complex)
            for k in range(a["harmonics"] + 1):
                i = np.argmin(np.abs(f - k * a["f0"]))
                if abs(f[i] - k * a["f0"]) <= 1e-6 * a["f0"]:
                    v[k] = x[i] if k == 0 else 2 * x[i]
            out[n] = v
        return {"kind": "hb", "f0": a["f0"], "method": "hb", "v": out}


def _f_center(a: dict) -> float:
    if a["type"] == "sparam":
        return math.sqrt(a["start"] * a["stop"]) if a["scale"] == "log" else 0.5 * (a["start"] + a["stop"])
    return a.get("f0") or 1e9


def _read_wrdata(p: Path) -> np.ndarray:
    rows = []
    for ln in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
        parts = ln.split()
        if not parts:
            continue
        try:
            rows.append([float(x) for x in parts])
        except ValueError:
            continue  # header
    return np.array(rows)


def _read_csv(p: Path) -> dict:
    with open(p, newline="", encoding="utf-8", errors="replace") as fh:
        r = list(csv.reader(fh))
    head = [h.strip().upper() for h in r[0]]
    cols = {h: [] for h in head}
    for row in r[1:]:
        if len(row) == len(head):
            for h, x in zip(head, row):
                cols[h].append(float(x))
    return cols


def _dft(t, x, T, start, stop, K) -> np.ndarray:
    """Single-sided peak phasors (cosine reference) of harmonics 0..K over the whole periods in [start, stop)."""
    t = np.asarray(t)
    m = (t >= start - 1e-6 * T) & (t < stop - 1e-6 * T)
    tt, xx = t[m], np.asarray(x)[m]
    out = np.zeros(K + 1, complex)
    w = 2 * np.pi / T
    for k in range(K + 1):
        out[k] = np.mean(xx * np.exp(-1j * k * w * tt)) * (1 if k == 0 else 2)
    return out


NGSPICE = NgspiceBackend()
XYCE = XyceBackend()
