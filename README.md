# EnergyTracer

EnergyTracer is a cross-layer observability framework for analyzing the energy behavior of DPDK-based packet-processing applications. It correlates fine-grained DPDK dataplane events with Linux CPU power-state activity and hardware energy measurements on a unified timeline, enabling study of how packet bursts, CPU power states, and energy usage interact in high-performance systems.

This repository belongs to the **EnergyTracer** project described in the paper:

**EnergyTracer: Energy Analysis of Packet Processing Events in DPDK-Based Applications**

**Authors:**  
Mohsen Memarian, Andreas Kassler, Karl-Johan Grinnemo, Sándor Laki, Gergely Pongracz, Johan Forsman

EnergyTracer was designed to help developers and researchers understand the energy cost of dataplane behavior in DPDK-based systems. It combines low-overhead DPDK fast-path tracing, Linux kernel power-state tracing, and hardware energy sampling, then synchronizes them into a single analysis-ready database.

## Repository guides

The repository includes several README files for different parts of the workflow:

- **DPDK application guide**  
  Located in the DPDK application folder.  
  Explains how to edit, compile, and run the `gNB_power_aware` application, including the tracing and power-management annotations in the code.

- **TRex traffic generation guide**  
  Located in the `Trex_Traffic` folder.  
  Explains how to use the TRex traffic profile, how bursts are generated, and how to run the script from the TRex console.

- **EnergyTracer run guide**  
  Located in the EnergyTracer workflow folder or project root, depending on your layout.  
  Explains the full analysis flow: run `ltracer.py`, run the DPDK application, start TRex traffic, export the DPDK trace with `dtracer.py`, build the unified database with `sync.py`, validate synchronization with `Validator/sync_checker.py`, and generate charts with `chart.py`.

## Typical workflow

At a high level, the workflow is:

1. **Capture Linux-side traces and energy samples** with `ltracer.py`
2. **Run the DPDK application** with tracing enabled
3. **Generate traffic** with the TRex profile
4. **Export the raw DPDK trace** with `dtracer.py`
5. **Synchronize Linux and DPDK traces** into `unified.sqlite` with `sync.py`
6. **Validate synchronization** with `Validator/sync_checker.py`
7. **Generate visualizations** with `chart.py`


## Notes

For detailed instructions, users should start with the child README files in the relevant subfolders. The top-level README is meant to give a short overview of the project and help readers quickly find the right guide for each part of the workflow.
