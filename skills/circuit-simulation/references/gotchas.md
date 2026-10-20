# Circuit-simulation gotchas (generated from circuit_tools.lint; do not edit by hand)

Each rule below is checked by `circuit-tools lint`. Rules marked *reference* run the scikit-rf reference solver. Measured behaviour is for ngspice 46 (dev), Xyce 7.10 (dev), qucsator_rf 1.0.7, circuitRF 1.0.9 and scikit-rf 2.2.

| id | gotcha |
|---|---|
| TOP001 | Floating (dangling) node |
| TOP002 | No ground reference |
| TOP003 | Node without a DC path to ground |
| TOP004 | Loop of voltage sources or inductors |
| TOP005 | Names that collide in case-insensitive SPICE |
| TOP006 | Ground aliases |
| UNI001 | SPICE prefix trap: M, MEG, F |
| UNI002 | Implausible component value (unit slip) |
| UNI003 | Default temperature differs between simulators |
| PRT001 | Port reference impedances differ |
| PRT002 | Port numbering |
| PRT003 | Port with a floating reference |
| PRT004 | Source in an S-parameter circuit |
| SWP001 | Sweep too coarse for the circuit's resonances |
| SWP002 | Sweep starting at 0 Hz |
| SWP003 | Electrical length per frequency step |
| SBK001 | S-block port count vs connected nodes |
| SBK002 | S-block data does not cover the sweep (extrapolation) |
| SBK003 | S-block reference impedance |
| SBK004 | Passivity of imported S-parameters |
| SBK005 | Reciprocity of imported S-parameters |
| SBK006 | Unconnected S-block port (open, not matched) |
| TLN001 | Microstrip model limits |
| TLN002 | Microstrip in SPICE is a frozen RLGC line |
| TLN003 | Ideal line given as electrical length |
| TLN004 | Thick-strip microstrip correction differs between tools |
| XYC001 | Xyce frequency-domain distributed and data elements |
| TRN001 | Transient output step vs circuit time constants |
| TRN002 | Edges faster than the time step |
| HB001 | Harmonic balance: harmonics and drive |
| SUP001 | Simulator coverage for this circuit |

## TOP001: Floating (dangling) node

A node touched by one terminal only is an open stub. SPICE accepts it in AC but aborts the DC operating point ('singular matrix') when nothing ties it to ground; in RF circuits it is usually a typo in a node name.

Source: ngspice manual 2.1 (every node needs a DC path to ground); SPICE practice.

## TOP002: No ground reference

Every simulator needs a reference node: SPICE '0', qucsator 'gnd', circuitRF '0'. Without one the matrix is singular; with a misspelled ground ('GND ' or 'ground1') the circuit floats and AC results depend on the solver's gmin.

Source: ngspice / Xyce reference guides.

## TOP003: Node without a DC path to ground

Nodes connected only through capacitors (or a series capacitor chain) have no DC solution: ngspice and Xyce stop with a singular matrix in .op/.tran. S-parameter runs still work, which hides the problem until a transient or harmonic-balance run.

Source: SPICE practice; ngspice manual (rshunt option).

## TOP004: Loop of voltage sources or inductors

Two ideal voltage sources in parallel, or a loop of inductors and voltage sources, has no unique DC solution: SPICE aborts with a singular matrix ('voltage source loop').

Source: ngspice manual; Xyce users guide.

## TOP005: Names that collide in case-insensitive SPICE

SPICE (ngspice, Xyce) folds node and element names to one case: 'Out' and 'out' are one node there but two in qucsator, circuitRF and scikit-rf, so the simulators solve different circuits and only a cross-check shows it.

Source: ngspice manual 2.1 (names are case-insensitive).

## TOP006: Ground aliases

ngspice and Xyce only treat '0' as ground (ngspice also maps 'gnd'); qucsator uses 'gnd'; a node called 'GND' or 'ground' is an ordinary floating node in some tools. The spec maps the aliases to '0' and the translators write each tool's own ground name.

Source: ngspice manual; Qucs netlist format.

## UNI001: SPICE prefix trap: M, MEG, F

SPICE scale factors are case-insensitive and ignore trailing letters: '1M' is 1 milli (not mega), '1MEG' is mega, '1F' is 1 femto (not one farad), '10mF' is 10 milli. A value copied from a SPICE deck into another tool (or the reverse) changes by 1e9 or 1e15 without an error.

