import json
import math
import re

import numpy as np
import pytest

from circuit_tools.spec import normalize
from circuit_tools.translate import Unsupported, backends, circuitrf, qucs, spice
from circuit_tools.translate.spice import _dft, _read_csv, _read_wrdata

from conftest import ROOT, rc_spec

S7P = ROOT / "examples" / "chebyshev_lpf" / "data" / "em_7port.s7p"


def _build(sim, spec, which=0):
    s = normalize(spec)
    return backends()[sim].build(s, s["analyses"][which])


def test_ngspice_sparam_one_deck_per_port():
    job = _build("ngspice", rc_spec())
    assert sorted(job.files) == ["sp_1.cir", "sp_2.cir"]
    d1 = job.files["sp_1.cir"]
    assert "V_port1 psrc_1 0 DC 0 AC 2" in d1 and "V_port2 psrc_2 0 DC 0 AC 0" in d1
    assert "R_port1 psrc_1 in 50" in d1 and "C1 out 0 1e-12" in d1 and "R1 in out 100" in d1
    assert "ac lin 11 100000000 1000000000" in d1 and "wrdata sp_1.txt v(in) v(out)" in d1
    assert ".options temp=27 tnom=27" in d1


def test_spice_names_get_type_letter():
    sp = rc_spec()
    sp["components"][1]["name"] = "load"
    job = _build("ngspice", sp)
    assert re.search(r"^R_load in out 100$", job.files["sp_1.cir"], re.M)


def test_xyce_lin_and_refusals():
    job = _build("xyce", rc_spec())
    d = job.files["sp.cir"]
    assert "P1 in 0 port=1 z0=50" in d and ".LIN SPARCALC=1 FORMAT=TOUCHSTONE2 FILE=sp.s2p" in d
    sp = rc_spec()
    sp["components"].append({"type": "tline", "name": "T1", "nodes": ["out", "x"], "z0": 50, "length_deg": 90,
                             "f_ref": "1GHz"})
    with pytest.raises(Unsupported):
        _build("xyce", sp)
    assert "T1 out 0 x 0 Z0=50" in _build("ngspice", sp).files["sp_1.cir"]


def test_qucsator_netlist():
    sp = rc_spec(substrates={"s": {"er": 3.55, "h": "0.508mm", "t": "35um", "tand": 0.0027}})
    sp["components"] += [{"type": "mline", "name": "M1", "nodes": ["out", "x"], "w": "1mm", "l": "10mm",
                          "substrate": "s"},
                         {"type": "diode", "name": "D1", "nodes": ["x", "0"], "model": {"is": 1e-12}}]
    s = normalize(sp)
    job = qucs.QUCSATOR.build(s, s["analyses"][0])
    net = job.files["case.net"]
    assert 'Pac:P1 in gnd Num="1" Z="50"' in net
    assert re.search(r'SUBST:Sub_s er="3.55" h="0.000508" t="3.5e-05" tand="0.0027" rho="1.72413793103e-08"', net)
    assert 'MLIN:M1 out x Subst="Sub_s" W="0.001" L="0.01"' in net
    assert "Diode:D1 gnd x " in net  # cathode first in qucsator
    assert '.SP:SP1 Type="lin" Start="100000000" Stop="1000000000" Points="11"' in net


def test_qucsator_series_voltage_sources():
    sp = {"components": [{"type": "vsource", "name": "V1", "nodes": ["s", "0"], "dc": 0.5,
                          "sin": {"amp": 1, "freq": 1e8}}, {"type": "R", "name": "R", "nodes": ["s", "0"], "value": 1}],
          "analyses": [{"type": "tran", "stop": 1e-7, "step": 1e-10}]}
    net = _build("qucsator", sp).files["case.net"]
    assert 'Vdc:V1 s V1_s0 U="0.5"' in net and 'Vac:V1_1 V1_s0 gnd U="1"' in net and 'Phase="90"' in net


