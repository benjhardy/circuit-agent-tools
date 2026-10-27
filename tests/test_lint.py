import copy

import numpy as np
import pytest

from circuit_tools.lint import RULES, lint, rules_table

from conftest import ROOT, rc_spec

S7P = str(ROOT / "examples" / "chebyshev_lpf" / "data" / "em_7port.s7p")


def ids(spec, sev=None, **ctx):
    r = lint(spec, ctx)
    return {f["id"] for f in r["findings"] if sev is None or f["severity"] == sev}


def test_clean_spec_has_no_errors_or_warnings():
    r = lint(rc_spec())
    assert r["ok"] and r["counts"]["error"] == 0 and r["counts"]["warning"] == 0


def test_every_rule_has_lesson_and_source():
    assert len(RULES) >= 25
    for r in rules_table():
        assert r["lesson"] and r["source"] and r["title"], r["id"]


def test_spec_error_is_reported_not_raised():
    r = lint({"components": [{"type": "Q", "nodes": []}]})
    assert not r["ok"] and r["findings"][0]["id"] == "SPEC"


def test_floating_node_and_ground():
    s = rc_spec()
    s["components"].append({"type": "R", "name": "Rx", "nodes": ["out", "dangle"], "value": 10})
    assert "TOP001" in ids(s, "warning")
    g = {"components": [{"type": "R", "name": "R1", "nodes": ["a", "b"], "value": 1},
                        {"type": "R", "name": "R2", "nodes": ["a", "b"], "value": 1}], "analyses": [{"type": "dc"}]}
    assert "TOP002" in ids(g, "error")


def test_dc_path_rule():
    s = {"components": [{"type": "vsource", "name": "V1", "nodes": ["a", "0"], "dc": 1},
                        {"type": "C", "name": "C1", "nodes": ["a", "b"], "value": 1e-12},
                        {"type": "C", "name": "C2", "nodes": ["b", "0"], "value": 1e-12}],
         "analyses": [{"type": "tran", "stop": 1e-9, "step": 1e-12}]}
    assert "TOP003" in ids(s, "error")
    s["analyses"] = [{"type": "sparam", "start": 1e9, "stop": 2e9, "points": 3}]
    assert "TOP003" not in ids(s)


def test_source_loop():
    s = {"components": [{"type": "vsource", "name": "V1", "nodes": ["a", "0"], "dc": 1},
                        {"type": "L", "name": "L1", "nodes": ["a", "0"], "value": 1e-9}], "analyses": [{"type": "dc"}]}
    assert "TOP004" in ids(s, "error")


def test_spice_case_collisions():
    s = rc_spec()
    s["components"].append({"type": "R", "name": "r1", "nodes": ["OUT", "0"], "value": 1e3})
    found = lint(s)["findings"]
    msgs = [f["message"] for f in found if f["id"] == "TOP005"]
    assert any("nodes" in m for m in msgs) and any("components" in m for m in msgs)


def test_ground_alias_info():
    s = rc_spec()
    s["components"][2]["nodes"] = ["out", "GND"]
    assert "TOP006" in ids(s, "info")


def test_prefix_trap_and_magnitude():
    s = rc_spec()
    s["components"][1]["value"] = "1M"   # mega here, milli in SPICE
    s["components"][2]["value"] = "1F"   # one farad here, femto in SPICE
    assert {"UNI001", "UNI002"} <= ids(s, "warning")
    s["components"][1]["value"] = "1Meg"
    s["components"][2]["value"] = "1pF"
    assert "UNI001" not in ids(s)


def test_port_rules():
    s = rc_spec()
    s["components"][3]["z0"] = 100
    assert "PRT001" in ids(s, "warning")
    s["components"][3]["num"] = 3
    assert "PRT002" in ids(s, "error")
    s = rc_spec()
    s["components"][3]["nodes"] = ["out", "ref"]
    s["components"].append({"type": "R", "name": "Rr", "nodes": ["ref", "0"], "value": 1})
    assert "PRT003" in ids(s, "info")
    s = rc_spec()
    s["components"].append({"type": "vsource", "name": "Vb", "nodes": ["out", "0"], "dc": 1})
    assert "PRT004" in ids(s, "warning")
    assert "PRT002" in ids({"components": [{"type": "R", "name": "R", "nodes": ["a", "0"], "value": 1}],
                            "analyses": [{"type": "sparam", "start": 1e9, "stop": 2e9, "points": 3}]}, "error")


