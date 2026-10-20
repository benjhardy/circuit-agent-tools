# Spec recipes

All values are SI numbers or SI strings. Ground is `"0"`. Ports default to the spec's `z0` (50 ohm).

## Lumped ladder (5th-order Chebyshev LPF, shunt-C first)

```json
{"name": "lpf5", "components": [
  {"type": "port", "name": "P1", "nodes": ["in", "0"], "num": 1},
  {"type": "C", "name": "C1", "nodes": ["in", "0"], "value": "1.4602pF"},
  {"type": "L", "name": "L2", "nodes": ["in", "n2"], "value": "4.3647nH"},
  {"type": "C", "name": "C3", "nodes": ["n2", "0"], "value": "2.5147pF"},
  {"type": "L", "name": "L4", "nodes": ["n2", "out"], "value": "4.3647nH"},
  {"type": "C", "name": "C5", "nodes": ["out", "0"], "value": "1.4602pF"},
  {"type": "port", "name": "P2", "nodes": ["out", "0"], "num": 2}],
 "analyses": [{"type": "sparam", "start": "0.1GHz", "stop": "8GHz", "points": 317}]}
```

## EM data as an S-block, internal ports terminated with parts (port tuning)

```json
{"type": "sblock", "name": "EM", "nodes": ["p1", "p2", "pC1", "pL2", "pC3", "pL4", "pC5"], "file": "em_7port.s7p"},
{"type": "port", "name": "P1", "nodes": ["p1", "0"], "num": 1},
{"type": "port", "name": "P2", "nodes": ["p2", "0"], "num": 2},
{"type": "C", "name": "C1", "nodes": ["pC1", "0"], "value": "1.0pF"}
```

One node per file port, in file order; the block's reference is ground unless `"ref"` names a node. A port
left unconnected is open, not matched (SBK006). Keep the sweep inside the file's band (SBK002).

## Ideal and microstrip lines

```json
{"type": "tline", "name": "T1", "nodes": ["a", "b"], "z0": 70.71, "length_deg": 90, "f_ref": "2.4GHz"}
{"type": "tline", "name": "T2", "nodes": ["a", "b"], "z0": 50, "length": "20mm", "eeff": 2.7}
{"type": "mline", "name": "M1", "nodes": ["a", "b"], "w": "0.579mm", "l": "19.37mm", "substrate": "ro4003c",
 "f_ref": "2.4GHz"}
"substrates": {"ro4003c": {"er": 3.55, "h": "0.508mm", "t": "35um", "tand": 0.0027, "sigma": 5.8e7, "rough": 0}}
```

`f_ref` on an mline is where ngspice's frozen RLGC line matches (default: mid-band).

## Transient step

```json
{"type": "vsource", "name": "V1", "nodes": ["s", "0"], "pulse": {"v1": 0, "v2": 1, "td": "0.1ns", "tr": "20ps"}},
"analyses": [{"type": "tran", "stop": "10ns", "step": "2ps", "outputs": ["c"]}]
```

Give `tr` (TRN002) and an output step <= 1/10 of the fastest time constant (TRN001).

## Harmonic balance (single tone)

```json
{"type": "vsource", "name": "V1", "nodes": ["src", "0"], "sin": {"amp": 1.0, "freq": "100MHz"}},
{"type": "diode", "name": "D1", "nodes": ["a", "0"], "model": {"is": 1e-12, "n": 1.05, "rs": 5, "cj0": "0.5pF"}},
"temp_c": 27,
"analyses": [{"type": "hb", "f0": "100MHz", "harmonics": 32, "outputs": ["a"],
              "samples_per_period": 512, "settle_periods": 30, "fft_periods": 10}]
```

Sinusoids are cosine-referenced (`v = offset + amp cos(2 pi f t + phase)`). The last three keys tune ngspice's
transient + DFT substitute. Results: single-sided peak phasors per harmonic (`dbv` re 1 V peak in summaries).

## DC operating point

```json
"analyses": [{"type": "dc", "outputs": ["a", "b"]}]
```
