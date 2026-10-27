# circuit-agent-tools

Tools that let AI coding agents (Claude Code, Codex, Cursor, or a plain script) describe an RF circuit once and
lint, translate, run and cross-check it in **ngspice, Xyce, Qucs-S/qucsator, circuitRF and scikit-rf**. Part of
the agent.rf open autonomous RF lab, next to [palace-agent-tools](../palace-agent-tools) (EM) and
[rflab-core](../rflab-core) (run records).

Circuit simulators rarely fail loudly. A capacitor written `1F` is a femtofarad in SPICE and a farad elsewhere;
`Out` and `out` are one node in ngspice and two in qucsator; a Touchstone 1 file with mixed port impedances is
renormalised on the next read; Xyce 7.10 treats an ideal line as a wire in `.AC`; qucsator's diode takes the
cathode first; a harmonic-balance run with too few harmonics is off by 20 dB at the 8th harmonic and still
"converges". This package turns those traps into lint rules and makes the cheapest check, *run it in a second
simulator*, one call:

| step | tool | what it does |
|---|---|---|
| describe | `spec`, `normalize`, `import-qucs` | one neutral JSON spec (R, L, C, ideal and microstrip lines, Touchstone S-blocks, ports, sources, diodes; S-parameter, DC, transient, harmonic-balance analyses); import Qucs-S schematics through Qucs-S itself |
| lint | `lint`, `lint-rules`, `gotchas` | 33 rules, each with the lesson and its source: topology, SPICE case and prefix traps, ports and z0, sweep resolution (checked with the scikit-rf reference), Touchstone blocks (port count, band, z0, passivity, reciprocity, open ports), line-model limits, transient/HB settings, simulator coverage |
| translate | `translate` | ngspice decks, Xyce netlists, qucsator (Qucs-S) netlists, circuitRF `.cnl` (+ `.ctech`) |
| cost, guard | `estimate`, `status` | wall time and RAM per simulator; refuses to start when the host is short of RAM; runs at `nice 10`, one at a time, with timeouts |
| run | `simulate` | one simulator (or the scikit-rf reference): log scanning, Touchstone/CSV/JSON output, passivity and reciprocity checks, an `rflab.run/1` record |
| cross-check | `crosscheck` | lint, then every simulator that supports the circuit, each compared with the reference: max \|ΔS\|, waveform difference relative to swing, harmonic dB differences, DC volts |
| inspect | `touchstone-check`, `doctor` | ports, z0, band, passivity, reciprocity of a Touchstone file; which simulators are installed |

It ships as a Python package with a CLI, a dependency-free MCP server (stdio), a Claude Code skill and an
[AGENTS.md](AGENTS.md). External tools we use or evaluated are listed in [EXTERNAL.yaml](EXTERNAL.yaml).

**What it does not do.** It does not replace circuitRF's own MCP server (`circuitrf serve --root DIR`: schematics,
layout, EM, loadpull, yield): use that directly for circuitRF work; this package only runs circuitRF's CLI verbs as
one cross-check backend. General SPICE design servers already exist (SPICEBridge, ltspice-mcp: GPL-3.0, run them
as separate processes) and mcp-ltspice-qucs covers filter synthesis (AGPL-3.0). What was missing, and is here: a
neutral RF netlist, the translators, cross-simulator comparison, RF lint rules, circuitRF and Touchstone handling.

**Status:** 0.1.0, simulation only. Measured with ngspice 46 (dev), Xyce 7.10 (dev), qucsator_rf 1.0.7, Qucs-S
26.1.1, circuitRF 1.0.9 and scikit-rf 2.2 on an 8-thread desktop (WSL2), while another job had the CPU fully loaded.

## Install (user space, no sudo)

The Python package (Windows or Linux):

```
python -m venv .venv
.venv\Scripts\activate                      # Linux: . .venv/bin/activate
pip install -e ".[dev]"                     # numpy + scikit-rf; dev adds pytest, scipy
pip install -e ../rflab-core                # optional: run records go to rflab (else a JSON-lines drop-box)
```

