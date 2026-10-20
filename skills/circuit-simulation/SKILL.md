---
name: circuit-simulation
description: Describe, lint, translate, run and cross-check RF circuits (S-parameters, DC, transient, harmonic balance) in ngspice, Xyce, Qucs-S/qucsator, circuitRF and scikit-rf from one neutral JSON spec with the circuit-agent-tools package. Use whenever a task involves a SPICE/Qucs/circuitRF netlist, an LC filter or matching network, microstrip or ideal lines in a circuit simulator, a Touchstone S-block (EM data, vendor models) in a circuit, harmonic balance of a diode/transistor, or judging whether a circuit-simulation result can be trusted.
---

# Circuit simulation workflow (circuit-agent-tools)

Circuit simulators fail quietly: a prefix read as milli instead of mega, two nodes merged by SPICE's case
folding, an ideal line that Xyce's AC analysis treats as a wire, a Touchstone file renormalised to the wrong
z0, a harmonic-balance run with too few harmonics. The cure is cheap: lint the circuit, then run it in a second
(and third) simulator and compare. Every tool takes one JSON object and returns one
(`circuit-tools <tool> '<json>'`, or the `circuit_*` MCP tools).

## 1. Spec and reference

Write down the figure of merit, its tolerance, and the reference: a closed form (L-match, quarter-wave, RC/RLC
step), the scikit-rf solver (automatic for S-parameters), a second engine, or measured/EM data.

## 2. The circuit as a neutral spec

`circuit-tools spec '{}'` prints the format and the coverage matrix. Recipes:
[references/spec-recipes.md](references/spec-recipes.md). From a Qucs-S schematic:
`circuit-tools import-qucs '{"schematic": "/path/in/wsl/x.sch", "out": "x.json"}'` (it lists what it could not
import: tees, bends, equations, other devices).

## 3. Lint

```
circuit-tools lint '{"spec": "x.json"}'
```

Fix every `error`; fix or justify every `warning`. `SUP001` lists which simulators will run each analysis and
why the others are refused; `SWP001` re-solves the circuit on a 4x finer grid to catch sweeps that step over a
resonance. Full list with lessons: [references/gotchas.md](references/gotchas.md).

## 4. Estimate and host

```
circuit-tools estimate '{"spec": "x.json"}'
circuit-tools status '{}'
```

Runs take seconds. The guard refuses when the host is short of RAM (another agent's EM job); runs are always
`nice 10`, single-threaded and sequential. See [references/running.md](references/running.md).

## 5. Cross-check

```
circuit-tools crosscheck '{"spec": "x.json", "case_dir": "runs/x"}'
circuit-tools crosscheck '{"spec": "hb.json", "case_dir": "runs/hb", "reference": "circuitrf", "tol": {"hb_db": 0.05}}'
```

Each row: `ok` with `vs_reference` (max |dS|, waveform error / swing, harmonic dB, DC volts) and checks
(passivity, reciprocity, `hb_converged`, log errors), or `unsupported` with the reason. Typical agreement on
lumped circuits is 1e-9 (ngspice prints 9 digits) to 1e-15; microstrip models differ at 1e-3..3e-2 (see
TLN002/TLN004); HB engines agree to ~0.01 dB once the harmonic count is enough. One run in one tool:
`circuit-tools simulate '{"spec": ..., "sim": "qucsator", "case_dir": ...}'`.

## 6. Report

The figure of merit per engine, the reference and the max difference, refused engines and why, the checks, the
cost (seconds), and "simulation only". Every run is recorded in rflab (`rflab.run/1`, kind `circuit`; the
cross-check record is the parent of its simulator runs).

## Coverage at a glance

| | S-param | DC | tran | HB | lines | Touchstone |
|---|---|---|---|---|---|---|
| scikit-rf | reference | - | - | - | ideal, MLine | yes |
| ngspice | AC drive | op | yes | tran + DFT | T, LTRA (frozen RLGC) | no |
| Xyce | .LIN | .DC | yes | .HB | T in tran only | HB only |
| qucsator | .SP | .DC | .TR | .HB (weak) | TLIN, MLIN | SPfile |
| circuitRF | sparam | dc | no | hb | TLIN, MLIN | SnP |

Details and measured caveats: [references/coverage.md](references/coverage.md). For circuitRF-native design
(schematics, layout, EM, loadpull) use circuitRF's own MCP server: `circuitrf serve --root DIR`.