Source: ngspice manual 2.3 (scale factors).

## UNI002: Implausible component value (unit slip)

RF parts live in narrow ranges: C from ~10 fF to ~10 uF, L from ~0.1 nH to ~100 uH. '1.46' for a capacitor is 1.46 F (the 'pF' was dropped), '4.365e-6' for a 4.365 nH inductor is 1000x too large.

Source: practice (port-tuning filter values).

## UNI003: Default temperature differs between simulators

circuitRF and qucsator default device temperatures to 26.85 C (300 K), ngspice and Xyce to 27 C. Diode and BJT currents depend on kT/q, so the same model differs by ~0.05 % in Vt before anything else; the translators write the spec's temp_c everywhere.

Source: circuitRF component reference (Temp 26.85); ngspice manual (TNOM 27).

## PRT001: Port reference impedances differ

S-parameters are defined against each port's z0. Mixed z0 (50 / 100 ohm) is legal, but a Touchstone v1 file holds one z0 on its option line: circuitRF writes port 1's and a comment, scikit-rf writes one value, so a re-read file is silently renormalised to the wrong reference.

Source: Touchstone 1.1 spec; circuitRF CLI reference ('Ports with different reference impedances').

## PRT002: Port numbering

Port numbers must be 1..N without gaps or duplicates; otherwise simulators renumber them differently and S21 of one tool is S31 of another.

Source: Qucs/circuitRF/Xyce port conventions.

## PRT003: Port with a floating reference

A port between two non-ground nodes is a differential port: SPICE and qucsator accept it, scikit-rf's Circuit and most Touchstone consumers assume a common ground, so the reference solver cannot check it.

Source: skrf.circuit documentation.

## PRT004: Source in an S-parameter circuit

Ports are the sources of an S-parameter run. An extra ideal voltage source shorts its node for small signals (and is dropped by some translators); bias it through a choke or leave it out.

Source: practice.

## SWP001: Sweep too coarse for the circuit's resonances *(reference)*

A sweep that steps over a resonance or notch reports a smooth, wrong curve and misses the worst-case point of a spec. Checked by re-solving the linear circuit on a 4x finer grid (scikit-rf) and comparing with the coarse sweep interpolated: more than 0.02 in |S| means the grid is too coarse.

Source: port-tuning (-3 dB and stop-band edges); practice.

## SWP002: Sweep starting at 0 Hz

At f = 0 inductors are shorts and capacitors opens: S-parameters exist but Touchstone readers, transmission-line models (Kirschning-Jansen) and vector fitting misbehave, and some tools refuse 0 Hz in a log sweep. Start at a small positive frequency.

Source: practice.

## SWP003: Electrical length per frequency step

Distributed elements rotate the phase quickly: with more than ~20 degrees of line phase per step a plot connects points across a ripple and interpolated Touchstone data are wrong between points.

Source: practice.

## SBK001: S-block port count vs connected nodes

An N-port Touchstone block needs N signal nodes in file port order. A .s7p connected with 6 nodes is refused by circuitRF and qucsator, but a wrong order (ports 3 and 4 swapped) is accepted by every tool.

Source: Touchstone spec; circuitRF SnP reference.

## SBK002: S-block data does not cover the sweep (extrapolation)

Outside the file's band each tool extrapolates differently: circuitRF holds the nearest edge, qucsator extrapolates its interpolator, scikit-rf refuses, Xyce's YLIN holds the edge. Results then differ between simulators for a reason that has nothing to do with the circuit.

Source: circuitRF SnP reference (ExtrapMode NearestEdge); skrf.Network.interpolate.

## SBK003: S-block reference impedance

A Touchstone file carries its own reference impedance (option line, or [Reference] per port in v2). Every tool renormalises the block to the circuit; a hand-edited option line that says 'R 50' over data measured at 75 ohm moves every pole of the filter without an error.

Source: Touchstone 1.1/2.0 spec.

## SBK004: Passivity of imported S-parameters

An EM or measured S-matrix with eigenvalues of S^H S above 1 creates energy: harmless in a linear sweep, but it makes transient and harmonic-balance runs unstable and hides real loss. Seen with coarse meshes, unresolved vias and port normalisation errors.