The simulators, inside WSL Ubuntu 24.04 (or any Linux), with [micromamba](https://micro.mamba.pm):

```
# ngspice 46-dev and Xyce 7.10-dev (conda-forge has ngspice 41 and no Xyce; the unic-cass channel has both)
micromamba create -n circuit-sim -c unic-cass -c conda-forge xyce ngspice fftw python=3.12 \
    cmake make flex bison gperf cxx-compiler c-compiler git
# qucsator_rf (Qucs-S's RF kernel), built from source into ~/.local/opt/qucsator_rf
git clone --depth 1 https://github.com/ra3xdh/qucsator_rf ~/src/qucsator_rf && cd ~/src/qucsator_rf
printf '#!/bin/sh\nif [ $# -eq 0 ]; then exec sed "s/\\r$//"; fi\nfor f; do sed -i "s/\\r$//" "$f"; done\n' \
    > $CONDA_PREFIX/bin/dos2unix && chmod +x $CONDA_PREFIX/bin/dos2unix      # the build calls dos2unix
mkdir build && cd build && cmake .. -DCMAKE_INSTALL_PREFIX=$HOME/.local/opt/qucsator_rf -DBISON_DIR=$CONDA_PREFIX/bin \
    && nice make -j2 && make install
# Qucs-S itself (optional: schematic import), against conda-forge Qt 6
micromamba create -n qucs-build -c conda-forge qt6-main qt6-charts cmake make cxx-compiler c-compiler flex bison gperf git libglu
git clone --depth 1 https://github.com/ra3xdh/qucs_s ~/src/qucs_s && cd ~/src/qucs_s && mkdir build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=$HOME/.local/opt/qucs-s && nice make -j2 && make install
# circuitRF: its own installer (.tar.gz + install.sh) puts ~/.local/bin/circuitrf
```

The runner finds them at those paths (or on `PATH`); override with `CIRCUIT_TOOLS_NGSPICE`, `CIRCUIT_TOOLS_XYCE`,
`CIRCUIT_TOOLS_QUCSATOR`, `CIRCUIT_TOOLS_CIRCUITRF`, `CIRCUIT_TOOLS_QUCS_S`; `CIRCUIT_TOOLS_BACKEND=wsl|native`,
`CIRCUIT_TOOLS_DISTRO`. `circuit-tools doctor '{}'` shows what it found.

## Quick start (human)

```
python examples/chebyshev_lpf/run_example.py          # 11 s: ideal + EM 7-port LPF in 5 tools vs port-tuning-palace
python examples/lsection_match/run_example.py         # 4 s: 50 -> 200 ohm L-match, mixed port z0, vs closed form
python examples/quarter_wave_microstrip/run_example.py  # 5 s: lossy microstrip QWT, model differences
python examples/rlc_transient/run_example.py          # 9 s: RLC step and line reflection vs closed form
python examples/diode_hb/run_example.py               # 17 s: diode HB in circuitRF, Xyce, qucsator, ngspice
python examples/qucs_s_import/run_example.py          # 5 s: Qucs-S schematic -> spec -> all tools
```

Or with the CLI (one JSON object in, one out; inline, `@file.json` or `-` for stdin):

```
circuit-tools spec '{}'                                     # the spec format, an example, the coverage matrix
circuit-tools lint '{"spec": "lpf.json"}'
circuit-tools estimate '{"spec": "lpf.json"}'
circuit-tools crosscheck '{"spec": "lpf.json", "case_dir": "runs/lpf"}'
circuit-tools simulate '{"spec": "lpf.json", "sim": "qucsator", "case_dir": "runs/lpf"}'
circuit-tools translate '{"spec": "lpf.json", "sim": "ngspice", "out_dir": "decks"}'
circuit-tools import-qucs '{"schematic": "/home/me/designs/wilkinson.sch", "out": "wilkinson.json"}'
circuit-tools touchstone-check '{"file": "em_7port.s7p"}'
```

Exit code 1 means the tool reported a problem (`ok: false` or `overall: fail`), 2 means bad input.

A spec:

```json
{"name": "lmatch", "z0": 50,
 "components": [
   {"type": "port", "name": "P1", "nodes": ["in", "0"], "num": 1, "z0": 50},
   {"type": "L", "name": "Ls", "nodes": ["in", "out"], "value": "5.743nH"},
   {"type": "C", "name": "Cp", "nodes": ["out", "0"], "value": "0.5743pF"},
   {"type": "port", "name": "P2", "nodes": ["out", "0"], "num": 2, "z0": 200}],
 "analyses": [{"type": "sparam", "start": "1.4GHz", "stop": "3.4GHz", "points": 201}]}
```

Other parts: `tline` (z0 + `length_deg` at `f_ref`, or `length` + `eeff`), `mline` (w, l, substrate; substrates
`{er, h, t, tand, sigma, rough}`), `mcline` (coupled pair: w, s, l, substrate; nodes 1-2 line 1, 3-4 line 2 in the
same direction, so 3 is beside 1), `mopen` (w, substrate; one node), `mstep` (w1, w2, substrate), `sblock` (Touchstone file, one node per port, optional `ref`), `vsource` /
`isource` (`dc`, `ac`, `pulse {v1, v2, td, tr, tf, pw, per}`, `sin {offset, amp, freq, phase_deg}`, cosine
reference), `diode` (SPICE level-1 `is, n, rs, cj0, vj, m, tt, bv, ibv, fc`). Analyses: `sparam`, `dc`, `tran
{stop, step, outputs}`, `hb {f0, harmonics, outputs}`. Values are SI numbers or SI strings (`M` = mega,
`m` = milli, `meg` = mega); the translators always write plain numbers.

## Quick start (agents)

**MCP.** `claude mcp add circuit-tools -- python -m circuit_tools.mcp_server` (or the repository's `.mcp.json`).
Tools: `circuit_spec`, `circuit_lint`, `circuit_lint_rules`, `circuit_gotchas`, `circuit_normalize`,
`circuit_translate`, `circuit_estimate`, `circuit_status`, `circuit_simulate`, `circuit_crosscheck`,
`circuit_import_qucs`, `circuit_touchstone_check`, `circuit_doctor`. Results come back as JSON text and
`structuredContent`. For native circuitRF work add circuitRF's own server too
(`circuitrf serve --root DIR --print-config`).

**Claude Code skill.** Copy `skills/circuit-simulation/` into `~/.claude/skills/`. It teaches spec -> lint ->
estimate -> cross-check -> report, with the gotchas, coverage matrix and run rules in `references/`.

**Other agents.** [AGENTS.md](AGENTS.md).

## Simulator coverage

What each backend runs (measured; `circuit-tools spec` prints the live matrix). "-" = refused by the translator
with a reason, so a cross-check never silently compares fewer tools than you think.

| | scikit-rf | ngspice | Xyce | qucsator (Qucs-S) | circuitRF |
|---|---|---|---|---|---|
| R, L, C, ports, sources | S-param | all | all | all | S-param, DC, HB |
| ideal line | S-param | all | **tran only** (wire in .AC) | S-param, tran | S-param |
| microstrip | MLine (HJ + KJ) | LTRA frozen at f_ref | - | MLIN (HJ + KJ) | MLIN (own t-correction) |
| coupled microstrip, open end, width step | - | - | - | MCOUPLED (KJ), MOPEN (KJK), MSTEP (Gupta-Garg) | - |
| Touchstone block | S-param (no extrapolation) | - | **HB only** (open in .AC) | S-param, DC (open) | S-param, DC, HB |
| diode | - | DC, tran, HB* | DC, tran, HB | DC, tran, HB | DC, HB |
| S-parameters | Circuit | AC drive per port | .LIN | .SP | sparam |
| DC | - | op | .DC | .DC | dc |
| transient | closed-form helpers | tran | .TRAN | .TR | - |
| harmonic balance | - | *tran + exact DFT | .HB | .HB (weak on hard drive) | hb |
| schematic format | - | - | - | **Qucs-S .sch import** (via `qucs-s -n`) | .csch via its own MCP |

## Verified examples (all simulator time on a fully loaded 8-thread desktop)

| example | reference | agreement |
|---|---|---|
| [5th-order 0.1 dB Chebyshev LPF](examples/chebyshev_lpf), ideal values, 0.1-8 GHz, 317 pts | scikit-rf; port-tuning-palace `ideal.s2p` | max \|ΔS\|: ngspice 4.9e-9, Xyce 7.0e-10, circuitRF 6.9e-11, qucsator 7.1e-12; f-3dB 2.836 GHz, worst passband S11 -16.43 dB, stopband S21 -25.81 dB in every tool (project: same) |
| same filter on the Palace EM 7-port (S-block), schematic values ("as drawn") | project `pred_as_drawn.s2p` | qucsator 7.2e-12, circuitRF 7.1e-11 (vs scikit-rf and vs project); f-3dB 2.2107 GHz, passband S21 -10.99 dB as in the project. ngspice/Xyce refused (no Touchstone in .AC) |
| EM 7-port with the port-tuned E24 values | project `pred_e24.s2p` | qucsator 2.8e-15, circuitRF 6.8e-11; f-3dB 2.9121 GHz, S11 -19.56 dB, stopband -31.84 dB = project |
| [L-section match](examples/lsection_match) 50 -> 200 ohm, 2.4 GHz | closed form (power waves, mixed z0) | scikit-rf 6.8e-13, qucsator 7.8e-13, circuitRF 7.0e-11, Xyce 6.6e-10, ngspice 5.0e-9; S11 < -200 dB at 2.4 GHz everywhere |
| [quarter-wave transformer](examples/quarter_wave_microstrip), 70.7 ohm lossy microstrip on RO4003C, 50 -> 100 ohm | scikit-rf MLine | qucsator 1.2e-3 (S11 min at 2.400 GHz, 0.055 dB loss); ngspice (RLGC at 2.4 GHz) 6.8e-3, min at 2.425 GHz; **circuitRF 3.1e-2**, min at 2.375 GHz, 0.074 dB loss: its thick-strip model gives eeff +2.7 %, Z0 -1.2 %, conductor loss +47 % (agrees for t -> 0; lint TLN004). Xyce refused |
| [RLC step](examples/rlc_transient) 10 ohm / 10 nH / 1 pF (Q = 10), 20 ps edge | closed form | max error / swing: qucsator 3.6e-5, ngspice 1.3e-4, Xyce 1.3e-4 |
| step into a 100 ps ideal line, open end | closed form | Xyce and qucsator exact (< 1e-6 V); ngspice 2.5 % of the swing at the ramp corners only (rms 0.14 %) |
| [diode HB](examples/diode_hb), 0.3 V, 100 MHz, 8 harmonics | circuitRF hb | harmonics 0-8 (to -100 dBV): Xyce 0.003 dB, qucsator 0.005 dB, ngspice tran+DFT 0.011 dB |
| diode HB, 1 V, 8 harmonics | circuitRF hb | Xyce 2.6 dB, ngspice 23 dB off at the 7th-8th harmonic: **8 harmonics are too few** (lint HB001); qucsator did not converge |
| diode HB, 1 V, 32 harmonics | circuitRF hb | harmonics 0-8: Xyce 0.011 dB, ngspice 0.019 dB; qucsator still not converged |
| diode DC, 5 V through 1 kohm | circuitRF dc | qucsator 0.6 uV, ngspice 4 uV, Xyce 13 uV |
| [Qucs-S schematic import](examples/qucs_s_import): Qucs-S's own Wilkinson example | scikit-rf | qucsator 1.2e-15, circuitRF 7.0e-11, ngspice 1.3e-8 (Xyce refused: ideal lines) |

Every S-parameter run also passed passivity (max eigenvalue of S^H S <= 1 + 1e-6) and reciprocity. The scripts
write `results.json` next to themselves; each simulator run and each cross-check is an `rflab.run/1` record.

## Gotchas

All 33 rules with their lessons: [skills/circuit-simulation/references/gotchas.md](skills/circuit-simulation/references/gotchas.md)
(generated from the code). The ones found while building this:

| gotcha | what happens | rule |
|---|---|---|
| Xyce 7.10 `.AC`/`.LIN` with T, LTRA, YLIN | ideal line and LTRA act as a wire, a Touchstone block as an open circuit, no warning (YLIN works in `.HB`, T in `.TRAN`) | XYC001 |
| qucsator diode pin order | `Diode:D cathode anode`, the reverse of SPICE; harmonic magnitudes cannot show it (the waveform is mirrored), DC can | translator, `compare_harmonics` DC-sign check |
| HB harmonic count | 8 harmonics for a 1 V diode drive: 2.6-23 dB error at the top harmonics; 32: < 0.02 dB between three engines | HB001 |
| qucsator HB | did not converge on the hard-driven diode at all, but still writes a plausible spectrum | HB001, `hb_converged` check |
| circuitRF MLIN with thick copper | eeff +2.7 %, Z0 -1.2 %, conductor loss +47 % vs Hammerstad-Jensen (t/h = 0.07) | TLN004 |
| SPICE prefixes | `1M` = milli, `1F` = femto, `1MEG` = mega | UNI001 |
| SPICE case folding | `Out`/`out`, `L1`/`l1` merge in SPICE only | TOP005 |
| default temperature | circuitRF and qucsator 26.85 C, SPICE 27 C | UNI003 (spec `temp_c` is written everywhere) |
| mixed port z0 in Touchstone 1 | one z0 on the option line: data renormalised on re-read (circuitRF adds a comment; `skrf.write_touchstone(r_ref=...)` renormalises) | PRT001; we write Touchstone 2 with `[Reference]` |
| S-block extrapolation | circuitRF holds the edge, scikit-rf refuses, others differ | SBK002 |
| unconnected S-block port | it is open, not matched | SBK006 |
| microstrip in SPICE | RLGC frozen at one frequency; ngspice's LTRA has no G | TLN002 |
| scikit-rf MLine defaults | wideband Debye dielectric and 0.15 um roughness by default | set explicitly in `microstrip.py` |
| circuitRF CLI | `--help` opens the GUI and hangs; HB `Tone="100 MHz"` refused (use a variable) | runner, translator |
| SPICE `BV=0` | breakdown at 0 V, not "no breakdown" | translator omits it |
| WSL from Git Bash | `/mnt/c/...` rewritten to `C:/Program Files/Git/mnt/c/...` (MSYS_NO_PATHCONV=1); `wsl -- bash -c "$x"` expands early | runner always writes `run.sh` |

## Cost

Circuit runs are cheap; start-up dominates. Measured per cross-check of a 2-port with ~300 frequencies, under a
full CPU load from another job: scikit-rf 0.01-0.04 s, ngspice 0.2-0.8 s (one deck per port), qucsator
0.2-1.2 s, Xyce 0.3-2.7 s, circuitRF 0.6-1.7 s (.NET start-up); peak RAM < 0.4 GB each. `estimate` uses those
constants; the guard refuses when less than 1 GB + the estimate is free, and every run is `nice 10`,
single-threaded, sequential, with a timeout (default 300 s).

## Limits

- Linear S-parameter references come from scikit-rf; transient and HB have no independent reference except the
  closed forms in the examples and the agreement between engines.
- Ground-referenced ports and S-blocks only in the scikit-rf reference; no mutual inductors, transistors,
  coupled lines, tees/bends/steps, noise, or multi-tone HB yet; one substrate per circuitRF netlist.
- ngspice has no Touchstone support here (a vector-fitted SPICE model would be the route) and no HB engine
  (transient + DFT instead); Xyce is refused for distributed elements in `.AC` because the build we measured
  gets them wrong.
- Qucs-S: schematics are imported (through Qucs-S's own converter), not written; components outside R, L, C,
  TLIN, MLIN, SPfile, Pac, Vdc, Diode are listed as not imported.
- The lint rules catch known traps; agreement between simulators shows the models agree, not that the hardware
  will (all examples are simulation only).
- Not affiliated with the ngspice, Xyce, Qucs-S, circuitRF or scikit-rf projects.

## Development

```
pip install -e ".[dev]"
pytest                          # 77 tests, ~15 s; tests/test_simulators.py runs the real simulators
                                # (CIRCUIT_TOOLS_SKIP_SIM=1 skips them)
python tools/gen_gotchas.py     # regenerate the gotchas reference after editing rules
```

## Licence

Apache License 2.0. The simulators are separate programs run as subprocesses under their own licences (ngspice
BSD-3-Clause, Xyce GPL-3.0, qucsator_rf and Qucs-S GPL-2.0, circuitRF MIT); nothing of theirs is vendored.
scikit-rf (BSD-3-Clause) is a dependency. The EM 7-port and reference Touchstone files in
`examples/chebyshev_lpf/data/` come from the author's port-tuning-palace project. See [NOTICE](NOTICE).
