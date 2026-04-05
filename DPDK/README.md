# SPDX-License-Identifier: BSD-3-Clause

# gNB_power_aware

`gNB_power_aware` is a DPDK v20.05 example application derived from `l3fwd-power` and extended with custom packet processing, runtime tracing hooks, and power-management logic.

This document explains how to place the application in a DPDK tree, edit it, compile it, and run it with tracing enabled.

---

## Overview

This application is intended to be added under the DPDK examples tree:

```text
<DPDK_DIR>/examples/gNB_power_aware/
```

The main source file is typically:

```text
<DPDK_DIR>/examples/gNB_power_aware/main.c
```

After placing the application in this location, users can edit the source, build it from the build directory, and run it as a standard DPDK example.

---

## Editing the application

Users can edit the source file directly to adapt the application to their own setup and experiment design.

Typical customization points include:

- packet-processing logic,
- trace trigger conditions,
- trace start and stop behavior,
- trace packet limits,
- idle heuristics,
- frequency scale-up and down logic,
- and queue/core mapping assumptions.

---

## Annotation guide

The source code uses two annotation styles to make modification easier.

### `Add Your Tracing Instrumentation Here`

These comments identify the tracing-related sections of the code.  
They show where a user can adapt:

- trace initialization,
- trace trigger conditions,
- anchor generation,
- tracepoint enable and disable logic,
- bounded capture logic,
- poll-side trace hooks,
- and lightweight RX polling counters.

These annotations are intended to help users understand where tracing behavior can be changed for their own experiments.

### `Power Mng Algortimgn heriotic`

These comments identify the power-management sections of the code.  
They show where a user can adapt:

- zero-poll thresholds,
- idle hint computation,
- sleep and backoff behavior,
- frequency scale-up heuristics,
- timer-driven frequency down-scaling,
- and power-library initialization and cleanup.

These annotations are intended to help users understand where CPU power behavior is controlled.

---

## Compiling the application

After editing the source, go to the application build directory:

```bash
cd <DPDK_DIR>/examples/gNB_power_aware/build
```

Then compile the application with:

```bash
sudo make
```

If a clean rebuild is needed:

```bash
sudo make clean
sudo make
```

This produces the executable:

```text
./gNB_power_aware
```

---

## Running the application with tracing

Example command:

```bash
sudo ./gNB_power_aware \
  -l 4-12 \
  -w 0000:17:08.0 -w 0000:17:08.1 -w 0000:17:08.2 -w 0000:17:08.3 \
  -w 0000:17:08.4 -w 0000:17:08.5 -w 0000:17:08.6 -w 0000:17:08.7 \
  -m 10000 \
  --file-prefix=0 \
  --trace='.*rx_burst_(empty|nonempty).*' \
  --trace-dir=<TRACE_ROOT>/my_trace_record \
  --trace-bufsz=128M \
  --trace-mode=overwrite \
  -- \
  -p 0xff \
  --config="(0,0,4),(1,0,5),(2,0,6),(3,0,7),(4,0,4),(5,0,5),(6,0,6),(7,0,7)" \
  --parse-ptype
```

The trace output directory name used in this guide is:

```text
my_trace_record
```

---

## Command explanation

The command is divided into two parts:

- **DPDK EAL arguments** before `--`
- **application arguments** after `--`

### EAL arguments

#### `-l 4-12`
Use logical cores 4 through 12.

#### `-w <PCI_ADDR>`
Attach the listed NIC PCI devices to the application.

#### `-m 10000`
Reserve 10000 MB of memory for DPDK.

#### `--file-prefix=0`
Set the shared-memory prefix for this run.

#### `--trace='.*rx_burst_(empty|nonempty).*'`
Enable DPDK tracepoints matching RX empty and non-empty burst events.

#### `--trace-dir=<TRACE_ROOT>/my_trace_record`
Store trace output in the directory `my_trace_record`.

#### `--trace-bufsz=128M`
Allocate a 128 MB trace buffer.

#### `--trace-mode=overwrite`
Allow the trace buffer to overwrite old entries when full.

#### `--`
Separator between EAL arguments and application-specific arguments.

---

### Application arguments

#### `-p 0xff`
Enable ports 0 through 7.

#### `--config="(port,queue,lcore),..."`
Map RX queues to lcores.

In the example above:

- lcore 4 handles ports 0 and 4,
- lcore 5 handles ports 1 and 5,
- lcore 6 handles ports 2 and 6,
- lcore 7 handles ports 3 and 7.

#### `--parse-ptype`
Enable software packet-type parsing.

---

## What users typically modify

Users usually customize two things:

### 1. The source code

This includes:

- packet-processing behavior,
- tracing conditions,
- trace limits,
- anchor logic,
- power-management heuristics,
- and frequency-control behavior.

### 2. The run command

This includes:

- core list,
- PCI device list,
- trace directory,
- port mask,
- and port/queue/lcore mapping.

---

## Notes

This application assumes:

- DPDK v20.05 is already built,
- the required NIC devices are available and usable by DPDK,
- and the user has sufficient privileges to run DPDK applications.

For tracing experiments, users should typically review both:
- the runtime command-line trace options, and
- the annotated tracing sections in the source code.

For power-management experiments, users should review:
- the heuristic functions,
- timer behavior,
- and the legacy loop logic marked by the power-management annotations.