def test_sweep_too_coarse_uses_reference():
    # shunt series-LC trap (X0 = 1 kohm, Q ~ 40 at 1.59 GHz): 11 points across 1-3 GHz step over the notch
    s = {"name": "q", "components": [{"type": "port", "name": "P1", "nodes": ["a"], "num": 1},
                                     {"type": "L", "name": "L1", "nodes": ["a", "b"], "value": 100e-9},
                                     {"type": "C", "name": "C1", "nodes": ["b", "0"], "value": 0.1e-12},
                                     {"type": "port", "name": "P2", "nodes": ["a"], "num": 2}],
         "analyses": [{"type": "sparam", "start": 1e9, "stop": 3e9, "points": 11}]}
    assert "SWP001" in ids(s, "warning")
    s["analyses"][0]["points"] = 2001
    assert "SWP001" not in ids(s)
    assert "SWP001" not in ids(dict(s, analyses=[dict(s["analyses"][0], points=11)]), skip_reference=True)


def test_sweep_start_and_line_phase():
    s = rc_spec()
    s["analyses"][0]["start"] = 0
    assert "SWP002" in ids(s, "warning")
    s = rc_spec()
    s["components"].append({"type": "tline", "name": "T1", "nodes": ["out", "x"], "z0": 50, "length": 3.0,
                            "f_ref": "1GHz"})
    s["components"].append({"type": "R", "name": "Rl", "nodes": ["x", "0"], "value": 50})
    assert "SWP003" in ids(s, "warning")


def _em_spec(file=S7P, nodes=None, sweep=(0.1e9, 8e9)):
    nodes = nodes or ["p1", "p2", "a", "b", "c", "d", "e"]
    comps = [{"type": "sblock", "name": "EM", "nodes": nodes, "file": file},
             {"type": "port", "name": "P1", "nodes": ["p1"], "num": 1},
             {"type": "port", "name": "P2", "nodes": ["p2"], "num": 2}]
    comps += [{"type": "C", "name": f"C{n}", "nodes": [n, "0"], "value": 1e-12} for n in nodes[2:]]
    return {"name": "em", "components": comps,
            "analyses": [{"type": "sparam", "start": sweep[0], "stop": sweep[1], "points": 317}]}


def test_sblock_rules_on_real_em_data():
    r = ids(_em_spec())
    assert not ({"SBK001", "SBK002", "SBK004"} & r)
    assert "SBK001" in ids(_em_spec(nodes=["p1", "p2", "a", "b", "c", "d"]), "error")
    assert "SBK002" in ids(_em_spec(sweep=(0.05e9, 9e9)), "error")
    s = _em_spec()
    s["components"] = [c for c in s["components"] if c.get("name") != "Ce"]
    assert "SBK006" in ids(s, "warning")


def test_sblock_passivity_and_reciprocity(tmp_path):
    from circuit_tools.engine import touchstone_text

    f = np.linspace(1e9, 2e9, 5)
    S = np.zeros((5, 2, 2), complex)
    S[:, 0, 1] = 1.05   # gain one way: not passive, not reciprocal
    S[:, 1, 0] = 0.2
    p = tmp_path / "bad.s2p"
    p.write_text(touchstone_text(f, S, 75.0))
    s = {"components": [{"type": "sblock", "name": "B", "nodes": ["a", "b"], "file": str(p)},
                        {"type": "port", "name": "P1", "nodes": ["a"], "num": 1},
                        {"type": "port", "name": "P2", "nodes": ["b"], "num": 2}],
         "analyses": [{"type": "sparam", "start": 1e9, "stop": 2e9, "points": 5}]}
    r = ids(s)
    assert {"SBK003", "SBK004", "SBK005"} <= r


def test_line_rules():
    sub = {"er": 3.55, "h": 0.508e-3, "t": 35e-6, "tand": 0.0027}
    s = rc_spec(substrates={"s": sub})
    s["components"].append({"type": "mline", "name": "M1", "nodes": ["out", "x"], "w": 1e-6, "l": 1e-2,
                            "substrate": "s"})
    s["components"].append({"type": "R", "name": "Rl", "nodes": ["x", "0"], "value": 50})
    r = ids(s)
    assert {"TLN001", "TLN002", "TLN004", "XYC001"} <= r
    s2 = rc_spec()
    s2["components"].append({"type": "tline", "name": "T1", "nodes": ["out", "x"], "z0": 50, "length_deg": 90})
    s2["components"].append({"type": "R", "name": "Rl", "nodes": ["x", "0"], "value": 50})
    assert "TLN003" in ids(s2, "warning")


