"""Translate -> run -> parse -> check -> record, for one simulator or all of them (cross-check)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from . import checks, record, run as runmod
from .reference import solve_sparam
from .spec import analysis as pick, normalize, nodes as node_map
from .translate import Unsupported, backends

SIMS = ["ngspice", "xyce", "qucsator", "circuitrf"]


def translate(spec, sim: str, which=None) -> dict:
    s = normalize(spec)
    a = pick(s, which)
    b = backends()[sim]
    job = b.build(s, a)
    return {"sim": sim, "analysis": a["type"], "files": job.files, "copies": job.copies, "commands": job.commands,
            "notes": job.notes}


def write_outputs(res: dict, out_base: Path) -> list[Path]:
    """sparam -> Touchstone (v2 with [Reference] when port z0 differ) + CSV; tran -> CSV; hb/dc -> JSON."""
    out_base = Path(out_base)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    files = []
    if res["kind"] == "sparam":
        n = res["s"].shape[1]
        z0 = np.asarray(res["z0"], float)
        p = out_base.with_suffix(f".s{n}p")
        p.write_text(touchstone_text(res["f"], res["s"], z0))
        files.append(p)
        c = out_base.with_suffix(".csv")
        cols = ["f_hz"] + [f"{q}S{i + 1}{j + 1}" for i in range(n) for j in range(n) for q in ("re_", "im_")]
        rows = [[res["f"][k]] + [x for i in range(n) for j in range(n)
                                 for x in (res["s"][k, i, j].real, res["s"][k, i, j].imag)] for k in range(len(res["f"]))]
        c.write_text("# z0 = " + " ".join(f"{z:g}" for z in z0) + "\n" + ",".join(cols) + "\n"
                     + "\n".join(",".join(f"{x:.12g}" for x in r) for r in rows) + "\n")
        files.append(c)
    elif res["kind"] == "tran":
        c = out_base.with_suffix(".csv")
        names = list(res["v"])
        c.write_text(",".join(["t_s"] + [f"v({n})" for n in names]) + "\n" + "\n".join(
            ",".join(f"{x:.12g}" for x in [res["t"][k]] + [res["v"][n][k] for n in names]) for k in range(len(res["t"])))
            + "\n")
        files.append(c)
    else:
        j = out_base.with_suffix(".json")
        j.write_text(json.dumps(jsonable(res), indent=1))
        files.append(j)
    return files


def touchstone_text(f, S, z0, comment: str = "written by circuit-agent-tools") -> str:
    """Touchstone RI in Hz. Uniform z0 -> version 1.1; per-port z0 -> version 2.0 with [Reference], so the data
    keep their own references (no silent renormalisation). N = 2 data order is 21_12 in both."""
    n = S.shape[1]
    z0 = np.broadcast_to(np.asarray(z0, float), (n,))
    uniform = bool(np.allclose(z0, z0[0]))
    lines = [f"! {comment}"]
    if not uniform:
        lines += ["[Version] 2.0", f"# Hz S RI R {z0[0]:.12g}", f"[Number of Ports] {n}"]
        if n == 2:
            lines.append("[Two-Port Data Order] 21_12")
        lines += [f"[Number of Frequencies] {len(f)}", "[Reference] " + " ".join(f"{z:.12g}" for z in z0),
                  "[Network Data]"]
    else:
        lines.append(f"# Hz S RI R {z0[0]:.12g}")
    for k in range(len(f)):
        if n == 2:
            vals = [S[k, 0, 0], S[k, 1, 0], S[k, 0, 1], S[k, 1, 1]]
            lines.append(f"{f[k]:.12g} " + " ".join(f"{v.real:.12g} {v.imag:.12g}" for v in vals))
        else:
            for i in range(n):
                row = " ".join(f"{S[k, i, j].real:.12g} {S[k, i, j].imag:.12g}" for j in range(n))
                lines.append((f"{f[k]:.12g} " if i == 0 else "  ") + row)
    if not uniform:
        lines.append("[End]")
    return "\n".join(lines) + "\n"


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, complex):
        return {"re": o.real, "im": o.imag}
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o


def summary(res: dict) -> dict:
    """Small JSON view of a result for agents (full data is in the written files)."""
    if res["kind"] == "sparam":
        s = res["s"]
        out = {"points": len(res["f"]), "f_hz": [float(res["f"][0]), float(res["f"][-1])], "ports": s.shape[1],
               "z0": [float(z) for z in res["z0"]]}
        with np.errstate(divide="ignore"):
            db = 20 * np.log10(np.abs(s) + 1e-30)
        out["db_min_max"] = {f"S{i + 1}{j + 1}": [round(float(db[:, i, j].min()), 3), round(float(db[:, i, j].max()), 3)]
                             for i in range(s.shape[1]) for j in range(s.shape[2])}
        return out
    if res["kind"] == "dc":
        return {"v": res["v"]}
    if res["kind"] == "tran":
        return {"points": len(res["t"]), "t_s": [float(res["t"][0]), float(res["t"][-1])],
                "final_v": {n: float(v[-1]) for n, v in res["v"].items()},
                "peak_v": {n: float(np.max(v)) for n, v in res["v"].items()}}
    return {"f0": res["f0"], "method": res.get("method"), "converged": res.get("converged", True),
            "dbv": {n: [round(float(x), 3) for x in 20 * np.log10(np.abs(v) + 1e-30)] for n, v in res["v"].items()}}


def simulate(spec, sim: str, case_dir, which=None, timeout_s: float = 300, exe: str | None = None,
             do_record: bool = True, project: str = "circuit-agent-tools", tags=None, check_host: bool = True,
             force: bool = False, parent_run: str | None = None) -> dict:
    """Run one analysis of the spec in one simulator ('scikit-rf' = the reference solver)."""
    s = normalize(spec)
    a = pick(s, which)
    case_dir = Path(case_dir) / sim
    case_dir.mkdir(parents=True, exist_ok=True)
    out = {"sim": sim, "analysis": a["type"], "case_dir": str(case_dir)}
    t0 = time.time()
    out = _simulate(s, a, sim, case_dir, out, timeout_s, exe, check_host, force)
    out["wall_s"] = round(time.time() - t0, 3)
    if do_record and not out.get("unsupported") and not out.get("refused"):
        files = [Path(p) for p in out.get("files", [])]
        inputs = sorted(p for p in case_dir.iterdir() if p.suffix in (".cir", ".net", ".cnl", ".ctech")
                        or (p.suffix.startswith(".s") and p.suffix.endswith("p") and p not in files))
        mets = {"wall_s": out["wall_s"], "sim_wall_s": (out.get("run") or {}).get("wall_s")}
        res = out.get("result")
        if res and res["kind"] == "sparam":
            mets.update({"points": len(res["f"]), "ports": int(res["s"].shape[1])})
            for c in out.get("checks", []):
                mets[c["name"]] = c["value"]
        elif res:
            mets.update({k: v for k, v in flat(summary(res)).items() if isinstance(v, (int, float, str))})
        if sim == "scikit-rf":
            import skrf

            tool = {"name": "scikit-rf", "version": skrf.__version__}
        else:
            tool = {"name": sim, "version": _ver_cache.get(sim, "")}
        out["record"] = record.save_run(project, s["name"], tool, "ok" if out.get("ok") else "failed",
                                        {"analysis": jsonable(a), "sim": sim}, inputs, files, mets,
                                        out.get("checks", []), out["wall_s"],
                                        list(tags or []) + [sim, a["type"]], notes=out.get("reason") or "",
                                        parent_run=parent_run)
    return out


def flat(d: dict, prefix: str = "") -> dict:
    o = {}
    for k, v in d.items():
        if isinstance(v, dict):
            o.update(flat(v, f"{prefix}{k}."))
        elif isinstance(v, list) and len(v) <= 40 and all(isinstance(x, (int, float)) for x in v):
            o.update({f"{prefix}{k}.{i}": x for i, x in enumerate(v)})
        else:
            o[f"{prefix}{k}"] = v
    return o


def _simulate(s, a, sim, case_dir, out, timeout_s, exe, check_host, force) -> dict:
    if sim == "scikit-rf":
        if a["type"] != "sparam":
            return dict(out, ok=False, unsupported=True, reason="scikit-rf reference covers S-parameters only")
        try:
            res = solve_sparam(s, a)
        except Unsupported as e:
            return dict(out, ok=False, unsupported=True, reason=str(e))
        out["run"] = {"ok": True, "wall_s": res["wall_s"], "exe": "python"}
    else:
        if check_host and not force:
            g = runmod.guard(runmod.host_status(scratch=case_dir))
            out["guard"] = {k: g[k] for k in ("ok", "reasons", "advice")}
            if not g["ok"]:
                return dict(out, ok=False, refused=True, reason="; ".join(g["reasons"]) + " (force=true overrides)")
        b = backends()[sim]
        try:
            job = b.build(s, a)
        except Unsupported as e:
            return dict(out, ok=False, unsupported=True, reason=str(e))
        r = runmod.run_job(job, case_dir, exe=exe, timeout_s=timeout_s)
        out["run"] = r
        out["notes"] = job.notes
        if r["exit_code"] != 0:
            return dict(out, ok=False, reason=f"{sim} exit code {r['exit_code']}; see run.logs")
        try:
            res = b.parse(case_dir, s, a, job)
        except Exception as e:  # noqa: BLE001  parsing failure is a failed run, reported to the agent
            return dict(out, ok=False, reason=f"could not parse {sim} output: {type(e).__name__}: {e}")
    out["result"] = res
    files = write_outputs(res, case_dir / f"{s['name']}_{a['type']}")
    out["files"] = [str(p) for p in files]
    out["summary"] = summary(res)
    cks = []
    if res["kind"] == "sparam":
        cks = [checks.passivity(res["s"], res["f"]), checks.reciprocity(res["s"], res["f"])]
    if res["kind"] == "hb" and res.get("converged") is False:
        cks.append({"name": "hb_converged", "status": "fail", "value": 0, "detail": f"{sim} HB did not converge"})
    if sim != "scikit-rf" and (out.get("run") or {}).get("errors"):
        errs = [e for e in out["run"]["errors"] if e["severity"] == "error"]
        if errs:
            cks.append({"name": "log_errors", "status": "warn", "value": len(errs), "detail": errs[0]["line"]})
    out["checks"] = cks
    out["ok"] = True
    return out


_ver_cache: dict = {}


def crosscheck(spec, case_dir, which=None, sims=None, reference: str | None = None, tol: dict | None = None,
               timeout_s: float = 300, do_record: bool = True, exes: dict | None = None) -> dict:
    """Run the same analysis in every simulator that supports it; compare each to the reference.

    Reference: scikit-rf for S-parameters, else the first successful simulator unless ``reference`` names one.
    tol: {"s": 1e-3, "tran_rel": 1e-2, "hb_db": 0.1, "dc_v": 1e-4}
    """
    s = normalize(spec)
    a = pick(s, which)
    tol = {"s": 1e-3, "tran_rel": 1e-2, "hb_db": 0.1, "dc_v": 1e-4, **(tol or {})}
    sims = list(sims or (["scikit-rf"] if a["type"] == "sparam" else []) + SIMS)
    g = runmod.guard(runmod.host_status(scratch=Path(case_dir)), n_runs=len(sims))
    if not g["ok"]:
        return {"ok": False, "refused": True, "reasons": g["reasons"]}
    if not _ver_cache:
        try:
            for k, v in runmod.versions(exes=exes).items():
                _ver_cache[k] = v.get("version") or ""
        except Exception:  # noqa: BLE001
            pass
    import uuid

    parent = str(uuid.uuid4())
    runs = {}
    for sim in sims:
        runs[sim] = simulate(s, sim, case_dir, which, timeout_s=timeout_s, do_record=do_record,
                             exe=(exes or {}).get(sim), check_host=False, parent_run=parent)
    ok = {k: v for k, v in runs.items() if v.get("ok")}
    ref_name = reference or ("scikit-rf" if a["type"] == "sparam" and "scikit-rf" in ok else next(iter(ok), None))
    table = []
    for sim, r in runs.items():
        row = {"sim": sim, "status": "ok" if r.get("ok") else ("unsupported" if r.get("unsupported") else "failed"),
               "wall_s": r.get("wall_s") or (r.get("run") or {}).get("wall_s"),
               "reason": r.get("reason"), "version": _ver_cache.get(sim, "scikit-rf" if sim == "scikit-rf" else "")}
        if r.get("ok") and ref_name and sim != ref_name:
            res, ref = r["result"], ok[ref_name]["result"]
            if a["type"] == "sparam":
                c = checks.compare_s(res, ref, tol["s"])
            elif a["type"] == "tran":
                c = checks.compare_waveforms(res, ref, tol["tran_rel"])
            elif a["type"] == "hb":
                c = checks.compare_harmonics(res, ref, tol_db=tol["hb_db"])
            else:
                c = checks.compare_dc(res, ref, tol["dc_v"])
            row["vs_reference"] = c
        if r.get("ok"):
            row["checks"] = r.get("checks")
            row["files"] = r.get("files")
            row["summary"] = r.get("summary")
        table.append(row)
    status = [row.get("vs_reference", {}).get("status") for row in table if row.get("vs_reference")]
    res = {"ok": bool(ok) and "fail" not in status, "analysis": a["type"], "reference": ref_name,
           "compared": len(status), "results": table, "guard_advice": g["advice"],
           "overall": "fail" if "fail" in status else ("pass" if status else "skip")}
    if do_record:
        from . import __version__

        cks = [dict(row["vs_reference"], name=f"{row['sim']}_vs_{ref_name}") for row in table if row.get("vs_reference")]
        mets = {f"{row['sim']}.{row['vs_reference']['name']}": row["vs_reference"]["value"] for row in table
                if row.get("vs_reference")}
        mets.update({f"{row['sim']}.status": row["status"] for row in table})
        res["record"] = record.save_run("circuit-agent-tools", s["name"],
                                        {"name": "circuit-agent-tools.crosscheck", "version": __version__}, "ok",
                                        {"analysis": jsonable(a), "sims": sims, "reference": ref_name, "tol": tol}, [],
                                        [], mets, cks, None, ["crosscheck", a["type"]], run_id=parent)
    return res


def topology(spec) -> dict:
    s = normalize(spec)
    return {n: v for n, v in node_map(s).items()}
