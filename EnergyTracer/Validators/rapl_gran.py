#!/usr/bin/env python3
# Python 3.6 compatible

from __future__ import print_function

import argparse
import os
import re
import struct
import subprocess
import sys
import time
import threading
try:
    import queue
except ImportError:
    import Queue as queue
from collections import defaultdict

MSR_RAPL_POWER_UNIT = 0x606
MSR_PKG_ENERGY = 0x611
MSR_DRAM_ENERGY = 0x619

DOMAIN_TO_REG = {
    "pkg": MSR_PKG_ENERGY,
    "dram": MSR_DRAM_ENERGY,
}

# ANSI colors
C_RST = "\033[0m"
C_RED = "\033[31m"
C_GRN = "\033[32m"
C_YEL = "\033[33m"
C_BLU = "\033[34m"
C_MAG = "\033[35m"
C_CYN = "\033[36m"
C_WHT = "\033[37m"
C_BOLD = "\033[1m"


def die(msg, code=1):
    print("{}[ERROR]{} {}".format(C_RED, C_RST, msg), file=sys.stderr)
    sys.exit(code)


def monotonic_raw_ns():
    return int(time.clock_gettime(time.CLOCK_MONOTONIC_RAW) * 1e9)


def open_msr(cpu):
    path = "/dev/cpu/{}/msr".format(cpu)
    try:
        return os.open(path, os.O_RDONLY)
    except Exception as e:
        die("failed to open {}: {}".format(path, e))


def rdmsr(fd, addr):
    data = os.pread(fd, 8, addr)
    if len(data) != 8:
        raise RuntimeError("short read on MSR 0x{:x}".format(addr))
    return struct.unpack("<Q", data)[0]


def read_energy_unit_j(fd):
    pu = rdmsr(fd, MSR_RAPL_POWER_UNIT)
    esu = (pu >> 8) & 0x1F
    return 1.0 / (2 ** esu)


def read_energy_counter_32(fd, reg):
    return rdmsr(fd, reg) & 0xFFFFFFFF


