"""scikit-rf reference solver for linear S-parameter analyses (skrf.circuit.Circuit).

Every element is a scikit-rf Network on the analysis frequencies, connected node by node with Circuit:
  * a two-terminal element to ground is a 1-port; between two other nodes it is a series 2-port
    (S11 = Z / (Z + 2 z0), S21 = 2 z0 / (Z + 2 z0) with z0 = 50; Circuit renormalises internally);
  * ideal lines are 2-ports from their ABCD matrix, microstrip comes from skrf.media.MLine;
  * S-blocks are read with skrf.Network and interpolated onto the sweep (scikit-rf refuses to extrapolate);
  * ports are Circuit.Port with their own z0; a node with a single connection gets Circuit.Open.
Only ground-referenced ports and S-blocks are supported (a floating reference raises Unsupported).
"""
from __future__ import annotations

import math
import time

import numpy as np

from . import microstrip
from .spec import C0, frequencies, normalize, ports
from .translate import Unsupported

Z_REF = 50.0
SUPPORT = {
    "sparam": {"components": ["R", "L", "C", "port", "isource", "tline", "mline", "sblock"],
               "notes": "skrf.circuit.Circuit; MLine (frequencyinvariant dielectric, rough=0); S-blocks cubic "
                        "interpolation, no extrapolation; ground-referenced ports only"},
    "dc": {"components": [], "notes": "not a DC solver"},
    "tran": {"components": [], "notes": "analytic helpers only (rc_step, rlc_series_step)"},
    "hb": {"components": [], "notes": "linear only"},
}


def _series(freq, name, z):
    import skrf as rf

    z = np.asarray(z, complex)
    s = np.zeros((len(z), 2, 2), complex)
    d = z + 2 * Z_REF
    s[:, 0, 0] = s[:, 1, 1] = z / d
    s[:, 0, 1] = s[:, 1, 0] = 2 * Z_REF / d
    return rf.Network(frequency=freq, s=s, z0=Z_REF, name=name)


def _shunt1(freq, name, z):
    import skrf as rf

    z = np.asarray(z, complex)
    return rf.Network(frequency=freq, s=((z - Z_REF) / (z + Z_REF))[:, None, None], z0=Z_REF, name=name)


def _abcd_line(freq, name, zc, gl):
    """Line of characteristic impedance zc and propagation gamma*l = gl (arrays over f)."""
    import skrf as rf

    zc = np.broadcast_to(np.asarray(zc, complex), gl.shape)
    a = np.zeros((len(gl), 2, 2), complex)
    a[:, 0, 0] = a[:, 1, 1] = np.cosh(gl)
    a[:, 0, 1] = zc * np.sinh(gl)
    a[:, 1, 0] = np.sinh(gl) / zc
    return rf.Network(frequency=freq, s=rf.network.a2s(a, Z_REF), z0=Z_REF, name=name)


def element_network(c: dict, spec: dict, freq, f: np.ndarray):
    t = c["type"]
    w = 2 * np.pi * f
    if t == "R":
        z = np.full(len(f), c["value"], complex)
    elif t == "L":
        z = 1j * w * c["value"]
    elif t == "C":
        with np.errstate(divide="ignore"):
            z = 1 / (1j * w * c["value"])
    elif t == "tline":
        gl = 1j * w * c["delay_s"]
        return _abcd_line(freq, c["name"], c["z0"], gl), 2
    elif t == "mline":
        m = microstrip.media(f, c["w"], spec["substrates"][c["substrate"]], Z_REF)
        n = m.line(c["l"], "m")
        n.name = c["name"]
        return n, 2
    elif t == "sblock":
        import skrf as rf

        n = rf.Network(c["file"])
        if f[0] < n.f[0] * (1 - 1e-9) or f[-1] > n.f[-1] * (1 + 1e-9):
            raise Unsupported(f"{c['name']}: sweep {f[0]:g}-{f[-1]:g} Hz outside the file's {n.f[0]:g}-{n.f[-1]:g} Hz "
                              "(scikit-rf does not extrapolate)")
        n = n.interpolate(freq, kind="cubic") if not np.array_equal(n.f, f) else n
        n.name = c["name"]
        if c["ref"] != "0":
            raise Unsupported(f"{c['name']}: floating S-block reference node")
        return n, n.nports
    else:
        raise Unsupported(f"{t} {c['name']} has no linear scikit-rf model")
    a, b = c["nodes"]
    if a == "0" and b == "0":
        return None, 0
    if "0" in (a, b):
        return _shunt1(freq, c["name"], z), 1
    return _series(freq, c["name"], z), 2


