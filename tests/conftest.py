from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent / "data"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def data():
    return DATA


def rc_spec(**kw):
    s = {"name": "rc", "z0": 50,
         "components": [{"type": "port", "name": "P1", "nodes": ["in", "0"], "num": 1},
                        {"type": "R", "name": "R1", "nodes": ["in", "out"], "value": "100 Ohm"},
                        {"type": "C", "name": "C1", "nodes": ["out", "0"], "value": "1pF"},
                        {"type": "port", "name": "P2", "nodes": ["out", "0"], "num": 2}],
         "analyses": [{"type": "sparam", "start": "0.1GHz", "stop": "1GHz", "points": 11}]}
    s.update(kw)
    return s
