# Examples

Each script builds neutral specs, lints them, cross-checks them in every installed simulator and writes
`results.json` next to itself (simulator folders go to `runs/`, git-ignored). Times are on a busy 8-thread desktop.

| script | what | time |
|---|---|---|
| `chebyshev_lpf/run_example.py` | 5th-order 0.1 dB Chebyshev LPF: ideal, Palace EM 7-port with schematic values, and with the port-tuned E24 values; compared with port-tuning-palace | 11 s |
| `lsection_match/run_example.py` | 50 -> 200 ohm L-match at 2.4 GHz, mixed port z0, vs closed form | 4 s |
| `quarter_wave_microstrip/run_example.py` | 70.7 ohm lossy microstrip quarter-wave transformer on RO4003C | 5 s |
| `rlc_transient/run_example.py` | series RLC step and an open-ended ideal line, vs closed forms | 9 s |
| `diode_hb/run_example.py` | diode harmonic balance at 0.3 V and 1 V (8 and 32 harmonics) + DC bias | 17 s |
| `qucs_s_import/run_example.py` | Qucs-S's Wilkinson schematic converted by Qucs-S, imported and cross-checked | 5 s |
