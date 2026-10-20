"""JSON-in / JSON-out operations shared by the CLI and the MCP server.

Every function takes one dict and returns a JSON-serialisable dict. ``TOOLS`` holds each tool's name, description
and input schema; the MCP server publishes them as-is.
"""
from __future__ import annotations

from pathlib import Path

from . import __version__, engine, estimate as est, lint as lintmod, run as runmod
from .checks import overall, passivity, reciprocity
from .engine import jsonable
from .spec import SPEC_DOC, load, normalize, sblock_info
from .translate import support_matrix

EXAMPLE_SPEC = {
    "name": "rc_lowpass", "z0": 50,
    "components": [{"type": "port", "name": "P1", "nodes": ["in", "0"], "num": 1},
                   {"type": "R", "name": "R1", "nodes": ["in", "out"], "value": "100 Ohm"},
                   {"type": "C", "name": "C1", "nodes": ["out", "0"], "value": "1pF"},
                   {"type": "port", "name": "P2", "nodes": ["out", "0"], "num": 2}],
    "analyses": [{"type": "sparam", "start": "0.1GHz", "stop": "3GHz", "points": 59}],
}


def _spec(a):
    sp = a.get("spec")
    if sp is None:
        raise ValueError("give 'spec' (object or path to a JSON file)")
    return load(sp)


def spec_doc(a: dict) -> dict:
    return {"spec_keys": SPEC_DOC, "example": EXAMPLE_SPEC, "coverage": support_matrix()}


def lint(a: dict) -> dict:
    return lintmod.lint(_spec(a), {"sims": a.get("sims"), "skip_reference": a.get("skip_reference", False)})


def lint_rules(a: dict) -> dict:
    return {"rules": lintmod.rules_table()}


def gotchas(a: dict) -> dict:
    return {"gotchas": [{k: r[k] for k in ("id", "title", "lesson", "source")} for r in lintmod.rules_table()],
            "also": OTHER_GOTCHAS}


def translate(a: dict) -> dict:
    res = engine.translate(_spec(a), a["sim"], a.get("analysis"))
    if a.get("out_dir"):
        d = Path(a["out_dir"])
        d.mkdir(parents=True, exist_ok=True)
        for n, t in res["files"].items():
            (d / n).write_bytes(t.encode("utf-8"))
        res["written"] = [str(d / n) for n in res["files"]]
    return res


def simulate(a: dict) -> dict:
    r = engine.simulate(_spec(a), a["sim"], a["case_dir"], a.get("analysis"), timeout_s=a.get("timeout_s", 300),
                        exe=a.get("exe"), do_record=a.get("record", True), project=a.get("project", "circuit-agent-tools"),
                        tags=a.get("tags"), force=a.get("force", False))
    r.pop("result", None)
    return r


def crosscheck(a: dict) -> dict:
    sp = _spec(a)
    li = lintmod.lint(sp, {"sims": a.get("sims"), "skip_reference": True})
    if not li["ok"] and not a.get("force"):
        return {"ok": False, "refused": True, "reason": "lint errors (force=true overrides)",
                "lint": [f for f in li["findings"] if f["severity"] == "error"]}
    r = engine.crosscheck(sp, a["case_dir"], a.get("analysis"), a.get("sims"), a.get("reference"), a.get("tol"),
                          a.get("timeout_s", 300), a.get("record", True), a.get("exes"))
    r["lint_counts"] = li["counts"]
    return r


def estimate(a: dict) -> dict:
    return est.estimate(_spec(a), a.get("analysis"), a.get("sims"))


def status(a: dict) -> dict:
    st = runmod.host_status(a.get("backend"), a.get("distro"))
    return {"host": st, "guard": runmod.guard(st, a.get("est_mem_gb", 0.3), a.get("n_runs", 1))}


