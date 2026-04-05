# EnergyTracer Run Guide

## Summary flow

The normal EnergyTracer flow is:

1. **Run `ltracer.py` first** to record Linux CPU power-state events and energy samples.
2. **Run the DPDK application** using the instructions in the `DPDK/` folder of this repository.
3. **Run TRex traffic** using the instructions in the `Trex_Traffic/` folder of this repository.
4. After the run, keep the **raw Linux trace**, **raw DPDK trace**, and **`anchor.txt`**.
5. **Run `dtracer.py`** to export the raw DPDK CTF trace into `dpdk.sqlite`.
6. **Run `sync.py`** to join Linux and DPDK data into `unified/unified.sqlite`.
7. **Optionally run `Validator/sync_checker.py`** to verify synchronization.
8. **Run `chart.py`** to generate SVG figures from the unified database.

Throughout this guide, the run directory is named:

```text
MY_TRACE_RECORD
```

---

## 1. What each module does

### `ltracer.py`
**Purpose:** records Linux kernel power-management events and energy samples.

It creates the Linux-side raw trace and exports it into a query-friendly database under:

```text
./traces/MY_TRACE_RECORD/linux/
```

Typical outputs include:
- `trace.dat`
- `kernel_events.jsonl`
- `energy_samples.csv`
- `linux.sqlite`

### DPDK application
**Purpose:** runs the packet-processing application and writes the raw DPDK trace.

The DPDK app should be run as described in the **`DPDK/`** folder of this repository. This step produces:
- the raw DPDK CTF trace,
- and `anchor.txt`, which is later used for synchronization.

### `Trex_Traffic`
**Purpose:** generates the packet workload that drives the DPDK application.

TRex should be run as described in the **`Trex_Traffic/`** folder of this repository.

### `dtracer.py`
**Purpose:** exports the raw DPDK CTF trace into a DPDK SQLite database.

It converts the saved DPDK trace into:
- `dpdk.sqlite`
- `dpdk_events.jsonl`

under:

```text
./traces/MY_TRACE_RECORD/dpdk/
```

### `sync.py`
**Purpose:** joins the Linux database and the DPDK database into one unified timeline.

It uses:
- `linux.sqlite`
- `dpdk.sqlite`
- `anchor.txt`

and creates:

```text
./traces/MY_TRACE_RECORD/unified/unified.sqlite
```

This unified database is the main input for later analysis and visualization.

### `chart.py`
**Purpose:** generates visualization outputs from `unified.sqlite`.

It creates categorized SVG charts under:

```text
./traces/MY_TRACE_RECORD/charts/
```

The charts are grouped into:
- `general/histograms`
- `general/energy`
- `cpu-wise/cpu_<ID>/timelines`
- `cpu-wise/cpu_<ID>/histograms`
- `port-wise/port_<ID>/timelines`
- `port-wise/port_<ID>/histograms`

---

## 2. Step 1: record Linux power-state and energy traces

Run `ltracer.py` first.

Example:

```bash
sudo python3 ltracer.py record \
  --run-dir ./traces/MY_TRACE_RECORD \
  --clock x86-tsc \
  --taskset-cpus 0 \
  --cpulist 1ff0 \
  --mask-via-sysfs \
  --set-tracing-on \
  --tsc-hz 2000000000 \
  --energy-interval-ms 1 \
  --energy-start-delay-s 7 \
  -d 15 \
  -e power:cpu_idle \
  -e power:cpu_frequency \
  -e power:pstate_sample
```

### What this command means

- `record`: use the recorder mode of `ltracer.py`.
- `--run-dir`: root directory of this experiment run.
- `--clock x86-tsc`: use x86 TSC as the primary timestamp unit.
- `--taskset-cpus 0`: pin the user-space `trace-cmd` recorder to CPU 0.
- `--cpulist 1ff0`: tracing CPU mask, passed to `trace-cmd -M` and also used for optional sysfs mask write.
- `--mask-via-sysfs`: also write `tracing_cpumask` directly through sysfs before recording starts.
- `--set-tracing-on`: force `tracing_on=1` before the run and restore it after the run.
- `--tsc-hz 2000000000`: TSC frequency used to convert cycle timestamps into time.
- `--energy-interval-ms 1`: energy sampling interval in milliseconds.
- `--energy-start-delay-s 7`: wait 7 seconds before starting energy sampling.
- `-d 15`: record for 15 seconds.
- `-e power:cpu_idle`: record Linux CPU idle-state transitions.
- `-e power:cpu_frequency`: record Linux CPU frequency changes.
- `-e power:pstate_sample`: record Linux pstate sampling events.

### Key idea
`ltracer.py` is the **Linux-side recorder**. It captures kernel power events and energy samples before the DPDK app and TRex traffic start, so the Linux-side timeline is already active when packet processing begins.

---

## 3. Step 2: run the DPDK app

After `ltracer.py` starts, run the DPDK application using the instructions in:

```text
DPDK/
```

### Key idea
The DPDK application is the **DPDK-side trace source**. It processes packets and writes:
- the raw DPDK CTF trace,
- and `anchor.txt`, which is later used by `sync.py`.

---

## 4. Step 3: start TRex traffic

After the DPDK app is running, start the traffic generator using the instructions in:

```text
Trex_Traffic/
```

### Key idea
TRex is the **traffic driver**. It creates the packet workload that triggers RX activity in the DPDK application.

---

## 5. Raw trace outputs after the run

After the experiment, you should have two raw trace sides.

### Linux side
Under:

```text
./traces/MY_TRACE_RECORD/linux/
```

