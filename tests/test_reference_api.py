import json
import math
import subprocess
import sys

import numpy as np
import pytest

from circuit_tools import api, checks, estimate, record
from circuit_tools.engine import touchstone_text, write_outputs
from circuit_tools.reference import Unsupported, rc_step, rlc_series_step, solve_sparam
from circuit_tools.translate.qucs_import import from_qucs_netlist

from conftest import ROOT, rc_spec


def test_reference_rc_closed_form():
    r = solve_sparam(rc_spec())
    f = r["f"]
    w = 2 * np.pi * f
    zc = 1 / (1j * w * 1e-12)
    zp = zc * 50 / (zc + 50)  # load at 'out': C || port 2
    zin = 100 + zp
    s11 = (zin - 50) / (zin + 50)
    assert np.abs(r["s"][:, 0, 0] - s11).max() < 1e-12
    assert checks.passivity(r["s"])["status"] == "pass" and checks.reciprocity(r["s"])["status"] == "pass"


def test_reference_mixed_z0_lmatch():
    f0, rs, rl = 2.4e9, 50.0, 200.0
    q = math.sqrt(rl / rs - 1)
    spec = {"components": [{"type": "port", "name": "P1", "nodes": ["a"], "num": 1, "z0": rs},
                           {"type": "L", "name": "L", "nodes": ["a", "b"], "value": q * rs / (2 * math.pi * f0)},
                           {"type": "C", "name": "C", "nodes": ["b", "0"], "value": q / (rl * 2 * math.pi * f0)},
                           {"type": "port", "name": "P2", "nodes": ["b"], "num": 2, "z0": rl}],
            "analyses": [{"type": "sparam", "start": f0, "stop": f0, "points": 1}]}
    r = solve_sparam(spec)
    assert abs(r["s"][0, 0, 0]) < 1e-10 and abs(r["s"][0, 1, 0]) == pytest.approx(1, abs=1e-10)


def test_reference_refuses_nonlinear():
    s = rc_spec()
    s["components"].append({"type": "diode", "name": "D", "nodes": ["out", "0"]})
    with pytest.raises(Unsupported):
        solve_sparam(s)


def test_closed_form_helpers():
    t = np.linspace(0, 5e-9, 101)
    assert rc_step(t, 50, 1e-12)[-1] == pytest.approx(1, abs=1e-9)
    y = rlc_series_step(t, 10, 10e-9, 1e-12)
    assert y.max() > 1.5  # Q = 10 overshoot
    assert rlc_series_step(t, 1e4, 1e-9, 1e-12)[-1] < 1


def test_compare_helpers():
    f = np.linspace(1, 2, 3)
    a = {"f": f, "s": np.zeros((3, 2, 2), complex)}
    b = {"f": f, "s": np.full((3, 2, 2), 1e-3, complex)}
    c = checks.compare_s(a, b, 1e-2)
    assert c["status"] == "pass" and c["value"] == pytest.approx(1e-3)
    t = np.linspace(0, 1, 11)
    w = checks.compare_waveforms({"t": t, "v": {"x": t + 0.02}}, {"t": t, "v": {"x": t}}, 0.01)
    assert w["status"] == "fail" and w["value"] == pytest.approx(0.02)
    h1 = {"v": {"a": np.array([-0.07, 0.87, 0.09])}}
    h2 = {"v": {"a": np.array([0.07, 0.87, 0.09])}}  # same magnitudes, DC sign flipped
    assert checks.compare_harmonics(h1, h2)["status"] == "fail"
    assert checks.compare_harmonics(h1, h1)["status"] == "pass"
    assert checks.compare_dc({"v": {"a": 1.0}}, {"v": {"a": 1.00001}})["status"] == "pass"


def test_touchstone_writer_keeps_per_port_z0(tmp_path):
    import skrf as rf

    f = np.array([1e9, 2e9])
    S = np.random.default_rng(1).random((2, 2, 2)) * 0.5 + 0j
    p = tmp_path / "x.s2p"
    p.write_text(touchstone_text(f, S, [50, 200]))
    n = rf.Network(str(p))
    assert np.allclose(n.s, S) and np.allclose(n.z0[0].real, [50, 200])
    files = write_outputs({"kind": "sparam", "f": f, "s": S, "z0": np.array([50.0, 50.0])}, tmp_path / "y")
    assert {x.suffix for x in files} == {".s2p", ".csv"} and np.allclose(rf.Network(str(files[0])).s, S)


def test_estimate_and_api_registry():
    e = estimate.estimate(rc_spec())
    assert e["per_sim"]["ngspice"]["runs"] == 2 and e["total_wall_s"] > 0 and not e["ask_user_first"]
    assert {"circuit_lint", "circuit_crosscheck", "circuit_simulate", "circuit_translate", "circuit_import_qucs",
            "circuit_touchstone_check", "circuit_doctor"} <= set(api.TOOLS)
    for t in api.TOOLS.values():
        assert t["schema"]["type"] == "object" and t["description"]
    sp = api.call("circuit_spec", {})
    assert "components" in sp["spec_keys"] and "ngspice" in sp["coverage"]
    tr = api.call("circuit_translate", {"spec": rc_spec(), "sim": "qucsator"})
    assert "case.net" in tr["files"]
    assert api.call("circuit_gotchas", {})["also"]


def test_touchstone_check_tool():
    r = api.call("circuit_touchstone_check", {"file": str(ROOT / "examples" / "chebyshev_lpf" / "data" /
                                                          "em_7port.s7p")})
    assert r["ports"] == 7 and r["points"] == 317 and r["overall"] == "pass"