def doctor(a: dict) -> dict:
    mods = {}
    for m in ("numpy", "skrf", "scipy", "rflab"):
        try:
            mods[m] = getattr(__import__(m), "__version__", "ok")
        except ImportError:
            mods[m] = None
    from .record import PENDING, _rflab

    return {"circuit_agent_tools": __version__, "python_modules": mods, "backend": runmod.backend(a.get("backend")),
            "simulators": runmod.versions(a.get("backend"), a.get("distro"), a.get("exes")),
            "rflab": "importable" if _rflab() else f"not importable: records go to {PENDING}",
            "coverage": support_matrix()}


def touchstone_check(a: dict) -> dict:
    """Ports, z0, band, passivity and reciprocity of a Touchstone file."""
    info = sblock_info(a["file"])
    n = info.pop("network")
    cks = [passivity(n.s, n.f, a.get("tol", 1e-6)), reciprocity(n.s, n.f, a.get("rec_tol", 1e-3))]
    info["z0"] = [z.real for z in info["z0"]]
    return {"file": a["file"], **info, "checks": cks, "overall": overall(cks)}


def import_qucs(a: dict) -> dict:
    """Qucs-S schematic (converted by Qucs-S itself, headless) or qucsator netlist -> neutral spec."""
    from .translate.qucs_import import from_qucs_netlist

    conv = None
    if a.get("schematic"):
        sch = a["schematic"]
        net = Path(a.get("out_dir") or Path.home() / ".circuit-agent-tools") / (Path(sch).stem + ".net")
        conv = runmod.qucs_schematic_to_netlist(sch, net)
        if not conv["ok"]:
            return {"ok": False, "conversion": conv}
        text, base = net.read_text(encoding="utf-8", errors="replace"), None if sch.startswith("/") else str(Path(sch).parent)
    else:
        text, base = Path(a["netlist"]).read_text(encoding="utf-8", errors="replace"), str(Path(a["netlist"]).parent)
    spec = from_qucs_netlist(text, a.get("name") or Path(a.get("schematic") or a["netlist"]).stem, base)
    out = {"ok": True, "spec": spec, "conversion": conv, "unsupported": spec["import_notes"]["unsupported"]}
    if a.get("out"):
        import json

        Path(a["out"]).write_text(json.dumps(spec, indent=1), encoding="utf-8")
        out["spec_path"] = a["out"]
    return out


def normalize_spec(a: dict) -> dict:
    return jsonable(normalize(_spec(a)))


