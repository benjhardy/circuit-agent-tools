"""Microstrip helpers built on scikit-rf's MLine (Hammerstad-Jensen + Kirschning-Jansen), plus model limits.

scikit-rf defaults that differ from the circuit simulators, set explicitly here so all tools model the same line:
``diel='frequencyinvariant'`` (scikit-rf's default is a wideband Djordjevic-Svensson Debye model fitted at 1 GHz,
while qucsator and circuitRF use a constant er and tan d) and ``rough=0`` (scikit-rf's default is 0.15 um rms).
"""
from __future__ import annotations

import math

import numpy as np

C0 = 299792458.0


def media(f_hz, w: float, sub: dict, z0_port: float | None = 50.0):
    import skrf as rf
    from skrf.media import MLine

    freq = rf.Frequency.from_f(np.atleast_1d(np.asarray(f_hz, float)), unit="hz")
    return MLine(frequency=freq, z0_port=z0_port, w=w, h=sub["h"], t=sub.get("t") or None, ep_r=sub["er"],
                 tand=sub.get("tand", 0.0), rho=1.0 / sub.get("sigma", 5.8e7), rough=sub.get("rough", 0.0),
                 diel="frequencyinvariant", model="hammerstadjensen", disp="kirschningjansen")


def line_props(f_hz, w: float, sub: dict) -> dict:
    """Z0 (complex), eeff, alpha (Np/m: conductor, dielectric) and beta over f."""
    m = media(f_hz, w, sub, None)
    return {"z0": np.asarray(m.z0_characteristic), "eeff": np.real(np.asarray(m.ep_reff_f)), "gamma": np.asarray(m.gamma),
            "alpha_c": np.asarray(m.alpha_conductor), "alpha_d": np.asarray(m.alpha_dielectric)}


def rlgc(f_ref: float, w: float, sub: dict) -> dict:
    """Per-unit-length R, L, G, C matching Z0 and gamma at f_ref (for SPICE lossy lines).

    Frozen at f_ref: SPICE's constant-RLGC line has no dispersion, R does not grow as sqrt(f) and G is not
    proportional to f, so the line is exact only at f_ref."""
    p = line_props([f_ref], w, sub)
    z, g = complex(p["z0"][0]), complex(p["gamma"][0])
    om = 2 * math.pi * f_ref
    a, b = g * z, g / z
    return {"R": max(a.real, 0.0), "L": a.imag / om, "G": max(b.real, 0.0), "C": b.imag / om, "f_ref": f_ref,
            "z0": z, "eeff": float(p["eeff"][0])}


def limits(w: float, sub: dict, f_max: float) -> list[dict]:
    """Where the closed-form models stop being trustworthy, as findings {level, message}."""
    out = []
    h, er = sub["h"], sub["er"]
    u = w / h
    if not 0.01 <= u <= 100:
        out.append({"level": "warning", "message": f"w/h = {u:.3g} outside Hammerstad-Jensen range 0.01-100"})
    if er > 128:
        out.append({"level": "warning", "message": f"er = {er:g} > 128 (Hammerstad-Jensen)"})
    if not 0.1 <= u <= 100 or not 1 <= er <= 20:
        out.append({"level": "warning", "message": f"w/h = {u:.3g}, er = {er:g}: outside the Kirschning-Jansen "
                                                   "dispersion fit (0.1 <= w/h <= 100, 1 <= er <= 20)"})
    fh = f_max * h * 1e-6  # GHz * mm
    if fh > 0.13 * C0 * 1e-6:
        out.append({"level": "warning", "message": f"f*h = {fh:.3g} GHz*mm > {0.13 * C0 * 1e-6:.0f} GHz*mm "
                                                   "(h/lambda0 > 0.13): beyond the Kirschning-Jansen dispersion fit"})
    if er > 1:
        f_sw = C0 * math.atan(er) / (math.sqrt(2) * math.pi * h * math.sqrt(er - 1))
        if f_max > 0.5 * f_sw:
            out.append({"level": "warning", "message": f"f_max {f_max / 1e9:.3g} GHz > half the TM1 surface-wave "
                                                       f"onset {f_sw / 1e9:.3g} GHz: radiation/coupling not modelled"})
    f_tr = C0 / (math.sqrt(er) * (2 * w + 0.8 * h))
    if f_max > 0.7 * f_tr:
        out.append({"level": "warning", "message": f"f_max {f_max / 1e9:.3g} GHz near the first transverse "
                                                   f"resonance {f_tr / 1e9:.3g} GHz of a {w * 1e3:.3g} mm strip"})
    if sub.get("t", 0) and sub["t"] > 0.2 * w:
        out.append({"level": "info", "message": "thick strip (t > 0.2 w): the thickness correction is approximate"})
    return out


def coupled_limits(w: float, s: float, sub: dict) -> list[dict]:
    """Fit range of the Kirschning-Jansen coupled-microstrip equations (static and dispersion)."""
    out = []
    u, g, er = w / sub["h"], s / sub["h"], sub["er"]
    if not 0.1 <= u <= 10:
        out.append({"level": "warning", "message": f"w/h = {u:.3g} outside the Kirschning-Jansen coupled-line fit "
                                                   "0.1-10"})
    if not 0.1 <= g <= 10:
        out.append({"level": "warning", "message": f"s/h = {g:.3g} outside the Kirschning-Jansen coupled-line fit "
                                                   "0.1-10"})
    if not 1 <= er <= 18:
        out.append({"level": "warning", "message": f"er = {er:g} outside the Kirschning-Jansen coupled-line fit 1-18"})
    return out


def open_end_limits(w: float, sub: dict) -> list[dict]:
    """Fit range of the Kirschning-Jansen-Koster open-end model."""
    u, er = w / sub["h"], sub["er"]
    if not 0.01 <= u <= 100 or not 1 <= er <= 50:
        return [{"level": "warning", "message": f"w/h = {u:.3g}, er = {er:g}: outside the open-end fit "
                                                "(0.01 <= w/h <= 100, 1 <= er <= 50)"}]
    return []


def step_limits(w1: float, w2: float, sub: dict) -> list[dict]:
    """Fit range of the Gupta-Garg width-step C and L (qucsator MSTEP), for the wide/narrow width ratio."""
    r = max(w1, w2) / min(w1, w2)
    out = []
    if sub["er"] > 10:
        out.append({"level": "warning", "message": f"er = {sub['er']:g} > 10: outside the step capacitance fit"})
    if r > 5:
        out.append({"level": "warning", "message": f"width ratio {r:.3g} > 5: outside the step inductance fit"})
    elif r < 1.5:
        t = math.log10(sub["er"])
        r0 = (12.6 * t + 3.17) / (10.1 * t + 2.33)    # ratio where the capacitance fit crosses zero
        out.append({"level": "info", "message": f"width ratio {r:.3g} < 1.5: below the step capacitance fit "
                                                f"(1.5-3.5){'; the fitted C is negative here' if r < r0 else ''}. "
                                                "The step is small at such ratios"})
    return out
