# AGENTS.md: using circuit-agent-tools from any coding agent

You are simulating an RF circuit. Use the `circuit-tools` CLI (or the `circuit_*` MCP tools of the same names;
`python -m circuit_tools.mcp_server`). Every tool takes one JSON object and prints one JSON object.
`circuit-tools list` shows the tools; `circuit-tools spec '{}'` shows the spec format and the coverage matrix.

## Workflow (do not skip steps 3 and 5)

1. **Spec.** Write the figure of merit (S11 in a band, cutoff, harmonic level, a waveform), its tolerance and
   the reference you will compare with (closed form, published data, a second simulator, EM data).
2. **Circuit.** Write the neutral JSON spec (or `import-qucs` a Qucs-S schematic). Ground is `"0"`. Write values
   as SI numbers or unambiguous strings (`"1.46pF"`, `"1Meg"`), give `f_ref` for lines in degrees, give
   `temp_c` when there are diodes.
3. **Lint.** `circuit-tools lint '{"spec": "x.json"}'`. Fix every error; fix or justify every warning. Read
   `SUP001`: it says which simulators can run the circuit. A cross-check needs at least two.
4. **Estimate / host.** `circuit-tools estimate` and `circuit-tools status`. Circuit runs take seconds; if an EM
   job holds the RAM, the guard refuses rather than competing.
5. **Cross-check.** `circuit-tools crosscheck '{"spec": "x.json", "case_dir": "runs/x"}'`. Reference: scikit-rf
   for S-parameters, else the first engine or the one you name (`"reference": "circuitrf"`). Read every row:
   `unsupported` rows did not run; a `fail` is a model or setup difference to explain, not noise.
6. **Report.** The figure of merit from each engine, max |dS| (or dB / volts) against the reference, which
   tools were refused and why, passivity/reciprocity, and "simulation only".

## Hard rules

- SPICE reads `1M` as milli, `1F` as femto, `MEG` as mega, and folds case (`Out` = `out`). Let the translators
  write the numbers; never paste raw SPICE values into the spec.
- Ports: numbered 1..N; mixed z0 is fine, but keep the per-port z0 with the data (we write Touchstone 2 with
  `[Reference]`); a Touchstone 1 option line holds one z0.
- Touchstone blocks: one node per port in file order, sweep inside the file's band, unused ports are OPEN (add
  a z0 resistor for a matched termination), check passivity of imported data.
- Xyce 7.10: no ideal lines, LTRA or Touchstone blocks in `.AC`/`.LIN` (silently wrong); YLIN only in `.HB`.
- ngspice: no Touchstone blocks, microstrip only as an RLGC line frozen at `f_ref`, HB only as transient + DFT.
- Coupled microstrip (`mcline`), open ends (`mopen`) and width steps (`mstep`) run in qucsator only. `mcline`
  nodes: 1 -> 2 is line 1, 3 -> 4 is line 2 in the same direction (3 beside 1).
- circuitRF: no transient; use `circuitrf serve --root DIR` (its own MCP) for schematics, layout and EM.
- qucsator's diode takes the cathode first (the translator handles it); its HB may not converge on hard drive:
  check `hb_converged`.
- Harmonic balance: raise `harmonics` until the harmonics you report stop moving (8 was 2.6-23 dB off for a
  1 V diode drive; 32 agreed to 0.02 dB).
- On Windows the simulators run in WSL from a generated `run.sh`; do not hand-write `wsl bash -c "..."` with `$`.

All 33 lint rules with their lessons: `circuit-tools gotchas '{}'` or
`skills/circuit-simulation/references/gotchas.md`. External tools and MCP servers we use or evaluated:
`EXTERNAL.yaml`.