OTHER_GOTCHAS = [
    {"title": "wsl.exe re-parses bash -c arguments", "lesson": "'$f', '$HOME' and loops in "
     "'wsl -- bash -c \"...\"' expand early or vanish; the runner always writes a run.sh and runs 'bash run.sh'.",
     "source": "this package (build log), palace-agent-tools"},
    {"title": "Git Bash rewrites /mnt/c paths", "lesson": "From Git Bash on Windows, 'wsl bash /mnt/c/x.sh' becomes "
     "'C:/Program Files/Git/mnt/c/x.sh'; set MSYS_NO_PATHCONV=1 (Python subprocess is not affected).",
     "source": "this package (build log)"},
    {"title": "circuitRF verbs, not --help", "lesson": "'circuitrf --help' starts the GUI and hangs headless; use the "
     "verbs (sparam, dc, hb, check, explain, reference) and put the verb first.", "source": "circuitRF CLI reference"},
    {"title": "circuitRF HB tone needs a variable or a bare number", "lesson": "Tone=\"100 MHz\" is refused "
     "('Unexpected token MHz'); define F0 = 100 MHz and write Tone=\"F0\".", "source": "this package (diode HB)"},
    {"title": "circuitRF Unit= on sparam", "lesson": "Before circuitRF 1.0 'Unit=GHz' on a sparam directive was "
     "ignored and the sweep ran in Hz with a plausible flat S11; the translator writes 'Hz' on every number.",
     "source": "circuitRF netlist reference ('Changed in 1.0')"},
    {"title": "scikit-rf MLine defaults", "lesson": "skrf.media.MLine defaults to a Djordjevic-Svensson wideband "
     "dielectric (er, tan d given at 1 GHz) and 0.15 um roughness; qucsator and circuitRF use constant er/tan d and "
     "smooth copper. Set diel='frequencyinvariant', rough=0 for a like-for-like check.",
     "source": "scikit-rf 2.2 source"},
    {"title": "SPICE has no S-parameter blocks (ngspice)", "lesson": "ngspice cannot read Touchstone; fit a rational "
     "model (scikit-rf VectorFitting.write_spice_subcircuit_s) or use qucsator/circuitRF/Xyce-HB.",
     "source": "ngspice manual"},
    {"title": "qucsator HB on hard-driven diodes", "lesson": "qucsator_rf 1.0.7 .HB did not converge (150 and 500 "
     "iterations) on a 1 V, 50 ohm-driven diode that circuitRF and Xyce solved in one Newton run; its "
     "non-converged output still looks like a spectrum. Check the log for 'no convergence'.",
     "source": "this package (diode HB example)"},
    {"title": "Xyce HB output is two-sided", "lesson": "The HB.FD.csv lists -N..N f0 with half amplitudes; "
     "single-sided peak = 2|X_k| for k > 0.", "source": "this package"},
    {"title": "qucsator diode pin order", "lesson": "qucsator's Diode is 'Diode:D cathode anode' (diode.cpp NODE_C 0), "
     "the reverse of SPICE. A reversed diode mirrors the waveform, so every harmonic magnitude still agrees with the "
     "other engines; only the DC term (or a DC run: 5 V instead of 0.62 V) shows it. compare_harmonics fails a DC "
     "sign flip explicitly.", "source": "this package (diode DC/HB examples), qucsator_rf diode.cpp"},
    {"title": "Touchstone 1 and mixed port impedances", "lesson": "The option line holds one z0. "
     "skrf Network.write_touchstone(r_ref=...) renormalises the data to that value (an L-match S11 of 0 became "
     "-4.4 dB); circuitRF writes port 1's z0 plus a comment. We write Touchstone 2.0 with [Reference] instead.",
     "source": "this package (L-match example)"},
    {"title": "Qucs-S headless", "lesson": "QT_QPA_PLATFORM=offscreen qucs-s -n -i x.sch -o x.net converts a "
     "schematic to a qucsator netlist without a display (--ngspice / --xyce for SPICE); the conda qucs-s package "
     "needs Qt 6.7 from the Anaconda defaults channel, so build it against conda-forge qt6-main.",
     "source": "this package (install log)"},
    {"title": "Diode BV = 0", "lesson": "In SPICE, BV=0 puts the breakdown at 0 V; omit BV for 'no breakdown' "
     "(qucsator and circuitRF read 0 as 'off').", "source": "ngspice manual (diode model)"},
]


# ------------------------------------------------------------------------------------------------ registry