def test_circuitrf_netlist_and_tech():
    sp = rc_spec(substrates={"s": {"er": 3.55, "h": "0.508mm", "t": "35um"}})
    sp["components"] += [{"type": "mline", "name": "M1", "nodes": ["out", "x"], "w": "1mm", "l": "10mm",
                          "substrate": "s"},
                         {"type": "sblock", "name": "EM", "nodes": ["x", "y"], "file": str(ROOT / "tests" / "data" /
                                                                                         "two.s2p")}]
    s = normalize(sp)
    job = circuitrf.CIRCUITRF.build(s, s["analyses"][0])
    cnl = job.files["case.cnl"]
    assert 'technology "sub.ctech"' in cnl and "Port:P1 in 0 Num=1 Z=50 Ohm" in cnl
    assert "C:C1 out 0 C=1e-12 F" in cnl and "MLIN:M1 out x W=1 mm L=10 mm" in cnl
    assert 'SnP:EM x y NumPorts=2 File="two.s2p"' in cnl
    assert "analysis SP1 type=sparam start=100000000 Hz stop=1000000000 Hz npts=11" in cnl
    tech = json.loads(job.files["sub.ctech"])
    assert tech["Stackup"]["Layers"][1]["ThicknessDbu"] == 508000 and tech["Stackup"]["Layers"][1]["Epsr"] == 3.55
    with pytest.raises(Unsupported):
        circuitrf.CIRCUITRF.build(s, {"type": "tran", "stop": 1, "step": 1e-3, "start": 0, "outputs": []})


def _coupled_spec():
    sp = rc_spec(substrates={"s": {"er": 3.66, "h": "0.508mm", "tand": 0.0037, "sigma": 1e30}})
    sp["components"] = [c for c in sp["components"] if c["type"] == "port"]
    sp["components"][1]["nodes"] = ["b2", "0"]
    sp["components"] += [{"type": "mstep", "name": "S1", "nodes": ["in", "a1"], "w1": "1.112mm", "w2": "0.88mm",
                          "substrate": "s"},
                         {"type": "mcline", "name": "CL1", "nodes": ["a1", "o1", "o2", "b2"], "w": "0.88mm",
                          "s": "0.18mm", "l": "8.6mm", "substrate": "s"},
                         {"type": "mopen", "name": "OE1", "nodes": ["o1"], "w": "0.88mm", "substrate": "s"},
                         {"type": "mopen", "name": "OE2", "nodes": ["o2"], "w": "0.88mm", "substrate": "s"}]
    return sp


def test_qucsator_coupled_microstrip_parts():
    net = _build("qucsator", _coupled_spec()).files["case.net"]
    assert net.count("SUBST:Sub_s ") == 1
    # spec order: line 1 a1 -> o1, line 2 o2 -> b2 in the same direction; MCOUPLED wants line 2 from the far end
    assert ('MCOUPLED:CL1 a1 o1 b2 o2 Subst="Sub_s" W="0.00088" L="0.0086" S="0.00018" Model="Kirschning" '
            'DispModel="Kirschning"') in net
    assert 'MOPEN:OE1 o1 Subst="Sub_s" W="0.00088" MSDispModel="Kirschning" MSModel="Hammerstad" '            'Model="Kirschning"' in net
    assert 'MSTEP:S1 in a1 Subst="Sub_s" W1="0.001112" W2="0.00088"' in net
    sp = _coupled_spec()
    sp["components"][2].update(nodes=["a1", "in"], w1="0.88mm", w2="1.112mm")   # narrow side first in the spec
    assert 'MSTEP:S1 in a1 Subst="Sub_s" W1="0.001112" W2="0.00088"' in _build("qucsator", sp).files["case.net"]


@pytest.mark.parametrize("sim", ["ngspice", "xyce", "circuitrf"])
def test_coupled_microstrip_refused_elsewhere(sim):
    with pytest.raises(Unsupported, match="mstep|mcline|mopen"):
        _build(sim, _coupled_spec())


def test_hb_sources_cosine_reference():
    sp = {"components": [{"type": "vsource", "name": "V1", "nodes": ["s", "0"], "sin": {"amp": 1, "freq": 1e8}},
                         {"type": "R", "name": "R", "nodes": ["s", "a"], "value": 50},
                         {"type": "diode", "name": "D1", "nodes": ["a", "0"]}],
          "analyses": [{"type": "hb", "f0": 1e8, "harmonics": 8, "outputs": ["a"]}]}
    assert "SIN(0 1 100000000 0 0 90)" in _build("xyce", sp).files["hb.cir"]
    assert ".OPTIONS HBINT NUMFREQ=8" in _build("xyce", sp).files["hb.cir"]
    cnl = _build("circuitrf", sp).files["case.cnl"]
    assert "F0 = 100000000 Hz" in cnl and "V_1Tone:V1 s 0 V=1 V Freq=F0" in cnl and 'Tone="F0" MaxHarm=8' in cnl
    ng = _build("ngspice", sp)
    assert "tran " in ng.files["tran.cir"] and ng.meta["T"] == pytest.approx(1e-8)