def test_record_fallback_jsonl_is_schema_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "_rflab", lambda: None)
    monkeypatch.setattr(record, "PENDING", tmp_path / "pending.jsonl")
    monkeypatch.setenv("CIRCUIT_TOOLS_RECORD", "1")
    inp = tmp_path / "a.cir"
    inp.write_text("* x\n")
    out = record.save_run("p", "c", {"name": "ngspice", "version": "46"}, "pass", {"x": 1}, [inp], [],
                          {"a": {"b": 1.5}, "flag": True, "bad": float("nan")},
                          [{"name": "passivity", "status": "pass", "value": 1.0, "detail": "ok"},
                           {"name": "skipme", "status": "skip"}], 0.5, ["t"])
    rec = json.loads((tmp_path / "pending.jsonl").read_text().splitlines()[0])
    assert out["via"] == "pending-jsonl" and rec["status"] == "failed"  # 'pass' is not an rflab status
    assert rec["outputs"]["metrics"] == {"a.b": 1.5, "flag": 1, "bad": "nan"}
    assert rec["checks"] == [{"name": "passivity", "passed": True, "value": 1.0, "limit": None, "note": "ok"}]
    try:
        from rflab.schema import validate
    except ImportError:
        pytest.skip("rflab-core not installed")
    assert validate(rec) == []


def test_record_through_rflab(tmp_path, monkeypatch):
    rflab = pytest.importorskip("rflab")
    monkeypatch.setenv("RFLAB_HOME", str(tmp_path / "rflab"))
    monkeypatch.setenv("CIRCUIT_TOOLS_RECORD", "1")
    out = record.save_run("p", "c", {"name": "qucsator", "version": "1.0.7"}, "ok", {"x": 1}, [], [],
                          {"max_dS": 1e-9}, [{"name": "passivity", "status": "warn", "value": 1.0000001}], 0.2,
                          ["t"])
    assert out["via"] == "rflab"
    rec = rflab.get(out["run_id"], store=str(tmp_path / "rflab"))
    assert rec["kind"] == "circuit" and rec["checks"][0]["passed"] is True


def test_qucs_netlist_import():
    text = "\n".join([
        "# hand-written qucsator netlist",
        'Pac:P1 n1 gnd Num="1" Z="50 Ohm" P="0 dBm" f="1 GHz" Temp="26.85"',
        'TLIN:T1 n1 n2 Z="70.7 Ohm" L="31.2 mm" Alpha="0 dB" Temp="26.85"',
        'SUBST:S1 er="3.55" h="0.508 mm" t="35 um" tand="0.0027" rho="1.72e-8" D="0"',
        'MLIN:M1 n2 n3 Subst="S1" W="1.1 mm" L="10 mm" Model="Hammerstad" DispModel="Kirschning" Temp="26.85"',
        'R:R1 n3 gnd R="100 Ohm" Temp="26.85" Tc1="0.0" Tc2="0.0" Tnom="26.85"',
        'MTEE:X1 n1 n2 n3 Subst="S1" W1="1 mm" W2="1 mm" W3="1 mm"',
        'Diode:D1 gnd n3 Is="1e-12 A" N="1.05" Rs="5 Ohm" Cj0="0.5 pF"',
        '.SP:SP1 Type="lin" Start="0.1 MHz" Stop="2 GHz" Points="100" Noise="no"'])
    s = from_qucs_netlist(text, "imp")
    c = {x["name"]: x for x in s["components"]}
    assert c["P1"]["nodes"] == ["n1", "0"] and c["T1"]["length"] == pytest.approx(0.0312)
    assert c["M1"]["w"] == pytest.approx(1.1e-3) and s["substrates"]["S1"]["sigma"] == pytest.approx(1 / 1.72e-8)
    assert c["D1"]["nodes"] == ["n3", "0"]  # anode first in the spec
    assert s["analyses"][0]["start"] == pytest.approx(1e5) and s["analyses"][0]["points"] == 100
    assert s["import_notes"]["unsupported"] == ["MTEE:X1"]


def _mcp(lines):
    p = subprocess.run([sys.executable, "-m", "circuit_tools.mcp_server"],
                       input="\n".join(map(json.dumps, lines)) + "\n", capture_output=True, text=True, timeout=120)
    return [json.loads(x) for x in p.stdout.splitlines() if x.strip()]


def test_mcp_server_protocol():
    out = _mcp([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "circuit_lint", "arguments": {"spec": rc_spec()}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 5, "method": "bogus"}])
    by = {m["id"]: m for m in out}
    assert by[1]["result"]["protocolVersion"] == "2025-06-18"
    assert "circuitrf serve" in by[1]["result"]["instructions"]
    names = {t["name"] for t in by[2]["result"]["tools"]}
    assert {"circuit_lint", "circuit_crosscheck", "circuit_simulate"} <= names
    assert by[3]["result"]["structuredContent"]["ok"] is True
    assert by[4]["result"]["isError"] is True and by[5]["error"]["code"] == -32601 and len(out) == 5


def test_cli_json_io(tmp_path):
    p = subprocess.run([sys.executable, "-m", "circuit_tools", "lint", json.dumps({"spec": rc_spec()})],
                       capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["ok"] is True
    bad = rc_spec()
    bad["components"][3]["num"] = 5
    f = tmp_path / "bad.json"
    f.write_text(json.dumps({"spec": bad}))
    p = subprocess.run([sys.executable, "-m", "circuit_tools", "lint", "@" + str(f)], capture_output=True, text=True)
    assert p.returncode == 1 and not json.loads(p.stdout)["ok"]
    p = subprocess.run([sys.executable, "-m", "circuit_tools", "lint", "{bad json"], capture_output=True, text=True)
    assert p.returncode == 2
