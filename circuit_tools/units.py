"""Values with SI prefixes and units, and the prefix traps between simulators.

The neutral spec takes plain numbers in SI base units (1.46e-12 for 1.46 pF) or strings with an SI prefix and an
optional unit ("1.46pF", "4.365 nH", "2.4GHz", "50 Ohm"). Strings follow SI case rules: "m" is milli, "M" is mega,
"meg"/"MEG" is also mega. The translators always write plain exponent numbers (or number + base unit), so no
simulator ever re-reads a prefix. ``prefix_traps`` lists strings that a SPICE simulator would read differently,
because SPICE prefixes are case-insensitive ("1M" = 1 milli; "1F" = 1 femto; "1meg" = 1e6).
"""
from __future__ import annotations

import math
import re

SI = {"y": 1e-24, "z": 1e-21, "a": 1e-18, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
      "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
# SPICE (ngspice, Xyce, LTspice): case-insensitive, letters after the scale factor are ignored
SPICE = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "mil": 25.4e-6, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
         "f": 1e-15, "a": 1e-18}
UNITS = ["ohms", "ohm", "Ohm", "Ω", "Hz", "hz", "F", "H", "V", "A", "s", "W", "S", "deg", "dB", "dBm", "m"]
LENGTH = {"m": 1.0, "cm": 1e-2, "mm": 1e-3, "um": 1e-6, "µm": 1e-6, "nm": 1e-9, "mil": 25.4e-6, "in": 25.4e-3,
          "inch": 25.4e-3}

_NUM = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*$")


class UnitError(ValueError):
    pass


def parse_value(x, unit: str | None = None) -> float:
    """Number or SI string -> float in base units. ``unit`` is the expected unit ("F", "H", "Ohm", "Hz", "s", "m")."""
    if isinstance(x, bool):
        raise UnitError(f"boolean is not a value: {x!r}")
    if isinstance(x, (int, float)):
        return float(x)
    if not isinstance(x, str):
        raise UnitError(f"not a value: {x!r}")
    m = _NUM.match(x)
    if not m:
        raise UnitError(f"cannot read a number from {x!r}")
    num, rest = float(m.group(1)), m.group(2).replace(" ", "")
    if not rest:
        return num
    if unit == "m" and rest in LENGTH:  # lengths: "10mm", "20mil", "1m" (one metre)
        return num * LENGTH[rest]
    if rest in SI:  # bare prefix: "1m" = milli, "1M" = mega
        return num * SI[rest]
    if rest.lower() == "meg":
        return num * 1e6
    for u in ([unit] if unit else []) + sorted(UNITS, key=len, reverse=True):
        if rest.endswith(u):
            pre = rest[: -len(u)]
            if pre == "":  # "1F" is one farad in SI (SPICE reads femto: see prefix_traps)
                return num
            if pre.lower() == "meg":
                return num * 1e6
            if pre in SI:
                return num * SI[pre]
    if rest in LENGTH:
        return num * LENGTH[rest]
    raise UnitError(f"unknown prefix/unit {rest!r} in {x!r}")


def spice_reading(s: str) -> float | None:
    """How a SPICE simulator reads the string ``s`` (case-insensitive scale factor, trailing letters ignored)."""
    m = _NUM.match(s) if isinstance(s, str) else None
    if not m:
        return None
    num, rest = float(m.group(1)), m.group(2).replace(" ", "").lower()
    for k in ("meg", "mil", "t", "g", "k", "m", "u", "n", "p", "f", "a"):
        if rest.startswith(k):
            return num * SPICE[k]
    return num


def prefix_traps(s, unit: str | None = None) -> str | None:
    """Explain why ``s`` is read differently by SPICE, or None. Only strings can be traps."""
    if not isinstance(s, str):
        return None
    try:
        ours = parse_value(s, unit)
    except UnitError:
        return None
    sp = spice_reading(s)
    if sp is None or ours == 0 or math.isclose(ours, sp, rel_tol=1e-9):
        return None
    return f"{s!r} is {ours:.4g} in SI but SPICE reads {sp:.4g} (case-insensitive prefixes: M = milli, MEG = mega, F = femto)"


def fmt(v: float, digits: int = 12) -> str:
    """Plain exponent/decimal string with no prefix: safe in every simulator."""
    if v == 0:
        return "0"
    return f"{v:.{digits}g}"


def eng(v: float, unit: str = "") -> str:
    """Human-readable engineering notation for reports (1.46 pF)."""
    if v == 0 or not math.isfinite(v):
        return f"{v:g} {unit}".strip()
    e = int(math.floor(math.log10(abs(v)) / 3) * 3)
    e = max(-18, min(12, e))
    pre = {-18: "a", -15: "f", -12: "p", -9: "n", -6: "u", -3: "m", 0: "", 3: "k", 6: "M", 9: "G", 12: "T"}[e]
    return f"{v / 10 ** e:.4g} {pre}{unit}".strip()
