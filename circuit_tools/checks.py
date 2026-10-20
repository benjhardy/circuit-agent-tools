"""Result checks and comparisons. Each check returns {"name", "status": pass|warn|fail|skip, "value", "detail"}."""
from __future__ import annotations

import numpy as np


def _res(name, status, value=None, detail="", **kw):
    d = {"name": name, "status": status, "value": value, "detail": detail}
    d.update(kw)
    return d


def passivity(s, f=None, tol: float = 1e-6, name: str = "passivity") -> dict:
    """Largest eigenvalue of S^H S over frequency must be <= 1 (+tol); > 1 means the data creates energy."""
    s = np.asarray(s)
    ev = np.array([np.linalg.eigvalsh(m.conj().T @ m).max() for m in s])
    k = int(ev.argmax())
    st = "pass" if ev[k] <= 1 + tol else ("warn" if ev[k] <= 1 + 1e-3 else "fail")
    return _res(name, st, float(ev[k]), "max eigenvalue of S^H S (<= 1 for a passive network)",
                at_hz=None if f is None else float(np.asarray(f)[k]),
                n_violating=int(np.sum(ev > 1 + tol)))


def reciprocity(s, f=None, tol: float = 1e-6, name: str = "reciprocity") -> dict:
    s = np.asarray(s)
    if s.shape[1] < 2:
        return _res(name, "skip", detail="1-port")
    d = np.abs(s - np.transpose(s, (0, 2, 1)))
    worst = float(d.max())
    k = int(np.argmax(d.max(axis=(1, 2))))
    st = "pass" if worst <= tol else ("warn" if worst <= 1e-3 else "fail")
    return _res(name, st, worst, "max |S_ij - S_ji| (passive circuits without ferrites/active parts are reciprocal)",
                at_hz=None if f is None else float(np.asarray(f)[k]))


def _resample(f_src, s_src, f_dst):
    if len(f_src) == len(f_dst) and np.allclose(f_src, f_dst, rtol=1e-9):
        return np.asarray(s_src), False
    out = np.empty((len(f_dst),) + np.asarray(s_src).shape[1:], complex)
    for i in range(out.shape[1]):
        for j in range(out.shape[2]):
            out[:, i, j] = (np.interp(f_dst, f_src, np.real(s_src[:, i, j]))
                            + 1j * np.interp(f_dst, f_src, np.imag(s_src[:, i, j])))
    return out, True


def compare_s(res: dict, ref: dict, tol: float = 1e-3, name: str = "max_dS") -> dict:
    """max |S - S_ref| over every entry and frequency, with where it happens (and dB of |S21|-type entries)."""
    s, interp = _resample(res["f"], res["s"], ref["f"])
    sr = np.asarray(ref["s"])
    if s.shape != sr.shape:
        return _res(name, "fail", None, f"shape {s.shape} vs reference {sr.shape}")
    d = np.abs(s - sr)
    k, i, j = np.unravel_index(int(np.argmax(d)), d.shape)
    worst = float(d[k, i, j])
    with np.errstate(divide="ignore"):
        db = 20 * np.log10(np.abs(s) + 1e-30) - 20 * np.log10(np.abs(sr) + 1e-30)
    m = 20 * np.log10(np.abs(sr) + 1e-30) > -40
    return _res(name, "pass" if worst <= tol else "fail", worst, f"max |S - S_ref| (tolerance {tol:g})",
                at_hz=float(ref["f"][k]), entry=f"S{i + 1}{j + 1}", interpolated=interp,
                max_ddb_above_m40db=float(np.max(np.abs(db[m]))) if m.any() else None,
                per_entry={f"S{a + 1}{b + 1}": float(d[:, a, b].max()) for a in range(d.shape[1])
                           for b in range(d.shape[2])})


def compare_waveforms(res: dict, ref: dict, tol_rel: float = 1e-2, name: str = "max_dv") -> dict:
    """max |v - v_ref| per output node after interpolating onto the reference time grid, also relative to the
    reference's peak-to-peak swing."""
    out, worst_rel = {}, 0.0
    for n, vr in ref["v"].items():
        if n not in res["v"]:
            continue
        v = np.interp(ref["t"], res["t"], res["v"][n])
        dv = np.abs(v - np.asarray(vr))
        swing = float(np.ptp(vr)) or 1.0
        k = int(np.argmax(dv))
        out[n] = {"max_dv": float(dv[k]), "at_s": float(ref["t"][k]), "rel_to_swing": float(dv[k] / swing),
                  "rms_dv": float(np.sqrt(np.mean(dv ** 2)))}
        worst_rel = max(worst_rel, dv[k] / swing)
    if not out:
        return _res(name, "skip", detail="no common output nodes")
    return _res(name, "pass" if worst_rel <= tol_rel else "fail", float(worst_rel),
                f"max |v - v_ref| / swing (tolerance {tol_rel:g})", nodes=out)


def compare_harmonics(res: dict, ref: dict, floor_db: float = -60.0, tol_db: float = 0.1,
                      name: str = "max_d_harmonic_db") -> dict:
    """Harmonic magnitudes in dB (re 1 V peak): worst difference over harmonics above floor_db in the reference."""
    out, worst = {}, 0.0
    for n, vr in ref["v"].items():
        if n not in res["v"]:
            continue
        a = 20 * np.log10(np.abs(np.asarray(res["v"][n])) + 1e-30)
        b = 20 * np.log10(np.abs(np.asarray(vr)) + 1e-30)
        k = min(len(a), len(b))
        m = b[:k] > floor_db
        d = np.abs(a[:k] - b[:k])
        r0, x0 = float(np.real(np.asarray(vr)[0])), float(np.real(np.asarray(res["v"][n])[0]))
        flip = abs(r0) > 1e-3 and abs(x0) > 1e-3 and np.sign(r0) != np.sign(x0)
        if flip:
            worst = max(worst, 1e9)  # magnitudes hide a polarity flip (e.g. a reversed diode): fail it explicitly
        out[n] = {"ref_dbv": [round(float(x), 3) for x in b[:k]], "dbv": [round(float(x), 3) for x in a[:k]],
                  "dc_sign_flipped": bool(flip), "dc_v": x0, "ref_dc_v": r0,
                  "max_ddb": float(d[m].max()) if m.any() else None,
                  "worst_harmonic": int(np.arange(k)[m][np.argmax(d[m])]) if m.any() else None}
        if m.any():
            worst = max(worst, float(d[m].max()))
    if not out:
        return _res(name, "skip", detail="no common output nodes")
    flipped = [n for n, x in out.items() if x["dc_sign_flipped"]]
    if flipped:
        worst = max([x["max_ddb"] or 0 for x in out.values()])
    return _res(name, "fail" if flipped or worst > tol_db else "pass", worst,
                f"max |dB difference| over harmonics above {floor_db:g} dBV (tolerance {tol_db:g} dB)"
                + (f"; DC term has the opposite sign at {flipped} (polarity flip)" if flipped else ""), nodes=out)


def compare_dc(res: dict, ref: dict, tol: float = 1e-4, name: str = "max_dV_dc") -> dict:
    common = [n for n in ref["v"] if n in res["v"]]
    if not common:
        return _res(name, "skip", detail="no common nodes")
    d = {n: abs(res["v"][n] - ref["v"][n]) for n in common}
    worst = max(d.values())
    return _res(name, "pass" if worst <= tol else "fail", worst, f"max |V - V_ref| (tolerance {tol:g} V)", nodes=d)


def overall(checks: list[dict]) -> str:
    st = [c["status"] for c in checks]
    return "fail" if "fail" in st else ("warn" if "warn" in st else "pass")
