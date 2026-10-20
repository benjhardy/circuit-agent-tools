# Running simulators: hosts, guard, records

## Where they run

On Windows the simulators live in WSL (Ubuntu 24.04, user space): ngspice and Xyce in the micromamba env
`circuit-sim`, qucsator_rf in `~/.local/opt/qucsator_rf`, Qucs-S in `~/.local/opt/qucs-s` (Qt from the
`qucs-build` env), circuitRF at `~/.local/bin/circuitrf`. Override with `CIRCUIT_TOOLS_NGSPICE`,
`CIRCUIT_TOOLS_XYCE`, `CIRCUIT_TOOLS_QUCSATOR`, `CIRCUIT_TOOLS_CIRCUITRF`, `CIRCUIT_TOOLS_QUCS_S`
(`CIRCUIT_TOOLS_QUCS_S_LIBS` for its Qt libraries); `CIRCUIT_TOOLS_BACKEND=native` on Linux;
`CIRCUIT_TOOLS_DISTRO` picks the WSL distribution. `circuit-tools doctor '{}'` prints paths and versions.

Each run writes the netlist(s) and a `run.sh` into `<case_dir>/<sim>/` and executes `wsl.exe -- bash run.sh`
(never `bash -c "..."`: wsl.exe re-parses `$` and loops). Commands run under `timeout` (default 300 s),
`nice -n 10`, `OMP_NUM_THREADS=1`. Logs are scanned for errors (ngspice "singular matrix", "timestep too small";
Xyce "Netlist error", "Xyce Abort"; qucsator "no convergence"; circuitRF "refused"/"failed").

## Guard

`circuit-tools status '{}'` reads CPUs, load average, available RAM and running EM solver processes
(Palace, MEEP, Elmer) on the simulator host. Runs are refused when less than 1 GB + the estimate is
available or more than 200 runs are requested; `force: true` overrides. A busy CPU only adds advice: circuit
runs are short and already sequential at low priority.

## Outputs

`<case>_sparam.sNp` (Touchstone 1.1, or 2.0 with `[Reference]` when port z0 differ) and `.csv`;
`<case>_tran.csv`; `<case>_hb.json` / `_dc.json` (single-sided peak phasors, volts).

## Records

Each simulator run and each cross-check is an `rflab.run/1` record (kind `circuit`) via `rflab.record()` when
rflab-core is installed, else a JSON line in `%USERPROFILE%\.rflab\pending\circuit-agent-tools.jsonl`.
Metrics are flat (`points`, `ports`, `passivity`, `wall_s`, `<sim>.max_dS`, ...); checks are
`{name, passed, value, limit, note}`; status `ok` means the run completed. `CIRCUIT_TOOLS_RECORD=0` disables it.
