"""Qucs-S / qucsator(-RF) netlists and the Qucs dataset (.dat) reader.

Qucs-S is a GUI that writes this netlist format and runs qucsator_rf (its RF engine) on it, or translates the
schematic to SPICE for ngspice/Xyce. We emit the qucsator netlist directly (``qucsator_rf -i x.net -o x.dat``),
which is what Qucs-S itself runs for the qucsator engine; the output is the Qucs dataset format Qucs-S plots.

Notes: ground is 'gnd'. TLIN has no eeff (a line given as length + eeff is written with the electrical length
L*sqrt(eeff) in air). MLIN takes a SUBST (rho = 1/sigma, D = rms roughness), Hammerstad + Kirschning models.
SPfile has N+1 terminals (the last is the reference). The Diode takes the cathode FIRST (the opposite of SPICE). The Vac source is a sine in transient (cosine-referenced
spec phases get +90 degrees). HB converged poorly for strongly driven diodes in the build tested.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from ..spec import ports
from ..units import fmt
from . import Job, Unsupported

LUMPED = ["R", "L", "C", "port", "vsource", "isource"]


def _n(n: str) -> str:
    return "gnd" if n == "0" else re.sub(r"[^A-Za-z0-9_]", "_", n)


def _id(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


class QucsatorBackend:
    NAME = "qucsator"
    EXE = "qucsator_rf"
    SUPPORT = {
        "sparam": {"components": LUMPED + ["tline", "mline", "sblock"], "notes": ".SP; MLIN Hammerstad/Kirschning; "
                                                                            "SPfile linear interpolation"},
        "dc": {"components": LUMPED + ["diode", "tline", "mline", "sblock"], "notes": ".DC; SPfile duringDC=open"},
        "tran": {"components": LUMPED + ["diode", "tline"], "notes": ".TR"},
        "hb": {"components": LUMPED + ["diode"], "notes": ".HB (converges poorly on hard-driven diodes)"},
    }

    def lines(self, s: dict, a: dict, job: Job) -> list[str]:
        out = []
        T = fmt(s["temp_c"])
        kind = a["type"]
        for c in s["components"]:
            t, nd = c["type"], [_n(n) for n in c["nodes"]]
            nm = _id(c["name"])
            if t == "R":
                out.append(f'R:{nm} {nd[0]} {nd[1]} R="{fmt(c["value"])}" Temp="{T}" Tc1="0" Tc2="0" Tnom="{T}"')
            elif t == "L":
                out.append(f'L:{nm} {nd[0]} {nd[1]} L="{fmt(c["value"])}" I=""')
            elif t == "C":
                out.append(f'C:{nm} {nd[0]} {nd[1]} C="{fmt(c["value"])}" V=""')
            elif t == "tline":
                el = c["length"] * c["eeff"] ** 0.5
                out.append(f'TLIN:{nm} {nd[0]} {nd[1]} Z="{fmt(c["z0"])}" L="{fmt(el)}" Alpha="0 dB" Temp="{T}"')
            elif t == "mline":
                sub = s["substrates"][c["substrate"]]
                sn = "Sub_" + _id(c["substrate"])
                line = (f'SUBST:{sn} er="{fmt(sub["er"])}" h="{fmt(sub["h"])}" t="{fmt(sub["t"])}" '
                        f'tand="{fmt(sub["tand"])}" rho="{fmt(1 / sub["sigma"])}" D="{fmt(sub["rough"])}"')
                if line not in out:
                    out.insert(0, line)
                out.append(f'MLIN:{nm} {nd[0]} {nd[1]} Subst="{sn}" W="{fmt(c["w"])}" L="{fmt(c["l"])}" '
                           f'Model="Hammerstad" DispModel="Kirschning" Temp="{T}"')
            elif t == "sblock":
                fn = Path(c["file"]).name
                job.copies[fn] = c["file"]
                out.append(f'SPfile:{nm} {" ".join(nd)} {_n(c["ref"])} File="{{{fn}}}" Data="rectangular" '
                           f'Interpolator="linear" duringDC="open"')
            elif t == "port":
                if kind == "sparam":
                    out.append(f'Pac:{nm} {nd[0]} {nd[1]} Num="{c["num"]}" Z="{fmt(c["z0"])}" P="0 dBm" f="1 GHz" '
                               f'Temp="{T}"')
                else:
                    out.append(f'R:port_{nm} {nd[0]} {nd[1]} R="{fmt(c["z0"])}" Temp="{T}" Tc1="0" Tc2="0" '
                               f'Tnom="{T}"')
            elif t in ("vsource", "isource"):
                out += self.source(c, a, nd, nm)
            elif t == "diode":
                m = c["model"]
                # qucsator's Diode takes the CATHODE first (diode.cpp: NODE_C 0, NODE_A 1); the spec is anode, cathode
                out.append(f'Diode:{nm} {nd[1]} {nd[0]} Is="{fmt(m["is"])}" N="{fmt(m["n"])}" Cj0="{fmt(m["cj0"])}" '
                           f'M="{fmt(m["m"])}" Vj="{fmt(m["vj"])}" Fc="{fmt(m["fc"])}" Cp="0" Isr="0" Nr="2" '
                           f'Rs="{fmt(m["rs"])}" Tt="{fmt(m["tt"])}" Ikf="0" Kf="0" Af="1" Ffe="1" '
                           f'Bv="{fmt(m["bv"])}" Ibv="{fmt(m["ibv"])}" Temp="{T}" Xti="3" Eg="1.11" Tbv="0" '
                           f'Trs="0" Ttt1="0" Ttt2="0" Tm1="0" Tm2="0" Tnom="{T}" Area="1"')
            else:
                raise Unsupported(f"qucsator: {t} {c['name']}")
        return out

    def source(self, c, a, nd, nm) -> list[str]:
        """Sources as qucsator parts. Voltage parts are chained in series through internal nodes (parallel ideal
        voltage sources would conflict); current parts sit in parallel."""
        V = c["type"] == "vsource"
        u = "U" if V else "I"
        kind = a["type"]
        segs = []  # (part, value text)
        if kind == "sparam":
            return []  # an ideal V source would short the node; S-parameter circuits use ports
        dc = c["dc"] + (c["sin"]["offset"] if c.get("sin") else 0.0)
        if dc and not (kind == "tran" and c.get("pulse")):
            segs.append(("Vdc" if V else "Idc", f'{u}="{fmt(dc)}"'))
        if c.get("sin") and kind in ("tran", "hb"):
            s = c["sin"]
            f = s.get("freq") or a.get("f0")
            ph = s["phase_deg"] + (90 if kind == "tran" else 0)
            segs.append(("Vac" if V else "Iac", f'{u}="{fmt(s["amp"])}" f="{fmt(f)}" Phase="{fmt(ph)}" Theta="0"'))
        elif c.get("pulse") and kind == "tran":
            p = c["pulse"]
            if p["per"] is not None:
                raise Unsupported("qucsator: periodic pulse not translated (use Vrect by hand)")
            t2 = p["td"] + p["tr"] + (p["pw"] if p["pw"] is not None else 1e3)
            u1, u2 = ("U1", "U2") if V else ("I1", "I2")
            segs.append(("Vpulse" if V else "Ipulse", f'{u1}="{fmt(p["v1"])}" {u2}="{fmt(p["v2"])}" T1="{fmt(p["td"])}" '
                         f'T2="{fmt(t2)}" Tr="{fmt(p["tr"] or 1e-15)}" Tf="{fmt(p["tf"] or 1e-15)}"'))
        if not segs:
            segs.append(("Vdc" if V else "Idc", f'{u}="{fmt(c["dc"])}"'))
        out = []
        a_node = nd[0]
        for k, (part, val) in enumerate(segs):
            last = k == len(segs) - 1
            b_node = nd[1] if (last or not V) else f"{nm}_s{k}"
            out.append(f"{part}:{nm}{'' if k == 0 else '_' + str(k)} {a_node if V else nd[0]} {b_node} {val}")
            a_node = b_node
        return out

    def build(self, s: dict, a: dict) -> Job:
        job = Job(self.NAME)
        body = [f"# {s['name']} ({a['type']}) - written by circuit-agent-tools (Qucs-S / qucsator_rf netlist)"]
        body += self.lines(s, a, job)
        T = fmt(s["temp_c"])
        if a["type"] == "sparam":
            if not ports(s):
                raise Unsupported("sparam analysis without ports")
            body.append(f'.SP:SP1 Type="{a["scale"]}" Start="{fmt(a["start"])}" Stop="{fmt(a["stop"])}" '
                        f'Points="{a["points"]}" Noise="no" NoiseIP="1" NoiseOP="2" saveCVs="no" saveAll="no"')
            job.meta = {"ports": [(p["num"], p["z0"]) for p in ports(s)]}
        elif a["type"] == "dc":
            body.append(f'.DC:DC1 Temp="{T}" reltol="0.001" abstol="1 pA" vntol="1 uV" saveOPs="no" MaxIter="150" '
                        'saveAll="no" convHelper="none" Solver="CroutLU"')
        elif a["type"] == "tran":
            npts = int(round((a["stop"] - a["start"]) / a["step"])) + 1
            body.append(f'.TR:TR1 Type="lin" Start="{fmt(a["start"])}" Stop="{fmt(a["stop"])}" Points="{npts}" '
                        f'IntegrationMethod="Trapezoidal" Order="2" InitialStep="{fmt(a["step"] / 10)}" '
                        f'MinStep="1e-16" MaxIter="150" reltol="0.001" abstol="1 pA" vntol="1 uV" Temp="{T}" '
                        f'LTEreltol="1e-3" LTEabstol="1e-6" LTEfactor="1" Solver="CroutLU" relaxTSR="no" '
                        f'initialDC="yes" MaxStep="{fmt(a["step"])}"')
        elif a["type"] == "hb":
            body.append(f'.HB:HB1 f="{fmt(a["f0"])}" n="{a["harmonics"] + 1}" iabstol="1 pA" vabstol="1 uV" '
                        f'reltol="0.001" MaxIter="{a.get("max_iter", 500)}"')
        job.meta["outputs"] = a.get("outputs") or sorted({n for c in s["components"] for n in c["nodes"] if n != "0"})
        job.files["case.net"] = "\n".join(body) + "\n"
        job.commands.append("{exe} -i case.net -o case.dat > case.log 2>&1")
        return job

    def parse(self, case: Path, s: dict, a: dict, job: Job) -> dict:
        ds = read_dataset(Path(case) / "case.dat")
        if a["type"] == "sparam":
            pts = job.meta["ports"]
            n = len(pts)
            f = ds["indep"]["frequency"]
            S = np.zeros((len(f), n, n), complex)
            for i in range(n):
                for j in range(n):
                    S[:, i, j] = ds["dep"][f"S[{i + 1},{j + 1}]"]
            return {"kind": "sparam", "f": f, "s": S, "z0": np.array([p[1] for p in pts])}
        outs = job.meta["outputs"]
        if a["type"] == "dc":  # a DC dataset stores each node voltage as an 'indep' of length 1
            allv = {**ds["indep"], **ds["dep"]}
            return {"kind": "dc", "v": {n: float(np.real(allv[f"{_n(n)}.V"][0])) for n in outs if f"{_n(n)}.V" in allv}}
        if a["type"] == "tran":
            t = ds["indep"]["time"]
            return {"kind": "tran", "t": t, "v": {n: np.real(ds["dep"][f"{_n(n)}.Vt"]) for n in outs}}
        f = np.real(ds["indep"]["hbfrequency"])
        res = {}
        for n in outs:
            x = ds["dep"][f"{_n(n)}.Vb"]
            v = np.zeros(a["harmonics"] + 1, complex)
            for k in range(a["harmonics"] + 1):
                i = int(np.argmin(np.abs(f - k * a["f0"])))
                if abs(f[i] - k * a["f0"]) <= 1e-6 * a["f0"] + 1e-9:
                    v[k] = x[i]
            res[n] = v
        log = (Path(case) / "case.log").read_text(encoding="utf-8", errors="replace")
        return {"kind": "hb", "f0": a["f0"], "method": "hb", "v": res,
                "converged": "no convergence" not in log}


_CPLX = re.compile(r"^([-+]?[0-9.]+(?:e[-+]?\d+)?)(?:([-+])j([0-9.]+(?:e[-+]?\d+)?))?$", re.I)


def _val(tok: str) -> complex:
    m = _CPLX.match(tok.strip())
    if not m:
        return complex(float("nan"))
    re_ = float(m.group(1))
    if m.group(2):
        im = float(m.group(3)) * (-1 if m.group(2) == "-" else 1)
        return complex(re_, im)
    return complex(re_, 0)


def read_dataset(path: Path) -> dict:
    """Qucs dataset -> {"indep": {name: array}, "dep": {name: array}} (complex arrays)."""
    indep, dep = {}, {}
    cur, name, vals = None, None, []
    for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if ln.startswith("<indep ") or ln.startswith("<dep "):
            parts = ln.strip("<>").split()
            cur, name, vals = parts[0], parts[1], []
        elif ln in ("</indep>", "</dep>"):
            arr = np.array(vals, complex)
            (indep if cur == "indep" else dep)[name] = np.real(arr) if cur == "indep" else arr
            cur = None
        elif cur and ln:
            vals.append(_val(ln))
    return {"indep": indep, "dep": dep}


QUCSATOR = QucsatorBackend()