Source: palace-agent-tools passivity check; Palace via lesson (S22 = +0.8 dB).

## SBK005: Reciprocity of imported S-parameters

Passive structures without ferrites are reciprocal; |S_ij - S_ji| well above the solver noise means the file has transposed data (Touchstone v1 2-port order is S11 S21 S12 S22, unlike the row order of N >= 3) or inconsistent port normalisation.

Source: Touchstone 1.1 spec (2-port data order).

## SBK006: Unconnected S-block port (open, not matched)

A block port whose node touches nothing else is left OPEN, not terminated in z0. To mimic a matched load (what the S-parameters assume) add a z0 resistor to ground; internal port-tuning ports must be terminated with their component.

Source: port-tuning-palace (7-port EM matrix, internal ports).

## TLN001: Microstrip model limits

Closed-form microstrip models are fits: Hammerstad-Jensen for 0.01 <= w/h <= 100, Kirschning-Jansen dispersion for w/h 0.1-100, er 1-20 and h/lambda0 <= 0.13. Above the TM1 surface-wave onset or near the strip's transverse resonance the line radiates and couples, and no circuit model is right: use EM.

Source: Hammerstad & Jensen 1980; Kirschning & Jansen 1982; Pozar ch. 3.

## TLN002: Microstrip in SPICE is a frozen RLGC line

ngspice has no microstrip model: the translator writes an LTRA line whose R, L, C match Z0, eeff and the loss at one frequency (f_ref, default mid-band). Dispersion (eeff rising with f), sqrt(f) skin loss and f-proportional dielectric loss are missing, so phase and loss drift away from f_ref; ngspice's LTRA also has no G, so dielectric loss is folded into R.

Source: ngspice manual (LTRA); this package's quarter-wave example.

## TLN003: Ideal line given as electrical length

length_deg is defined at f_ref only. qucsator's TLIN takes a physical length (air), circuitRF accepts either, SPICE takes a delay: the translators convert, but a forgotten f_ref (default 1 GHz) puts the quarter wave at the wrong frequency in every tool at once.

Source: practice.

## TLN004: Thick-strip microstrip correction differs between tools

For a 0.579 mm, 70.7 ohm line on 0.508 mm RO4003C with 35 um copper (t/h = 0.07), scikit-rf and qucsator (Hammerstad-Jensen thickness correction) gave Z0 70.71 ohm, eeff 2.5986, 1.87 dB/m conductor loss; circuitRF 1.0.9 gave 69.83 ohm, eeff 2.6675 (+2.7 %) and 2.75 dB/m. With t = 1 nm all three agree (73.1 ohm, 2.6561), so the difference is the thickness/conductor-loss model, not the substrate. A quarter wave moves by ~1.3 % in frequency.

Source: this package (quarter_wave_microstrip example, 2026-10-19).

## XYC001: Xyce frequency-domain distributed and data elements

Measured with Xyce 7.10 (conda unic-cass build): in .AC/.LIN the lossless T line and LTRA behave as a plain wire and a YLIN Touchstone block as an open circuit, with no warning. YLIN works in .HB; T works in .TRAN. The Xyce translator refuses these combinations.

Source: this package (measured, 2026-10-19).

## TRN001: Transient output step vs circuit time constants

An output step coarser than ~1/10 of the fastest time constant (RC, L/R, 1/omega0) or of a line delay under-samples the edge; simulators then interpolate their internal points differently and the waveforms disagree at the edges. The translators also cap the internal step at the output step.

Source: SPICE practice.

## TRN002: Edges faster than the time step

A pulse with tr = 0 is an ideal step: SPICE replaces it with its own minimum rise time (ngspice: tstep; Xyce: its breakpoint logic), so the 'same' source differs between simulators. Give tr explicitly, a few output steps long.

Source: ngspice manual (PULSE defaults TR = TSTEP).

## HB001: Harmonic balance: harmonics and drive

HB truncates the spectrum at K harmonics. For a diode driven by 1 V through 50 ohm, K = 8 put the 7th-8th harmonics 2.6 dB (Xyce vs circuitRF) to 23 dB (vs a converged transient) off; with K = 32 circuitRF, Xyce and ngspice transient + DFT agreed to 0.02 dB on harmonics 0-8. qucsator's HB did not converge on that case at all. Raise K until the harmonics you report stop moving.

