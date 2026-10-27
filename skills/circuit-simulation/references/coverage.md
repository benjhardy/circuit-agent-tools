# Simulator coverage (measured 2026-10-19)

Versions: ngspice 46 development build (conda unic-cass 45.2_158), Xyce 7.10.0-172 development build (conda
unic-cass), qucsator_rf 1.0.7 (source, 951bfed), Qucs-S 26.1.1 (source, 46e47e8), circuitRF 1.0.9,
scikit-rf 2.2.0. The translators raise `Unsupported` (with this reason) instead of running something wrong.

| element / analysis | scikit-rf | ngspice | Xyce | qucsator | circuitRF |
|---|---|---|---|---|---|
| R, L, C | S | S DC T H | S DC T H | S DC T H | S DC H |
| port (z0 per port) | S | S (AC drive per port; real z0) | S (.LIN P ports) | S (Pac) | S (Port) |
| vsource / isource | isource only (open) | DC T H; AC in S | DC T H | DC T H (series chain for V) | Vdc, V_1Tone (no I) |
| ideal line | S | S DC T | T only (wire in .AC) | S T | S |
| microstrip | S (MLine, HJ + KJ, invariant er) | S T as LTRA, RLGC frozen at f_ref, no G | - | S (MLIN Hammerstad + Kirschning) | S (MLIN, own thickness model) |
| coupled microstrip, open end, width step | - | - | - | S (MCOUPLED Kirschning-Jansen, MOPEN Kirschning-Jansen-Koster, MSTEP Gupta-Garg, wide side first) | - (no models in 1.0.9) |
| Touchstone block | S (cubic interpolation, no extrapolation) | - | H only (YLIN; open in .AC) | S DC (SPfile, linear interp., open at DC) | S DC H (SnP, cubic spline, nearest-edge) |
| diode (SPICE level 1) | - | DC T H | DC T H | DC T H (cathode first) | DC H |
| harmonic balance | - | transient + exact-bin DFT | .HB (two-sided output) | .HB (poor convergence on hard drive) | hb (JSON) |
| transient | closed-form helpers | tran + linearize | .TRAN | .TR | - |
| schematic | - | - | - | Qucs-S .sch import via `qucs-s -n` | its own MCP / .csch |

S = S-parameters, DC = operating point, T = transient, H = harmonic balance.

## Agreement to expect

| circuit | typical max difference |
|---|---|
| lumped R/L/C, ideal lines | 1e-15 (qucsator, scikit-rf) to 5e-9 (ngspice prints 9 digits) |
| Touchstone block terminated with parts | 1e-11 (circuitRF, qucsator vs scikit-rf) |
| microstrip, thin strip | < 1e-3 (qucsator vs scikit-rf) |
| microstrip, t/h = 0.07 | 1e-3 qucsator, 7e-3 ngspice (frozen RLGC), 3e-2 circuitRF (thickness model) |
| transient RLC | 1e-4 of swing |
| HB, weakly nonlinear | 0.01 dB |
| HB, hard drive | 0.02 dB with enough harmonics (32); 2-23 dB with 8 |
| DC with a diode | 1-15 uV |
