"""Run simulator jobs (WSL on Windows, or native bash) with a host guard, timeouts and log parsing.

Executables (arguments override environment variables, which override the defaults):
  CIRCUIT_TOOLS_NGSPICE, CIRCUIT_TOOLS_XYCE, CIRCUIT_TOOLS_QUCSATOR, CIRCUIT_TOOLS_CIRCUITRF
  defaults: ~/micromamba/envs/circuit-sim/bin/{ngspice,Xyce}, ~/.local/opt/qucsator_rf/bin/qucsator_rf,
            ~/.local/bin/circuitrf, then whatever is on PATH.
  CIRCUIT_TOOLS_BACKEND  wsl | native (default: wsl on Windows)    CIRCUIT_TOOLS_DISTRO  WSL distribution
Every job runs from a generated ``run.sh`` (so wsl.exe never re-parses a ``$`` or a loop), at ``nice 10`` with
OMP_NUM_THREADS=1 and a ``timeout``. Circuit runs are small; the guard exists so a cross-check never competes
with a big EM job for the last gigabyte of RAM.
"""
from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from .translate import Job

DEFAULT_EXE = {
    "ngspice": ("CIRCUIT_TOOLS_NGSPICE", "$HOME/micromamba/envs/circuit-sim/bin/ngspice", "ngspice"),
    "xyce": ("CIRCUIT_TOOLS_XYCE", "$HOME/micromamba/envs/circuit-sim/bin/Xyce", "Xyce"),
    "qucsator": ("CIRCUIT_TOOLS_QUCSATOR", "$HOME/.local/opt/qucsator_rf/bin/qucsator_rf", "qucsator_rf"),
    "circuitrf": ("CIRCUIT_TOOLS_CIRCUITRF", "$HOME/.local/bin/circuitrf", "circuitrf"),
}
VERSION_CMD = {"ngspice": "echo quit | \"$EXE\" -v 2>&1 | grep -m1 -i ngspice", "xyce": "\"$EXE\" -v 2>&1 | head -1",
               "qucsator": "\"$EXE\" -v 2>&1 | head -1", "circuitrf": "timeout 60 \"$EXE\" --version 2>&1 | head -1"}


def backend(b: str | None = None) -> str:
    b = b or os.environ.get("CIRCUIT_TOOLS_BACKEND")
    if b:
        return b
    return "wsl" if platform.system() == "Windows" else "native"


def wslpath(p: Path) -> str:
    p = Path(p).resolve()
    drive = p.drive.rstrip(":").lower()
    return f"/mnt/{drive}" + p.as_posix()[len(p.drive):]


def _exe_lines(sim: str, exe: str | None) -> list[str]:
    env, default, name = DEFAULT_EXE[sim]
    want = exe or os.environ.get(env) or default
    return [f'EXE="{want}"', f'[ -x "$EXE" ] || EXE="$(command -v {name})"',
            f'[ -n "$EXE" ] || {{ echo "{name} not found" >&2; exit 127; }}']


def _bash(script: Path, be: str, distro: str | None, timeout: float) -> subprocess.CompletedProcess:
    if be == "wsl":
        args = ["wsl.exe"] + (["-d", distro] if distro else []) + ["--", "bash", wslpath(script)]
    else:
        args = ["bash", str(script)]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def write_script(path: Path, lines: list[str]) -> Path:
    path.write_bytes(("\n".join(["#!/bin/bash", 'cd "$(dirname "$0")"'] + lines) + "\n").encode("utf-8"))
    return path


def host_status(be: str | None = None, distro: str | None = None, scratch: Path | None = None) -> dict:
    """CPUs, load average, available RAM and running Palace/MEEP/Elmer ranks where the simulators run."""
    be = backend(be)
    distro = distro or os.environ.get("CIRCUIT_TOOLS_DISTRO")
    d = Path(scratch or (Path.home() / ".circuit-agent-tools"))
    d.mkdir(parents=True, exist_ok=True)
    sc = write_script(d / "probe.sh", ["nproc", "cut -d' ' -f1-3 /proc/loadavg",
                                       "grep -E '^MemAvailable|^MemTotal' /proc/meminfo | awk '{print $2}'",
                                       "pgrep -fc 'palace-[A-Za-z0-9_]+\\.bin|meep|ElmerSolver' || true"])
    try:
        r = _bash(sc, be, distro, 60)
        x = r.stdout.split()
        cpus, l1, l5, l15 = int(x[0]), float(x[1]), float(x[2]), float(x[3])
        tot, avail = int(x[4]) / 2 ** 20, int(x[5]) / 2 ** 20
        heavy = int(x[6]) if len(x) > 6 else 0
    except (subprocess.TimeoutExpired, FileNotFoundError, IndexError, ValueError) as e:
        return {"backend": be, "error": f"cannot reach the simulator host: {e}"}
    return {"backend": be, "distro": distro, "cpus": cpus, "load_1m": l1, "load_5m": l5, "load_15m": l15,
            "ram_total_gb": round(tot, 2), "ram_available_gb": round(avail, 2), "heavy_em_processes": heavy}