Source: this package's diode HB example; circuitRF HB reference.

## SUP001: Simulator coverage for this circuit

Not every simulator supports every element/analysis pair (no Touchstone in ngspice, no transient in circuitRF, no HB in ngspice, Xyce frequency-domain lines). A cross-check that silently drops a tool looks like agreement; this rule lists who will run.

Source: this package's coverage matrix.

# Workflow lessons (not lint rules)

## wsl.exe re-parses bash -c arguments

'$f', '$HOME' and loops in 'wsl -- bash -c "..."' expand early or vanish; the runner always writes a run.sh and runs 'bash run.sh'.

Source: this package (build log), palace-agent-tools.

## Git Bash rewrites /mnt/c paths

From Git Bash on Windows, 'wsl bash /mnt/c/x.sh' becomes 'C:/Program Files/Git/mnt/c/x.sh'; set MSYS_NO_PATHCONV=1 (Python subprocess is not affected).

Source: this package (build log).

## circuitRF verbs, not --help

'circuitrf --help' starts the GUI and hangs headless; use the verbs (sparam, dc, hb, check, explain, reference) and put the verb first.

Source: circuitRF CLI reference.

## circuitRF HB tone needs a variable or a bare number

Tone="100 MHz" is refused ('Unexpected token MHz'); define F0 = 100 MHz and write Tone="F0".

Source: this package (diode HB).

## circuitRF Unit= on sparam

Before circuitRF 1.0 'Unit=GHz' on a sparam directive was ignored and the sweep ran in Hz with a plausible flat S11; the translator writes 'Hz' on every number.

Source: circuitRF netlist reference ('Changed in 1.0').

## scikit-rf MLine defaults

skrf.media.MLine defaults to a Djordjevic-Svensson wideband dielectric (er, tan d given at 1 GHz) and 0.15 um roughness; qucsator and circuitRF use constant er/tan d and smooth copper. Set diel='frequencyinvariant', rough=0 for a like-for-like check.

Source: scikit-rf 2.2 source.

## SPICE has no S-parameter blocks (ngspice)

ngspice cannot read Touchstone; fit a rational model (scikit-rf VectorFitting.write_spice_subcircuit_s) or use qucsator/circuitRF/Xyce-HB.

Source: ngspice manual.

## qucsator HB on hard-driven diodes

qucsator_rf 1.0.7 .HB did not converge (150 and 500 iterations) on a 1 V, 50 ohm-driven diode that circuitRF and Xyce solved in one Newton run; its non-converged output still looks like a spectrum. Check the log for 'no convergence'.

Source: this package (diode HB example).

## Xyce HB output is two-sided

The HB.FD.csv lists -N..N f0 with half amplitudes; single-sided peak = 2|X_k| for k > 0.

Source: this package.

## qucsator diode pin order

qucsator's Diode is 'Diode:D cathode anode' (diode.cpp NODE_C 0), the reverse of SPICE. A reversed diode mirrors the waveform, so every harmonic magnitude still agrees with the other engines; only the DC term (or a DC run: 5 V instead of 0.62 V) shows it. compare_harmonics fails a DC sign flip explicitly.

Source: this package (diode DC/HB examples), qucsator_rf diode.cpp.

## Touchstone 1 and mixed port impedances

The option line holds one z0. skrf Network.write_touchstone(r_ref=...) renormalises the data to that value (an L-match S11 of 0 became -4.4 dB); circuitRF writes port 1's z0 plus a comment. We write Touchstone 2.0 with [Reference] instead.

Source: this package (L-match example).

## Qucs-S headless

QT_QPA_PLATFORM=offscreen qucs-s -n -i x.sch -o x.net converts a schematic to a qucsator netlist without a display (--ngspice / --xyce for SPICE); the conda qucs-s package needs Qt 6.7 from the Anaconda defaults channel, so build it against conda-forge qt6-main.

Source: this package (install log).

## Diode BV = 0

In SPICE, BV=0 puts the breakdown at 0 V; omit BV for 'no breakdown' (qucsator and circuitRF read 0 as 'off').

Source: ngspice manual (diode model).