def test_coupled_line_and_discontinuity_rules():
    def spec(w, s_gap, w1, w2, er=3.66):
        return {"substrates": {"s": {"er": er, "h": "0.508mm"}}, "components": [
            {"type": "port", "name": "P1", "nodes": ["a", "0"], "num": 1},
            {"type": "mstep", "name": "ST", "nodes": ["a", "b"], "w1": w1, "w2": w2, "substrate": "s"},
            {"type": "mcline", "name": "CL", "nodes": ["b", "c", "d", "e"], "w": w, "s": s_gap, "l": "8mm",
             "substrate": "s"},
            {"type": "mopen", "name": "O1", "nodes": ["c"], "w": w, "substrate": "s"},
            {"type": "mopen", "name": "O2", "nodes": ["d"], "w": w, "substrate": "s"},
            {"type": "port", "name": "P2", "nodes": ["e", "0"], "num": 2}],
            "analyses": [{"type": "sparam", "start": "3GHz", "stop": "7GHz", "points": 201}]}

    good = lint(spec("0.9mm", "0.2mm", "2mm", "1mm"))
    assert good["counts"]["error"] == 0
    assert not [f for f in good["findings"] if f["id"] in ("TLN005", "TLN006")]
    r = lint(spec("0.9mm", "0.03mm", "1.1mm", "0.9mm", er=20))
    msgs = {f["id"]: [] for f in r["findings"]}
    for f in r["findings"]:
        msgs[f["id"]].append(f["message"])
    assert any("s/h" in m for m in msgs["TLN005"]) and any("er = 20" in m for m in msgs["TLN005"])
    assert any("width ratio 1.22" in m and "negative" in m for m in msgs["TLN006"])
    assert any("er = 20 > 10" in m for m in msgs["TLN006"])
    f = [x for x in good["findings"] if x["id"] == "SUP001"][0]
    assert f["runs_in"] == ["qucsator"] and f["severity"] == "warning"


def test_coupled_line_dc_path():
    s = {"substrates": {"s": {"er": 3.66, "h": "0.508mm"}}, "components": [
        {"type": "vsource", "name": "V1", "nodes": ["a", "0"], "dc": 1},
        {"type": "mstep", "name": "ST", "nodes": ["a", "b"], "w1": "2mm", "w2": "1mm", "substrate": "s"},
        {"type": "mcline", "name": "CL", "nodes": ["b", "c", "d", "e"], "w": "1mm", "s": "0.2mm", "l": "8mm",
         "substrate": "s"},
        {"type": "R", "name": "R1", "nodes": ["c", "0"], "value": 50},
        {"type": "C", "name": "C2", "nodes": ["e", "0"], "value": 1e-12}],
        "analyses": [{"type": "dc"}]}
    dc = [f for f in lint(s)["findings"] if f["id"] == "TOP003"]
    assert sorted(f["node"] for f in dc) == ["d", "e"]   # line 1 conducts end to end; line 2 is isolated at DC


def test_transient_and_hb_rules():
    s = {"components": [{"type": "vsource", "name": "V1", "nodes": ["s", "0"], "pulse": {"v1": 0, "v2": 1}},
                        {"type": "R", "name": "R", "nodes": ["s", "c"], "value": 50},
                        {"type": "C", "name": "C", "nodes": ["c", "0"], "value": 1e-12}],
         "analyses": [{"type": "tran", "stop": 1e-9, "step": 1e-11, "outputs": ["c"]}]}
    assert {"TRN001", "TRN002"} <= ids(s, "warning")
    h = {"components": [{"type": "vsource", "name": "V1", "nodes": ["s", "0"], "sin": {"amp": 1, "freq": 1.5e8}},
                        {"type": "R", "name": "R", "nodes": ["s", "a"], "value": 50},
                        {"type": "diode", "name": "D", "nodes": ["a", "0"]}],
         "analyses": [{"type": "hb", "f0": 1e8, "harmonics": 8}]}
    found = lint(h)["findings"]
    assert any(f["id"] == "HB001" and f["severity"] == "error" for f in found)   # 150 MHz is not a harmonic
    assert any(f["id"] == "HB001" and f["severity"] == "warning" for f in found)  # 8 harmonics, 1 V


def test_coverage_rule_lists_simulators():
    f = [x for x in lint(_em_spec())["findings"] if x["id"] == "SUP001"][0]
    assert set(f["runs_in"]) == {"qucsator", "circuitrf"} and "ngspice" in f["skipped"]
    f = [x for x in lint(rc_spec(), {"sims": ["ngspice"]})["findings"] if x["id"] == "SUP001"][0]
    assert f["runs_in"] == ["ngspice"] and f["severity"] == "warning"


def test_lint_does_not_mutate_input():
    s = rc_spec()
    before = copy.deepcopy(s)
    lint(s)
    assert s == before


@pytest.mark.parametrize("rid", ["TOP001", "UNI001", "SBK004", "XYC001", "HB001", "TLN005", "TLN006"])
def test_gotchas_doc_mentions_rule(rid):
    doc = (ROOT / "skills" / "circuit-simulation" / "references" / "gotchas.md").read_text(encoding="utf-8")
    assert rid in doc
