"""Translators from the neutral spec to each simulator's input, and the parsers for their outputs.

Each backend module provides:
  NAME, SUPPORT                      what it can simulate (analysis -> component types), with notes
  build(spec, analysis) -> Job       files to write and the shell commands to run in the case folder
  parse(case_dir, spec, analysis, job) -> result dict (kind sparam | dc | tran | hb)
Raising ``Unsupported`` means: this simulator cannot run this circuit/analysis correctly (and says why).
"""
from __future__ import annotations

from dataclasses import dataclass, field


class Unsupported(Exception):
    pass


@dataclass
class Job:
    sim: str
    files: dict = field(default_factory=dict)      # name -> text
    copies: dict = field(default_factory=dict)     # name -> source path (Touchstone files)
    commands: list = field(default_factory=list)   # shell commands; {exe} is the simulator executable
    meta: dict = field(default_factory=dict)       # whatever parse() needs
    notes: list = field(default_factory=list)


def backends() -> dict:
    from . import circuitrf, qucs, spice

    return {"ngspice": spice.NGSPICE, "xyce": spice.XYCE, "qucsator": qucs.QUCSATOR, "circuitrf": circuitrf.CIRCUITRF}


def support_matrix() -> dict:
    from ..reference import SUPPORT as REF

    out = {name: b.SUPPORT for name, b in backends().items()}
    out["scikit-rf"] = REF
    return out
