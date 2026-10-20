import math

import pytest

from circuit_tools.spec import SpecError, frequencies, normalize, ports
from circuit_tools.units import UnitError, eng, parse_value, prefix_traps, spice_reading

from conftest import rc_spec


@pytest.mark.parametrize("text,unit,value", [
    ("1.46pF", "F", 1.46e-12), ("4.365 nH", "H", 4.365e-9), ("2.4GHz", "Hz", 2.4e9), ("50 Ohm", "Ohm", 50.0),
    ("1k", None, 1e3), ("1M", None, 1e6), ("1m", None, 1e-3), ("1meg", None, 1e6), ("1MEG", None, 1e6),
    ("10mm", "m", 1e-2), ("20mil", "m", 20 * 25.4e-6), ("1m", "m", 1.0), ("1F", "F", 1.0), ("35um", "m", 35e-6),
    ("100 MHz", "Hz", 1e8), (3, None, 3.0), ("0.5pF", "F", 0.5e-12), ("90deg", "deg", 90.0), ("1u", None, 1e-6),
])
def test_parse_value(text, unit, value):
    assert parse_value(text, unit) == pytest.approx(value, rel=1e-12)


def test_parse_errors():
    with pytest.raises(UnitError):
        parse_value("abc")
    with pytest.raises(UnitError):
        parse_value("1 parsec")
    with pytest.raises(UnitError):
        parse_value(True)


def test_spice_prefix_traps():
    assert spice_reading("1M") == pytest.approx(1e-3)
    assert spice_reading("1MEG") == pytest.approx(1e6)
    assert spice_reading("1F") == pytest.approx(1e-15)
    assert spice_reading("10mF") == pytest.approx(1e-2)
    assert "milli" in prefix_traps("1M")
    assert prefix_traps("1F", "F") is not None
    assert prefix_traps("1pF", "F") is None and prefix_traps("1meg") is None and prefix_traps(1e6) is None
    assert eng(1.46e-12, "F") == "1.46 pF"


def test_normalize_rc():
    s = normalize(rc_spec())
    c = {x["name"]: x for x in s["components"]}
    assert c["C1"]["value"] == pytest.approx(1e-12) and c["R1"]["value"] == 100
    assert [p["num"] for p in ports(s)] == [1, 2] and ports(s)[0]["z0"] == 50
    f = frequencies(s["analyses"][0])
    assert f[0] == 1e8 and f[-1] == 1e9 and len(f) == 11


def test_normalize_ground_alias_and_tline():
    sp = rc_spec()
    sp["components"][2]["nodes"] = ["out", "GND"]
    sp["components"].append({"type": "tline", "name": "T1", "nodes": ["out", "x"], "z0": 70.7, "length_deg": 90,
                             "f_ref": "2.4GHz"})
    s = normalize(sp)
    c = {x["name"]: x for x in s["components"]}
    assert c["C1"]["nodes"] == ["out", "0"] and c["C1"]["_ground_alias"] == ["GND"]
    assert c["T1"]["delay_s"] == pytest.approx(1 / (4 * 2.4e9))
    assert c["T1"]["length"] == pytest.approx(299792458 / (4 * 2.4e9))


def test_normalize_errors():
    with pytest.raises(SpecError):
        normalize({"components": [{"type": "Q", "nodes": ["a", "b"]}]})
    with pytest.raises(SpecError):
        normalize({"components": [{"type": "R", "name": "R1", "nodes": ["a"], "value": 1}]})
    with pytest.raises(SpecError):
        normalize({"components": [{"type": "R", "name": "R1", "nodes": ["a", "0"], "value": 1},
                                  {"type": "R", "name": "R1", "nodes": ["a", "0"], "value": 1}]})
    with pytest.raises(SpecError):
        normalize({"components": [{"type": "mline", "name": "M", "nodes": ["a", "b"], "w": 1e-3, "l": 1e-2,
                                   "substrate": "none"}]})
    with pytest.raises(SpecError):
        normalize({"components": [{"type": "diode", "name": "D", "nodes": ["a", "0"], "model": {"xx": 1}}]})


def test_diode_and_sources():
    s = normalize({"components": [
        {"type": "vsource", "name": "V1", "nodes": ["s", "0"], "sin": {"amp": "300mV", "freq": "100MHz"}},
        {"type": "diode", "name": "D1", "nodes": ["s", "0"], "model": {"IS": 1e-12, "cj0": "0.5pF"}}],
        "analyses": [{"type": "hb", "f0": "100MHz", "harmonics": 4}]})
    v, d = s["components"]
    assert v["sin"]["amp"] == pytest.approx(0.3) and v["sin"]["freq"] == 1e8
    assert d["model"]["is"] == 1e-12 and d["model"]["cj0"] == pytest.approx(0.5e-12) and d["model"]["n"] == 1.0
    assert s["analyses"][0]["harmonics"] == 4 and math.isclose(s["analyses"][0]["f0"], 1e8)