Important files include:
- `trace.dat`
- `kernel_report.txt`
- `kernel_events.jsonl`
- `energy_samples.csv`
- `linux.sqlite`

### DPDK side
Under the DPDK trace directory and run directory, you should have:
- the raw CTF trace,
- `anchor.txt`,
- and later `dpdk.sqlite` after export.

---

## 6. Step 4: export the DPDK raw trace into a database

Run `dtracer.py` after the raw DPDK trace has been saved.

Example:

```bash
sudo python3.6 dtracer.py \
  --run-dir ./traces/MY_TRACE_RECORD \
  -t ./traces/MY_TRACE_RECORD/<DPDK_RAW_TRACE_DIR>
```

### What this command means

- `--run-dir`: root directory of this experiment run.
- `-t` / `--trace`: path to the saved raw DPDK CTF trace directory.

### Key idea
`dtracer.py` is the **DPDK trace exporter**. It reads the raw CTF trace and creates:
- `dpdk.sqlite`
- `dpdk_events.jsonl`

This is the DPDK-side database used later by `sync.py`.

---

## 7. Step 5: synchronize Linux and DPDK into one unified database

Run `sync.py` after both `linux.sqlite` and `dpdk.sqlite` exist.

Example:

```bash
sudo python3 sync.py \
  --run-dir ./traces/MY_TRACE_RECORD \
  --linux-db ./traces/MY_TRACE_RECORD/linux/linux.sqlite \
  --dpdk-db ./traces/MY_TRACE_RECORD/dpdk/dpdk.sqlite \
  --anchor ./traces/MY_TRACE_RECORD/anchor.txt
```

### What this command means

- `--run-dir`: root directory of this experiment run.
- `--linux-db`: path to the Linux-side SQLite database.
- `--dpdk-db`: path to the DPDK-side SQLite database.
- `--anchor`: path to `anchor.txt`, used to align the two timelines.

### Key idea
`sync.py` is the **timeline joiner**. It aligns Linux events, DPDK events, and energy samples using `anchor.txt`, then stores them in:

```text
./traces/MY_TRACE_RECORD/unified/unified.sqlite
```

This is the main database for cross-layer analysis.

### Optional sync validation
After `sync.py`, you can check the synchronization using the validator script in the repository:

```bash
sudo python3 Validator/sync_checker.py \
  -i ./traces/MY_TRACE_RECORD/unified/unified.sqlite
```

This step is useful to confirm that the unified timeline looks correct before chart generation.

---

## 8. Other optional checks

### Check Linux database

```bash
sudo python3 checker.py -i ./traces/MY_TRACE_RECORD/linux/linux.sqlite
```

### Inspect summary statistics

```bash
sudo python3 stats.py \
  -d ./traces/MY_TRACE_RECORD/dpdk/dpdk.sqlite \
  -l ./traces/MY_TRACE_RECORD/linux/linux.sqlite
```

These scripts are useful for checking whether the exported databases look reasonable before chart generation.

---

## 9. Step 6: generate charts

Run `chart.py` on the unified database.

Example:

```bash
sudo python3.6 chart.py \
  --run-dir ./traces/MY_TRACE_RECORD \
  --porttocpu '(0,4),(1,5),(2,6),(3,7),(4,4),(5,5),(6,6),(7,7)' \
  --auto --auto-d 0.2 \
  --pack-rx-zero --rxnz --igrxz 200 \
  -o ./traces/MY_TRACE_RECORD/charts
```

### What this command means

- `--run-dir`: root directory of this experiment run.
- `--porttocpu`: mapping from DPDK port ID to CPU/lcore ID. This is required for CPU-wise and port-wise overlays.
- `--auto`: choose the chart window automatically, starting from the first DPDK event with `nb_rx > 0`.
- `--auto-d 0.2`: keep a 0.2-second window after the auto start point.
- `--pack-rx-zero`: pack consecutive RX-zero events into grouped zero-poll bars.
- `--rxnz`: also generate extra packed RX-nonzero timeline versions.
- `--igrxz 200`: when packing RX nonzero bursts, ignore up to 200 intervening RX-zero polls.
- `-o`: output directory for generated charts.

### Key idea
`chart.py` is the **visualization module**. It reads `unified.sqlite` and produces SVG outputs grouped into global, CPU-wise, and port-wise views.

---

## 10. What `--igrxz` means

`--igrxz N` controls how aggressively short RX-zero runs are ignored when packed RXNZ views are created.

Examples:
- `--igrxz 2` means up to 2 zero polls may be ignored between nonzero groups.
- If a zero run is 14 or 17 polls, it is **not** ignored when `--igrxz 10` is used, because 14 and 17 are larger than 10.

So `--igrxz` controls whether short zero runs are treated as small gaps inside one burst group, or as real separators between bursts.

---

## 11. Chart families produced by `chart.py`

### General histograms
These summarize the full selected window, including:
- event counts,
- RX count distribution,
- zero vs nonzero RX counts by port,
- gap distributions,
- idle-state counts,
- frequency-state counts,
- and event density.

### General energy charts
These include time-series views for:
- ACPI power,
- package energy,
- DRAM energy,
- package power,
- and DRAM power.

### CPU-wise timelines and histograms
For each CPU, `chart.py` can produce:
- mixed timelines,
- stacked timelines,
- stacked RXNZ-packed timelines,
- event-count histograms,
- RX-count histograms,
- idle-state histograms,
- and frequency-state histograms.

### Port-wise timelines and histograms
For each port, `chart.py` can produce:
- mixed timelines,
- stacked timelines,
- stacked RXNZ-packed timelines,
- event-count histograms,
- RX-count histograms,
- gap-after-zero histograms,
- and gap-after-nonzero histograms.