def guard(st: dict, est_mem_gb: float = 0.3, n_runs: int = 1, max_runs: int = 200) -> dict:
    """May circuit runs start now? Refuse on < 1 GB + estimate free RAM, unreachable host or too many runs."""
    reasons, advice = [], []
    if st.get("error"):
        return {"ok": False, "reasons": [st["error"]], "advice": [], "status": st}
    if st["ram_available_gb"] < 1.0 + est_mem_gb:
        reasons.append(f"only {st['ram_available_gb']:.1f} GB RAM available (need {1.0 + est_mem_gb:.1f} GB with "
                       "a 1 GB margin for the other jobs on this host)")
    if n_runs > max_runs:
        reasons.append(f"{n_runs} simulator runs requested (limit {max_runs}); coarsen the sweep or split the job")
    if st["load_1m"] > 1.5 * st["cpus"]:
        advice.append(f"host is busy (load {st['load_1m']:.1f} on {st['cpus']} CPUs): runs go at nice 10, one at a "
                      "time; expect longer wall times")
    if st.get("heavy_em_processes"):
        advice.append(f"{st['heavy_em_processes']} EM solver process(es) running: keep circuit runs sequential")
    return {"ok": not reasons, "reasons": reasons, "advice": advice, "status": st}


def run_job(job: Job, case_dir: Path, exe: str | None = None, timeout_s: float = 300, be: str | None = None,
            distro: str | None = None) -> dict:
    """Write job files into case_dir, run its commands, return {ok, exit_code, wall_s, log_tail, errors}."""
    case_dir = Path(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    for name, text in job.files.items():
        (case_dir / name).write_bytes(text.encode("utf-8"))
    for name, src in job.copies.items():
        if Path(src).resolve() != (case_dir / name).resolve():
            shutil.copyfile(src, case_dir / name)
    be = backend(be)
    distro = distro or os.environ.get("CIRCUIT_TOOLS_DISTRO")
    t = int(max(5, timeout_s))
    lines = ["export OMP_NUM_THREADS=1"] + _exe_lines(job.sim, exe) + ["rc=0"]
    for c in job.commands:
        lines.append(f"timeout {t} nice -n 10 {c.replace('{exe}', chr(34) + '$EXE' + chr(34))} || rc=$?")
    lines += ['echo "EXE=$EXE"', "exit $rc"]
    sc = write_script(case_dir / "run.sh", lines)
    t0 = time.time()
    try:
        r = _bash(sc, be, distro, timeout_s * max(1, len(job.commands)) + 60)
        code, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        code, out, err = None, "", "timed out"
    wall = round(time.time() - t0, 3)
    logs = sorted(case_dir.glob("*.log"))
    tail = []
    errors = []
    for lg in logs:
        txt = lg.read_text(encoding="utf-8", errors="replace")
        tail.append({"file": lg.name, "tail": txt.splitlines()[-8:]})
        errors += scan_log(txt, job.sim)
    exe_used = next((ln[4:] for ln in out.splitlines() if ln.startswith("EXE=")), None)
    return {"ok": code == 0 and not [e for e in errors if e["severity"] == "error"], "exit_code": code,
            "timed_out": code in (124, None), "wall_s": wall, "exe": exe_used, "errors": errors[:20],
            "stderr": err[-2000:], "logs": tail, "case_dir": str(case_dir)}


ERROR_PATTERNS = {
    "ngspice": [("error", r"(?i)\berror\b(?!s? *= *0)"), ("error", r"(?i)singular matrix"),
                ("error", r"(?i)timestep too small"), ("warning", r"(?i)warning")],
    "xyce": [("error", r"Netlist error|Xyce Abort|[1-9]\d* MSG_ERROR"), ("warning", r"(?i)warning")],
    "qucsator": [("error", r"(?i)no convergence|exception|error"), ("warning", r"(?i)warning")],
    "circuitrf": [("error", r"(?i)^error|refused|failed"), ("warning", r"(?i)\bwarning\b")],
}


def scan_log(text: str, sim: str) -> list[dict]:
    import re

    out = []
    for ln in text.splitlines():
        for sev, pat in ERROR_PATTERNS.get(sim, []):
            if re.search(pat, ln):
                out.append({"severity": sev, "line": ln.strip()[:300]})
                break
    return out


def versions(be: str | None = None, distro: str | None = None, exes: dict | None = None) -> dict:
    """Which simulators are installed where they run, with their version strings."""
    d = Path.home() / ".circuit-agent-tools"
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for sim in DEFAULT_EXE:
        lines += ["(" ] + _exe_lines(sim, (exes or {}).get(sim)) + [f'echo "@@{sim} $EXE"', VERSION_CMD[sim], ") 2>&1"]
    sc = write_script(d / "versions.sh", lines)
    try:
        r = _bash(sc, backend(be), distro or os.environ.get("CIRCUIT_TOOLS_DISTRO"), 240)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"error": str(e)}
    out, cur = {}, None
    for ln in r.stdout.splitlines():
        if ln.startswith("@@"):
            parts = ln[2:].split(maxsplit=1)
            cur = parts[0]
            out[cur] = {"path": parts[1] if len(parts) > 1 else None, "version": None, "available": len(parts) > 1}
        elif cur and ln.strip() and out[cur]["version"] is None:
            if "not found" in ln:
                out[cur]["available"] = False
            out[cur]["version"] = ln.strip().strip("*").strip()
    return out


