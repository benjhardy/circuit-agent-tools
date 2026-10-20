"""Integration tests that run the real simulators (skipped when they are not installed or CIRCUIT_TOOLS_SKIP_SIM=1).

Small circuits only: the whole file takes ~15 s on a busy desktop.
"""
import os

import numpy as np
import pytest

from circuit_tools import engine, run

from conftest import rc_spec

pytestmark = pytest.mark.sim


@pytest.fixture(scope="module")
def available():
    if os.environ.get("CIRCUIT_TOOLS_SKIP_SIM") == "1":
        pytest.skip("CIRCUIT_TOOLS_SKIP_SIM=1")
    try:
        v = run.versions()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no simulator host: {e}")
    have = [k for k, x in v.items() if isinstance(x, dict) and x.get("available")]
    if not have:
        pytest.skip("no simulators installed")
    return have


def test_rc_crosscheck_all(available, tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCUIT_TOOLS_RECORD", "0")
    r = engine.crosscheck(rc_spec(), tmp_path)
    rows = {x["sim"]: x for x in r["results"]}
    assert rows["scikit-rf"]["status"] == "ok"
    for sim in available:
        assert rows[sim]["status"] == "ok", rows[sim]
        assert rows[sim]["vs_reference"]["value"] < 1e-6
    assert r["overall"] == "pass"


def test_dc_and_tran(available, tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCUIT_TOOLS_RECORD", "0")
    dc = {"name": "div", "components": [{"type": "vsource", "name": "V1", "nodes": ["a", "0"], "dc": 4},
                                        {"type": "R", "name": "R1", "nodes": ["a", "b"], "value": 1e3},
                                        {"type": "R", "name": "R2", "nodes": ["b", "0"], "value": 3e3}],
          "analyses": [{"type": "dc", "outputs": ["b"]}]}
    r = engine.crosscheck(dc, tmp_path / "dc")
    for row in r["results"]:
        if row["status"] == "ok":
            assert row["summary"]["v"]["b"] == pytest.approx(3.0, abs=1e-6)
    tr = {"name": "rc", "components": [
        {"type": "vsource", "name": "V1", "nodes": ["s", "0"], "pulse": {"v1": 0, "v2": 1, "td": 1e-10, "tr": 1e-12}},
        {"type": "R", "name": "R1", "nodes": ["s", "c"], "value": 50},
        {"type": "C", "name": "C1", "nodes": ["c", "0"], "value": 2e-12}],
        "analyses": [{"type": "tran", "stop": 1e-9, "step": 2e-12, "outputs": ["c"]}]}
    r = engine.crosscheck(tr, tmp_path / "tr")
    ok = [x for x in r["results"] if x["status"] == "ok"]
    assert len(ok) >= 1
    for row in ok:
        assert row["summary"]["final_v"]["c"] == pytest.approx(1 - np.exp(-(0.9e-9) / 1e-10), abs=5e-3)
