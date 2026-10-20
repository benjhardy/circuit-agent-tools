"""Qucs-S schematic -> neutral spec -> every simulator.

Takes the Wilkinson divider that ships with Qucs-S (examples/qucsator/RF/Power combiners/wilkinson.sch, GPL-2.0,
read in place from a Qucs-S source tree and not copied here), converts it with Qucs-S itself (headless,
``qucs-s -n``), imports the qucsator netlist and cross-checks it. The schematic: three ideal lines (50 ohm 100 mm,
two 70.7 ohm 75 mm arms), a 100 ohm isolation resistor, three 50 ohm ports, 0.1 MHz - 2 GHz.

    python examples/qucs_s_import/run_example.py [--sch /home/<you>/src/qucs_s/examples/qucsator/RF/Power\\ combiners/wilkinson.sch]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from circuit_tools import api, engine, lint  # noqa: E402

DEFAULT = "/home/ben/src/qucs_s/examples/qucsator/RF/Power combiners/wilkinson.sch"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sch", default=DEFAULT, help="schematic path where Qucs-S runs (a WSL path on Windows)")
    a = ap.parse_args()
    imp = api.import_qucs({"schematic": a.sch, "out_dir": str(HERE / "runs"), "out": str(HERE / "runs" / "spec.json")})
    if not imp["ok"]:
        print(json.dumps(imp, indent=1))
        sys.exit(1)
    spec = imp["spec"]
    li = lint.lint(spec)
    cc = engine.crosscheck(spec, HERE / "runs")
    out = {"qucs_s": imp["conversion"]["qucs_s_version"], "imported_components": len(spec["components"]),
           "not_imported": imp["unsupported"],
           "lint": [x["id"] + ": " + x["message"] for x in li["findings"] if x["severity"] != "info"],
           "simulators": {r["sim"]: {"status": r["status"], "wall_s": r.get("wall_s"), "reason": r.get("reason"),
                                     "max_dS_vs_scikit_rf": (r.get("vs_reference") or {}).get("value"),
                                     "s21_s31_db_range": {k: v for k, v in ((r.get("summary") or {}).get("db_min_max")
                                                                            or {}).items() if k in ("S21", "S31",
                                                                                                    "S32")}}
                          for r in cc["results"]}}
    (HERE / "results.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