__all__ = ["run_job", "host_status", "guard", "versions", "backend", "shlex"]


QUCS_S = ("CIRCUIT_TOOLS_QUCS_S", "$HOME/.local/opt/qucs-s/bin/qucs-s", "qucs-s")
QUCS_S_LIBS = "$HOME/micromamba/envs/qucs-build/lib"


def qucs_schematic_to_netlist(sch: Path, net: Path, flavour: str = "qucsator", exe: str | None = None,
                              be: str | None = None, distro: str | None = None, timeout_s: float = 120) -> dict:
    """Convert a Qucs-S schematic with Qucs-S itself, headless (Qt offscreen): qucsator netlist by default,
    or 'ngspice' / 'xyce' (Qucs-S's own SPICE translation)."""
    be = backend(be)
    posix_sch = isinstance(sch, str) and sch.startswith("/")  # a path on the simulator host (e.g. inside WSL)
    sch, net = (sch if posix_sch else Path(sch).resolve()), Path(net).resolve()
    net.parent.mkdir(parents=True, exist_ok=True)

    def p(x):
        if isinstance(x, str):
            return x
        return wslpath(x) if be == "wsl" else str(x)
    env, default, name = QUCS_S
    want = exe or os.environ.get(env) or default
    flag = {"qucsator": "", "ngspice": " --ngspice", "xyce": " --xyce"}[flavour]
    lines = ["export QT_QPA_PLATFORM=offscreen",
             f'export LD_LIBRARY_PATH="{os.environ.get("CIRCUIT_TOOLS_QUCS_S_LIBS", QUCS_S_LIBS)}:$LD_LIBRARY_PATH"',
             f'EXE="{want}"', f'[ -x "$EXE" ] || EXE="$(command -v {name})"',
             f'[ -n "$EXE" ] || {{ echo "{name} not found" >&2; exit 127; }}',
             f'timeout {int(timeout_s)} "$EXE" -n{flag} -i "{p(sch)}" -o "{p(net)}" > /dev/null 2>&1; rc=$?',
             '"$EXE" -v 2>/dev/null | head -1', "exit $rc"]
    sc = write_script(net.parent / "qucs_s_convert.sh", lines)
    r = _bash(sc, be, distro or os.environ.get("CIRCUIT_TOOLS_DISTRO"), timeout_s + 60)
    ok = r.returncode == 0 and net.exists() and net.stat().st_size > 0
    return {"ok": ok, "netlist": str(net), "exit_code": r.returncode, "qucs_s_version": r.stdout.strip() or None,
            "stderr": r.stderr[-1000:]}
