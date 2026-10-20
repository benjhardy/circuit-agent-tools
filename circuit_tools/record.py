"""Record each simulator run as an ``rflab.run/1`` record (rflab-core).

With rflab-core installed (``pip install -e <rflab-core>``, or RFLAB_CORE pointing at its checkout) the record goes
through ``rflab.record()``, which validates it and stores it in %USERPROFILE%/.rflab (or RFLAB_HOME). Without it,
the same schema-valid record is appended as one JSON line to %USERPROFILE%/.rflab/pending/circuit-agent-tools.jsonl,
rflab-core's drop-box. Disable recording with CIRCUIT_TOOLS_RECORD=0.

Schema notes (rflab.run/1): run_id is a uuid4; status is queued|running|ok|failed|cancelled ("ok" = the run
completed, not that it passed); outputs.metrics is flat (name -> number or string); acceptance results go in
checks[] as {name, passed, value, limit, note}.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

PENDING = Path(os.environ.get("USERPROFILE") or Path.home()) / ".rflab" / "pending" / "circuit-agent-tools.jsonl"


def _rflab():
    if os.environ.get("RFLAB_CORE") and os.environ["RFLAB_CORE"] not in sys.path:
        sys.path.insert(0, os.environ["RFLAB_CORE"])
    try:
        import rflab

        return rflab if hasattr(rflab, "record") else None
    except Exception:  # noqa: BLE001  any import problem -> fallback
        return None


def rflab_checks(cks: list[dict]) -> list[dict]:
    """Our {name, status pass|warn|fail|skip, value, detail} -> rflab's {name, passed, value, limit, note}."""
    out = []
    for c in cks:
        if c.get("status") == "skip":
            continue
        v = c.get("value")
        v = v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
        out.append({"name": str(c.get("name")), "passed": c.get("status") in ("pass", "warn"), "value": v,
                    "limit": c.get("limit"), "note": (c.get("status") + ": " if c.get("status") == "warn" else "")
                    + str(c.get("detail", ""))[:300]})
    return out


def flat_metrics(m: dict, prefix: str = "") -> dict:
    """Flatten to name -> number|string (rflab metrics are flat)."""
    out = {}
    for k, v in (m or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flat_metrics(v, key + "."))
        elif isinstance(v, bool):
            out[key] = int(v)
        elif isinstance(v, (int, float)):
            out[key] = v if math.isfinite(v) else str(v)
        elif isinstance(v, str):
            out[key] = v
        elif v is not None and hasattr(v, "item"):
            out[key] = float(v.item())
    return out


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _git(path: Path) -> dict:
    try:
        root = Path(path).resolve()
        run = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, timeout=10)  # noqa: E731
        c = run("rev-parse", "HEAD")
        if c.returncode:
            return {"repo": None, "commit": None, "dirty": None}
        return {"repo": run("rev-parse", "--show-toplevel").stdout.strip(), "commit": c.stdout.strip(),
                "dirty": bool(run("status", "--porcelain").stdout.strip())}
    except (OSError, subprocess.TimeoutExpired):
        return {"repo": None, "commit": None, "dirty": None}


def save_run(project: str, case: str, tool: dict, status: str, params: dict, in_files: list, out_files: list,
             metrics: dict, checks: list, wall_s: float | None, tags: list, notes: str = "",
             parent_run: str | None = None, run_id: str | None = None) -> dict:
    """Record one run. Returns {"via": "rflab" | "pending-jsonl" | "disabled", "run_id", ...}."""
    if os.environ.get("CIRCUIT_TOOLS_RECORD", "1") == "0":
        return {"via": "disabled"}
    status = status if status in ("queued", "running", "ok", "failed", "cancelled") else "failed"
    cks = rflab_checks(checks)
    mets = flat_metrics(metrics)
    notes = notes or f"circuit-agent-tools {__version__} on {platform.system()}"
    compute = {"host": socket.gethostname(), "cores": 1, "ranks": 1, "wall_s": wall_s, "peak_ram_gb": None,
               "gpu": None}
    git_dir = Path(__file__).resolve().parents[1]
    rl = _rflab()
    if rl is not None:
        try:
            rec = rl.record(project, case, tool, "circuit", status=status, params=params,
                            files=[str(p) for p in in_files], metrics=mets, outputs=[str(p) for p in out_files],
                            checks=cks, compute=compute, tags=tags, parent_run=parent_run, notes=notes,
                            git=str(git_dir), **({"run_id": run_id} if run_id else {}))
            return {"via": "rflab", "run_id": rec["run_id"]}
        except Exception as e:  # noqa: BLE001  fall back rather than lose the record
            notes += f" (rflab.record failed: {type(e).__name__}: {e})"
    files = [{"path": str(Path(p).resolve()), "sha256": _sha(p)} for p in in_files]
    canon = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    pairs = "".join(f"{Path(f['path']).name}:{f['sha256']}" for f in sorted(files, key=lambda f: Path(f["path"]).name))
    rec = {"schema": "rflab.run/1", "run_id": run_id or str(uuid.uuid4()),
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
           "project": project, "case": case, "tool": tool, "kind": "circuit", "status": status,
           "parent_run": parent_run or os.environ.get("RFLAB_PARENT_RUN"), "tags": list(tags),
           "inputs": {"files": files, "params": params, "hash": hashlib.sha256((canon + pairs).encode()).hexdigest()},
           "mesh": {"elements": None, "unknowns": None, "order": None}, "compute": compute,
           "outputs": {"files": [{"path": str(Path(p).resolve()), "sha256": _sha(p)} for p in out_files],
                       "metrics": mets},
           "checks": cks, "git": _git(git_dir), "notes": notes}
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    with PENDING.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return {"via": "pending-jsonl", "run_id": rec["run_id"], "path": str(PENDING)}