def solve_sparam(spec: dict, analysis: dict | None = None) -> dict:
    """S-parameters of the spec's circuit with scikit-rf; returns {kind, f, s, z0, wall_s, tool}."""
    import skrf as rf
    from skrf.circuit import Circuit

    s = normalize(spec) if "notes" not in spec else spec
    a = analysis or next(x for x in s["analyses"] if x["type"] == "sparam")
    f = frequencies(a)
    t0 = time.time()
    freq = rf.Frequency.from_f(f, unit="hz")
    conn: dict[str, list] = {}
    pts = ports(s)
    for p in pts:
        if p["nodes"][1] != "0":
            raise Unsupported(f"port {p['name']}: floating reference node {p['nodes'][1]!r}")
        conn.setdefault(p["nodes"][0], []).append(
            (Circuit.Port(freq, name=f"P{p['num']}", z0=p["z0"]), 0))
    for c in s["components"]:
        if c["type"] == "port":
            continue
        if c["type"] in ("vsource", "isource"):
            if c["type"] == "vsource":
                raise Unsupported(f"vsource {c['name']} in an S-parameter circuit")
            continue  # an ideal current source is an open circuit for small signals
        if c["type"] == "diode":
            raise Unsupported(f"diode {c['name']} is nonlinear")
        n, k = element_network(c, s, freq, f)
        if n is None:
            continue
        nodes = c["nodes"] if c["type"] != "sblock" else c["nodes"]
        if k == 1:
            nd = [x for x in c["nodes"] if x != "0"]
            conn.setdefault(nd[0], []).append((n, 0))
        else:
            for i, nd in enumerate(nodes[:k]):
                conn.setdefault(nd, []).append((n, i))
    gnd = conn.pop("0", [])
    cnx = []
    for nd, lst in conn.items():
        if len(lst) == 1:
            lst = lst + [(Circuit.Open(freq, name=f"open_{nd}"), 0)]
        cnx.append(lst)
    if gnd:
        cnx.append(gnd + [(Circuit.Ground(freq, name="gnd"), 0)])
    cir = Circuit(cnx)
    net = cir.network
    names = cir.port_names
    order = [names.index(f"P{p['num']}") for p in pts]
    S = net.s[:, order][:, :, order]
    z0 = np.array([p["z0"] for p in pts], float)
    return {"kind": "sparam", "f": f, "s": S, "z0": z0, "wall_s": round(time.time() - t0, 4),
            "tool": {"name": "scikit-rf", "version": rf.__version__}}


def rc_step(t, r: float, c: float, v: float = 1.0, td: float = 0.0):
    """Analytic RC low-pass step response (helper for transient examples)."""
    t = np.asarray(t, float) - td
    return np.where(t > 0, v * (1 - np.exp(-t / (r * c))), 0.0)


def rlc_series_step(t, r: float, l: float, c: float, v: float = 1.0, td: float = 0.0):
    """Capacitor voltage of a series R-L-C driven by an ideal step v (underdamped or overdamped)."""
    t = np.asarray(t, float) - td
    a = r / (2 * l)
    w0 = 1 / math.sqrt(l * c)
    tt = np.maximum(t, 0)
    if a < w0:
        wd = math.sqrt(w0 ** 2 - a ** 2)
        y = 1 - np.exp(-a * tt) * (np.cos(wd * tt) + a / wd * np.sin(wd * tt))
    elif a > w0:
        s1, s2 = -a + math.sqrt(a * a - w0 * w0), -a - math.sqrt(a * a - w0 * w0)
        y = 1 - (s2 * np.exp(s1 * tt) - s1 * np.exp(s2 * tt)) / (s2 - s1)
    else:
        y = 1 - (1 + a * tt) * np.exp(-a * tt)
    return np.where(t > 0, v * y, 0.0)


__all__ = ["solve_sparam", "Unsupported", "rc_step", "rlc_series_step", "C0"]