def read_text(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def _glob_cpu_package_paths():
    import glob
    return glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/physical_package_id")


def _cpu_from_sysfs_path(path):
    m = re.search(r"/cpu(\d+)/", path)
    return int(m.group(1)) if m else None


def discover_cpu_to_package():
    cpu_to_pkg = {}

    # sysfs
    for path in sorted(_glob_cpu_package_paths()):
        cpu = _cpu_from_sysfs_path(path)
        txt = read_text(path)
        if cpu is None or txt is None:
            continue
        try:
            pkg = int(txt)
        except ValueError:
            continue
        cpu_to_pkg[cpu] = pkg
    if cpu_to_pkg:
        return dict(sorted(cpu_to_pkg.items()))

    # lscpu fallback
    try:
        out = subprocess.check_output(["lscpu", "-p=CPU,SOCKET"], universal_newlines=True)
        for ln in out.splitlines():
            if not ln or ln.startswith("#"):
                continue
            parts = ln.split(",")
            if len(parts) < 2:
                continue
            try:
                cpu = int(parts[0])
                pkg = int(parts[1])
            except ValueError:
                continue
            cpu_to_pkg[cpu] = pkg
    except Exception:
        pass
    if cpu_to_pkg:
        return dict(sorted(cpu_to_pkg.items()))

    # /proc/cpuinfo fallback
    txt = read_text("/proc/cpuinfo") or ""
    cur_cpu = None
    cur_pkg = None
    for ln in txt.splitlines():
        if ln.startswith("processor"):
            m = re.search(r"processor\s*:\s*(\d+)", ln)
            if m:
                cur_cpu = int(m.group(1))
        elif ln.startswith("physical id"):
            m = re.search(r"physical id\s*:\s*(\d+)", ln)
            if m:
                cur_pkg = int(m.group(1))
        elif ln.strip() == "":
            if cur_cpu is not None and cur_pkg is not None:
                cpu_to_pkg[cur_cpu] = cur_pkg
            cur_cpu = None
            cur_pkg = None
    if cur_cpu is not None and cur_pkg is not None:
        cpu_to_pkg[cur_cpu] = cur_pkg

    if not cpu_to_pkg:
        die("could not discover CPU -> package mapping")

    return dict(sorted(cpu_to_pkg.items()))


def parse_include(include_text):
    out = []
    for raw in include_text.split(","):
        item = raw.strip().lower()
        if not item:
            continue
        m = re.fullmatch(r"(pkg|dram)(\d+)", item)
        if not m:
            die("bad include item '{}', expected like pkg0,dram1".format(item))
        out.append((m.group(1), int(m.group(2))))
    if not out:
        die("empty -i/--include")
    return out


def parse_cpu_bindings(bindings):
    out = defaultdict(list)
    for raw in bindings:
        item = raw.strip().lower()
        m = re.fullmatch(r"pkg(\d+):(\d+)", item)
        if not m:
            die("bad --cpu '{}', expected like pkg0:4".format(raw))
        pkg = int(m.group(1))
        cpu = int(m.group(2))
        out[pkg].append(cpu)

    clean = {}
    for pkg, cpus in out.items():
        seen = set()
        ordered = []
        for cpu in cpus:
            if cpu not in seen:
                ordered.append(cpu)
                seen.add(cpu)
        clean[pkg] = ordered
    return clean


def fmt_f(v, digits=3):
    if v is None:
        return "n/a"
    return ("{:." + str(digits) + "f}").format(v)


def ns_to_us(ns):
    if ns is None:
        return None
    return ns / 1e3


def color_for_label(label):
    return C_CYN if label.startswith("pkg") else C_MAG


class Stats(object):
    def __init__(self):
        self.samples = 0            # nonzero energy-counter changes
        self.intervals = 0          # number of Δt / ΔE pairs used for interval/power stats

        self.min_update_ns = None
        self.max_update_ns = None
        self.sum_update_ns = 0

        self.min_power_w = None
        self.max_power_w = None
        self.sum_power_w = 0.0

        self.lock = threading.Lock()

    def record_interval(self, dt_ns, power_w):
        self.intervals += 1
        self.sum_update_ns += dt_ns

        if self.min_update_ns is None or dt_ns < self.min_update_ns:
            self.min_update_ns = dt_ns
        if self.max_update_ns is None or dt_ns > self.max_update_ns:
            self.max_update_ns = dt_ns

        self.sum_power_w += power_w
        if self.min_power_w is None or power_w < self.min_power_w:
            self.min_power_w = power_w
        if self.max_power_w is None or power_w > self.max_power_w:
            self.max_power_w = power_w

    def snapshot(self):
        with self.lock:
            return {
                "samples": self.samples,
                "intervals": self.intervals,
                "min_update_ns": self.min_update_ns,
                "avg_update_ns": None if self.intervals == 0 else float(self.sum_update_ns) / float(self.intervals),
                "max_update_ns": self.max_update_ns,
                "min_power_w": self.min_power_w,
                "avg_power_w": None if self.intervals == 0 else self.sum_power_w / float(self.intervals),
                "max_power_w": self.max_power_w,
            }


class Target(object):
    def __init__(self, domain, pkg, cpu, fd, unit_j):
        self.domain = domain
        self.pkg = pkg
        self.cpu = cpu
        self.fd = fd
        self.unit_j = unit_j
        self.reg = DOMAIN_TO_REG[domain]
        self.stats = Stats()

    @property
    def label(self):
        return "{}{}-cpu{}".format(self.domain, self.pkg, self.cpu)


def build_targets(include_items, pkg_to_cpus, cpu_to_pkg):
    targets = []
    opened = {}

    needed_pkgs = sorted(set(pkg for _domain, pkg in include_items))
    for pkg in needed_pkgs:
        if pkg not in pkg_to_cpus:
            die("package {} requested but no --cpu pkg{}:<cpu> provided".format(pkg, pkg))

    for pkg, cpus in sorted(pkg_to_cpus.items()):
        for cpu in cpus:
            actual_pkg = cpu_to_pkg.get(cpu)
            if actual_pkg is None:
                die("cpu {} not found in topology".format(cpu))
            if actual_pkg != pkg:
                die("cpu {} belongs to package {}, not package {}".format(cpu, actual_pkg, pkg))

    for domain, pkg in include_items:
        for cpu in pkg_to_cpus[pkg]:
            if cpu not in opened:
                fd = open_msr(cpu)
                opened[cpu] = (fd, read_energy_unit_j(fd))
            fd, unit_j = opened[cpu]
            targets.append(Target(domain, pkg, cpu, fd, unit_j))

    return targets, opened


def print_header(targets):
    print(C_BOLD + C_WHT + "---------------- START ----------------" + C_RST)
    for t in targets:
        print("{}{}{} reg=0x{:x}".format(color_for_label(t.label), t.label, C_RST, t.reg))
    print(C_BOLD + C_WHT + "---------------------------------------" + C_RST)
    print("")


def quick_batch(events):
    lines = [C_BOLD + C_GRN + "-------------- QUICK --------------" + C_RST + "\n"]
    for ev in events:
        c = color_for_label(ev["label"])
        lines.append(
            "{}t={:.6f}s  {:<12}{}  min_us={}  qlat_us={}\n".format(
                c,
                ev["elapsed_s"],
                ev["label"],
                C_RST,
                fmt_f(ns_to_us(ev["min_ns"]), 3),
                fmt_f(ns_to_us(ev["qlat_ns"]), 3),
            )
        )
    lines.append("\n")
    return "".join(lines)


def periodic_block(elapsed_s, targets):
    lines = [C_BOLD + C_YEL + "------------- PERIODIC ------------" + C_RST + "\n"]
    for t in targets:
        s = t.stats.snapshot()
        c = color_for_label(t.label)
        lines.append(
            "{}t={:.3f}s  {:<12}{}  n={}  min_us={}  avg_us={}  max_us={}  "
            "Pmin={}  Pavg={}  Pmax={}\n".format(
                c, elapsed_s, t.label, C_RST,
                s["samples"],
                fmt_f(ns_to_us(s["min_update_ns"]), 3),
                fmt_f(ns_to_us(s["avg_update_ns"]), 3),
                fmt_f(ns_to_us(s["max_update_ns"]), 3),
                fmt_f(s["min_power_w"], 6),
                fmt_f(s["avg_power_w"], 6),
                fmt_f(s["max_power_w"], 6),
            )
        )
    lines.append("\n")
    return "".join(lines)


def final_block(elapsed_s, targets):
    lines = [C_BOLD + C_BLU + "--------------- FINAL --------------" + C_RST + "\n"]
    for t in targets:
        s = t.stats.snapshot()
        c = color_for_label(t.label)
        lines.append(
            "{}t={:.3f}s  {:<12}{}  n={}  min_us={}  avg_us={}  max_us={}  "
            "Pmin={}  Pavg={}  Pmax={}\n".format(
                c, elapsed_s, t.label, C_RST,
                s["samples"],
                fmt_f(ns_to_us(s["min_update_ns"]), 3),
                fmt_f(ns_to_us(s["avg_update_ns"]), 3),
                fmt_f(ns_to_us(s["max_update_ns"]), 3),
                fmt_f(s["min_power_w"], 6),
                fmt_f(s["avg_power_w"], 6),
                fmt_f(s["max_power_w"], 6),
            )
        )
    lines.append(C_BOLD + C_WHT + "------------------------------------" + C_RST + "\n")
    return "".join(lines)


def worker(target, start_ns, stop_evt, quick_q):
    raw = read_energy_counter_32(target.fd, target.reg)
    prev_raw = raw
    prev_change_ns = monotonic_raw_ns()

    while not stop_evt.is_set():
        now_ns = monotonic_raw_ns()
        raw = read_energy_counter_32(target.fd, target.reg)

        delta_ticks = (raw - prev_raw) & 0xFFFFFFFF
        if delta_ticks > 0:
            delta_energy_j = delta_ticks * target.unit_j
            new_min = False
            min_ns = None

            with target.stats.lock:
                target.stats.samples += 1

                dt_ns = now_ns - prev_change_ns
                if dt_ns > 0:
                    power_w = delta_energy_j / (dt_ns / 1e9)
                    old_min = target.stats.min_update_ns
                    target.stats.record_interval(dt_ns, power_w)

                    if old_min is None or dt_ns < old_min:
                        new_min = True
                        min_ns = dt_ns

            prev_change_ns = now_ns
            prev_raw = raw

            if new_min:
                detect_ns = monotonic_raw_ns()
                quick_q.put({
                    "elapsed_s": (now_ns - start_ns) / 1e9,
                    "label": target.label,
                    "min_ns": min_ns,
                    "detect_ns": detect_ns,
                })


def main():
    ap = argparse.ArgumentParser(description="Fast threaded RAPL MSR probe")
    ap.add_argument("-d", "--duration", type=float, required=True, help="run time in seconds")
    ap.add_argument("-i", "--include", required=True, help="like 'pkg0,pkg1,dram0,dram1'")
    ap.add_argument("--cpu", action="append", default=[], help="repeat: --cpu pkg0:4")
    ap.add_argument("-r", "--report", type=float, default=None, help="optional periodic report seconds")
    args = ap.parse_args()

    if os.geteuid() != 0:
        die("run with sudo/root")

    try:
        subprocess.run(["modprobe", "msr"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass

    include_items = parse_include(args.include)
    pkg_to_cpus = parse_cpu_bindings(args.cpu)
    cpu_to_pkg = discover_cpu_to_package()
    targets, opened = build_targets(include_items, pkg_to_cpus, cpu_to_pkg)

    print_header(targets)

    start_ns = monotonic_raw_ns()
    end_ns = start_ns + int(args.duration * 1e9)

    stop_evt = threading.Event()
    quick_q = queue.Queue()
    threads = []

    next_report_ns = None if args.report is None else start_ns + int(args.report * 1e9)

    for t in targets:
        th = threading.Thread(target=worker, args=(t, start_ns, stop_evt, quick_q))
        th.daemon = True
        th.start()
        threads.append(th)

    try:
        while True:
            now_ns = monotonic_raw_ns()
            if now_ns >= end_ns:
                break

            batch = []
            while True:
                try:
                    ev = quick_q.get_nowait()
                    ev["qlat_ns"] = monotonic_raw_ns() - ev["detect_ns"]
                    batch.append(ev)
                except queue.Empty:
                    break

            if batch:
                sys.stdout.write(quick_batch(batch))
                sys.stdout.flush()

            if next_report_ns is not None and now_ns >= next_report_ns:
                elapsed_s = (now_ns - start_ns) / 1e9
                sys.stdout.write(periodic_block(elapsed_s, targets))
                sys.stdout.flush()
                next_report_ns += int(args.report * 1e9)

            time.sleep(0.0005)

    finally:
        stop_evt.set()
        for th in threads:
            th.join(timeout=0.2)

        batch = []
        while True:
            try:
                ev = quick_q.get_nowait()
                ev["qlat_ns"] = monotonic_raw_ns() - ev["detect_ns"]
                batch.append(ev)
            except queue.Empty:
                break
        if batch:
            sys.stdout.write(quick_batch(batch))

        elapsed_s = (monotonic_raw_ns() - start_ns) / 1e9
        sys.stdout.write(final_block(elapsed_s, targets))
        sys.stdout.flush()

        for fd, _unit in opened.values():
            try:
                os.close(fd)
            except Exception:
                pass


if __name__ == "__main__":
    main()