def _obj(props: dict, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": True}


S_STR = {"type": "string"}
S_NUM = {"type": "number"}
S_BOOL = {"type": "boolean"}
SPEC = {"type": ["object", "string"], "description": "neutral circuit spec (object) or path to a JSON file"}
SIMS = {"type": "array", "items": {"type": "string", "enum": ["scikit-rf", "ngspice", "xyce", "qucsator", "circuitrf"]}}
ANALYSIS = {"type": ["integer", "string"], "description": "analysis index or type (default: the first)"}

TOOLS = {
    "circuit_spec": {"fn": spec_doc, "description": "Describe the neutral circuit spec (components, analyses, value "
                     "rules) with an example and the simulator coverage matrix.", "schema": _obj({})},
    "circuit_lint": {"fn": lint, "description": "Lint a circuit spec: topology (floating nodes, ground, DC paths, "
                     "SPICE case collisions), unit-prefix traps, ports and z0, sweep resolution (checked with the "
                     "scikit-rf reference), Touchstone blocks (ports, band, z0, passivity, reciprocity, open ports), "
                     "line-model limits, transient/HB settings, simulator coverage.",
                     "schema": _obj({"spec": SPEC, "sims": SIMS, "skip_reference": S_BOOL}, ["spec"])},
    "circuit_lint_rules": {"fn": lint_rules, "description": "Every lint rule with its lesson and source.",
                           "schema": _obj({})},
    "circuit_gotchas": {"fn": gotchas, "description": "Lint rules plus workflow lessons (WSL, circuitRF CLI, "
                        "scikit-rf defaults, HB conventions).", "schema": _obj({})},
    "circuit_normalize": {"fn": normalize_spec, "description": "The spec with every value parsed to SI floats and "
                          "defaults filled (what the translators see).", "schema": _obj({"spec": SPEC}, ["spec"])},
    "circuit_translate": {"fn": translate, "description": "Write the netlist(s) for one simulator (ngspice, xyce, "
                          "qucsator, circuitrf) without running; returns file texts and the commands.",
                          "schema": _obj({"spec": SPEC, "sim": S_STR, "analysis": ANALYSIS, "out_dir": S_STR},
                                         ["spec", "sim"])},
    "circuit_estimate": {"fn": estimate, "description": "Estimated wall time and memory per simulator for one "
                         "analysis.", "schema": _obj({"spec": SPEC, "analysis": ANALYSIS, "sims": SIMS}, ["spec"])},
    "circuit_status": {"fn": status, "description": "CPUs, load, free RAM and running EM jobs on the simulator host; "
                       "whether circuit runs may start (guard).",
                       "schema": _obj({"backend": S_STR, "distro": S_STR, "n_runs": {"type": "integer"}})},
    "circuit_simulate": {"fn": simulate, "description": "Run one analysis in one simulator (or 'scikit-rf'), with "
                         "the host guard, a timeout and log scanning; writes Touchstone/CSV/JSON, runs passivity and "
                         "reciprocity on S-parameters and records the run (rflab.run/1).",
                         "schema": _obj({"spec": SPEC, "sim": S_STR, "case_dir": S_STR, "analysis": ANALYSIS,
                                         "timeout_s": S_NUM, "exe": S_STR, "record": S_BOOL, "force": S_BOOL},
                                        ["spec", "sim", "case_dir"])},
    "circuit_crosscheck": {"fn": crosscheck, "description": "Lint, then run the same analysis in every available "
                           "simulator and compare each with the reference (scikit-rf for S-parameters): max |dS|, "
                           "waveform difference relative to swing, harmonic dB differences, DC volts.",
                           "schema": _obj({"spec": SPEC, "case_dir": S_STR, "analysis": ANALYSIS, "sims": SIMS,
                                           "reference": S_STR, "tol": {"type": "object"}, "timeout_s": S_NUM,
                                           "record": S_BOOL, "force": S_BOOL}, ["spec", "case_dir"])},
    "circuit_import_qucs": {"fn": import_qucs, "description": "Import a Qucs-S schematic (.sch, converted headless "
                            "by Qucs-S itself) or a qucsator netlist into the neutral spec; lists what was not "
                            "imported.", "schema": _obj({"schematic": S_STR, "netlist": S_STR, "out": S_STR,
                                                         "out_dir": S_STR, "name": S_STR})},
    "circuit_touchstone_check": {"fn": touchstone_check, "description": "Ports, reference impedances, band, "
                                 "passivity and reciprocity of a Touchstone file.",
                                 "schema": _obj({"file": S_STR, "tol": S_NUM}, ["file"])},
    "circuit_doctor": {"fn": doctor, "description": "Installed simulators (paths, versions), Python modules, rflab "
                       "recording and the coverage matrix.", "schema": _obj({"backend": S_STR, "distro": S_STR})},
}


def call(name: str, args: dict | None) -> dict:
    if name not in TOOLS:
        raise KeyError(f"unknown tool {name}; known: {sorted(TOOLS)}")
    return jsonable(TOOLS[name]["fn"](dict(args or {})))