def test_bv_zero_is_omitted_in_spice():
    sp = {"components": [{"type": "diode", "name": "D1", "nodes": ["a", "0"]},
                         {"type": "vsource", "name": "V", "nodes": ["a", "0"], "dc": 0.6}],
          "analyses": [{"type": "dc"}]}
    deck = _build("ngspice", sp).files["dc.cir"]
    assert "BV=" not in deck and ".model DM_D1 D (IS=1e-14" in deck


def test_parse_ngspice_wrdata(data):
    d = _read_wrdata(data / "ngspice_wrdata.txt")
    assert d.shape[1] == 5 and d[0, 0] == pytest.approx(1e8)
    assert d[0, 1] - 1 == pytest.approx(0.49990752, rel=1e-6)  # S11 = V1 - 1


def test_parse_xyce_csv(data):
    cols = _read_csv(data / "xyce_hb_fd.csv")
    f = np.array(cols["FREQ"])
    k = int(np.argmin(abs(f - 1e8)))
    v1 = 2 * complex(cols["VR(A)"][k], cols["VI(A)"][k])
    assert abs(v1) == pytest.approx(0.8715, rel=1e-3)
    t = _read_csv(data / "xyce_tran.csv")
    assert "TIME" in t and "V(C)" in t


def test_parse_qucs_datasets(data):
    ds = qucs.read_dataset(data / "qucs_sp.dat")
    assert len(ds["indep"]["frequency"]) == 11
    assert ds["dep"]["S[1,1]"][0] == pytest.approx(0.49990752 - 0.00392481j, rel=1e-6)
    dc = qucs.read_dataset(data / "qucs_dc.dat")
    assert "a.V" in dc["indep"] and 0.6 < dc["indep"]["a.V"][0].real < 0.7
    hb = qucs.read_dataset(data / "qucs_hb.dat")
    assert "a.Vb" in hb["dep"] and len(hb["indep"]["hbfrequency"]) == len(hb["dep"]["a.Vb"])


def test_parse_circuitrf_json(data, tmp_path):
    s = normalize({"components": [{"type": "vsource", "name": "V1", "nodes": ["src", "0"],
                                   "sin": {"amp": 1, "freq": 1e8}},
                                  {"type": "R", "name": "R1", "nodes": ["src", "a"], "value": 50},
                                  {"type": "diode", "name": "D1", "nodes": ["a", "0"]}],
                   "analyses": [{"type": "hb", "f0": 1e8, "harmonics": 8, "outputs": ["a"]}, {"type": "dc"}]})
    (tmp_path / "hb.json").write_text((data / "circuitrf_hb.json").read_text())
    job = circuitrf.CIRCUITRF.build(s, s["analyses"][0])
    r = circuitrf.CIRCUITRF.parse(tmp_path, s, s["analyses"][0], job)
    assert abs(r["v"]["a"][1]) == pytest.approx(0.87131, rel=1e-4) and r["v"]["a"][0].real < 0
    (tmp_path / "dc.json").write_text((data / "circuitrf_dc.json").read_text())
    job = circuitrf.CIRCUITRF.build(s, s["analyses"][1])
    job.meta["outputs"] = ["a", "b"]
    r = circuitrf.CIRCUITRF.parse(tmp_path, s, s["analyses"][1], job)
    assert 0.6 < r["v"]["a"] < 0.7 and r["v"]["b"] == pytest.approx(3.75, rel=1e-6)


def test_dft_exact_bins():
    T = 1e-8
    t = np.arange(0, 10 * T, T / 256) + 30 * T
    x = 0.1 + 0.5 * np.cos(2 * np.pi * t / T + 0.3) + 0.2 * np.cos(4 * np.pi * t / T)
    v = _dft(t, x, T, 30 * T, 40 * T, 3)
    assert v[0] == pytest.approx(0.1) and abs(v[1]) == pytest.approx(0.5) and np.angle(v[1]) == pytest.approx(0.3)
    assert abs(v[2]) == pytest.approx(0.2) and abs(v[3]) < 1e-12


def test_support_matrix_shape():
    from circuit_tools.translate import support_matrix

    m = support_matrix()
    assert set(m) == {"ngspice", "xyce", "qucsator", "circuitrf", "scikit-rf"}
    assert "sblock" in m["qucsator"]["sparam"]["components"] and "sblock" not in m["ngspice"]["sparam"]["components"]
    for part in ("mcline", "mopen", "mstep"):
        assert part in m["qucsator"]["sparam"]["components"]
        assert all(part not in m[k]["sparam"]["components"] for k in ("ngspice", "xyce", "circuitrf", "scikit-rf"))
    assert m["circuitrf"]["tran"]["components"] == [] and math.isfinite(len(m["xyce"]["hb"]["components"]))
    assert spice.NGSPICE.NAME == "ngspice"
