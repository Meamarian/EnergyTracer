#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function

import argparse
import ctypes
import json
import math
import mmap
import os
import re
import shlex
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from collections import defaultdict

DEFAULT_EVENTS = [
    "power:cpu_idle",
    "power:cpu_frequency",
]

SUPPORTED_PRIMARY_CLOCKS = ("x86-tsc", "mono_raw", "mono")

TSC_WIDTH_BITS = 48
TSC_MASK = (1 << TSC_WIDTH_BITS) - 1

MSR_RAPL_POWER_UNIT = 0x606
MSR_PKG_ENERGY = 0x611
MSR_DRAM_ENERGY = 0x619

LINE_RE = re.compile(
    r"^\s*(?P<task>.+?)-(?P<pid>\d+)\s+\[(?P<cpu>\d+)\]\s+"
    r"(?:(?P<flags>\S+)\s+)?"
    r"(?P<ts>[0-9]+(?:\.[0-9]+)?):\s+"
    r"(?P<event>[^:]+):\s*(?P<body>.*)$"
)
KV_RE = re.compile(r'([A-Za-z0-9_]+)=((?:"[^"]*")|(?:[^\s]+))')
EMPTY_CPU_RE = re.compile(r"^CPU\s+(\d+)\s+is empty\s*$")

_LOG_FP = None


def log(msg):
    text = "[INFO] {}".format(msg)
    print(text, flush=True)
    if _LOG_FP:
        _LOG_FP.write(text + "\n")
        _LOG_FP.flush()


def ok(msg):
    text = "[OK] {}".format(msg)
    print(text, flush=True)
    if _LOG_FP:
        _LOG_FP.write(text + "\n")
        _LOG_FP.flush()


def warn(msg):
    text = "[WARN] {}".format(msg)
    print(text, flush=True)
    if _LOG_FP:
        _LOG_FP.write(text + "\n")
        _LOG_FP.flush()


def die(msg, code=1):
    text = "[ERROR] {}".format(msg)
    print(text, file=sys.stderr)
    if _LOG_FP:
        _LOG_FP.write(text + "\n")
        _LOG_FP.flush()
    sys.exit(code)


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def open_logger(path):
    global _LOG_FP
    _LOG_FP = open(path, "a")


def close_logger():
    global _LOG_FP
    if _LOG_FP:
        _LOG_FP.close()
        _LOG_FP = None


def mask_tsc_value(value):
    if value is None:
        return None
    return int(value) & TSC_MASK


def parse_key_value_body(body):
    out = {}
    for key, raw_val in KV_RE.findall(body):
        val = raw_val
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        else:
            try:
                if str(val).lower().startswith("0x"):
                    val = int(val, 16)
                else:
                    val = int(val)
            except Exception:
                try:
                    val = float(val)
                except Exception:
                    pass
        out[key] = val
    return out


def parse_report_line(line):
    m = LINE_RE.match(line)
    if not m:
        return None

    ts_text = m.group("ts")
    body = m.group("body")
    fields = parse_key_value_body(body)

    rec = {
        "task": m.group("task").strip(),
        "pid": int(m.group("pid")),
        "cpu": int(m.group("cpu")),
        "flags": (m.group("flags") or "").strip(),
        "raw_ts_text": ts_text,
        "event_name": m.group("event").strip(),
        "raw_body": body,
        "fields": fields,
    }

    field_cpu_id = fields.get("cpu_id")
    if isinstance(field_cpu_id, float):
        field_cpu_id = int(field_cpu_id)
    rec["field_cpu_id"] = field_cpu_id if isinstance(field_cpu_id, int) else None

    field_state = fields.get("state")
    if isinstance(field_state, float):
        field_state = int(field_state)
    rec["field_state"] = field_state if isinstance(field_state, int) else None
    return rec


def normalize_hex_mask(mask_text):
    text = str(mask_text).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    text = text.replace(",", "")
    if not text:
        raise ValueError("empty cpumask")
    int(text, 16)
    return text


def format_mask_for_kernel(mask_text):
    raw = normalize_hex_mask(mask_text)
    value = int(raw, 16)
    ncpus = os.cpu_count() or 1
    ngroups = max(1, int(math.ceil(float(ncpus) / 32.0)))
    groups = []
    for _ in range(ngroups):
        groups.append("%08x" % (value & 0xffffffff))
        value >>= 32
    return ",".join(reversed(groups))


def get_tracefs_file_path(name, explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.extend([
        os.path.join("/sys/kernel/debug/tracing", name),
        os.path.join("/sys/kernel/tracing", name),
    ])
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def write_tracefs_value(path, value):
    try:
        with open(path, "w") as fp:
            fp.write(str(value))
    except Exception as e:
        die("failed writing {}: {}".format(path, e))


def set_tracing_cpumask(mask_text, explicit_path=None):
    path = get_tracefs_file_path("tracing_cpumask", explicit_path)
    if not path:
        die("could not find tracing_cpumask sysfs path")
    kernel_mask = format_mask_for_kernel(mask_text)
    log("sysfs cpumask write equivalent: echo {} | sudo tee {}".format(kernel_mask, path))
    write_tracefs_value(path, kernel_mask)
    with open(path, "r") as fp:
        effective = fp.read().strip()
    log("set tracing_cpumask via sysfs: {} -> {}".format(path, effective))
    return path, effective


def set_tracing_on(value, explicit_path=None):
    path = get_tracefs_file_path("tracing_on", explicit_path)
    if not path:
        die("could not find tracing_on sysfs path")
    write_tracefs_value(path, int(value))
    with open(path, "r") as fp:
        effective = fp.read().strip()
    log("set tracing_on: {} -> {}".format(path, effective))
    return path, effective


class RdtscReader(object):
    def __init__(self):
        self._fn = None
        self._mmap = None
        self.available = False
        machine = os.uname().machine.lower()
        if machine not in ("x86_64", "amd64"):
            return
        try:
            code = b"\x0f\x31\x48\xc1\xe2\x20\x48\x09\xd0\xc3"
            buf = mmap.mmap(-1, len(code), prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
            buf.write(code)
            addr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
            fn_type = ctypes.CFUNCTYPE(ctypes.c_uint64)
            self._fn = fn_type(addr)
            self._mmap = buf
            _ = int(self._fn())
            self.available = True
        except Exception:
            self.available = False

    def read(self):
        if not self.available:
            return None
        try:
            return mask_tsc_value(int(self._fn()))
        except Exception:
            return None


def monotonic_raw_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)


def monotonic_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def primary_clock_and_unit(clock):
    if clock not in SUPPORTED_PRIMARY_CLOCKS:
        die("unsupported --clock for ltracer primary sync: {} (supported: {})".format(
            clock, ", ".join(SUPPORTED_PRIMARY_CLOCKS)
        ))
    if clock == "x86-tsc":
        return "x86-tsc", "cycles"
    return clock, "ns"


def get_primary_stamp(clock, rdtsc_reader):
    ts_tsc = rdtsc_reader.read() if rdtsc_reader else None
    ts_raw = monotonic_raw_ns()
    ts_mono = monotonic_ns()
    if clock == "x86-tsc":
        if ts_tsc is None:
            die("x86-tsc selected but userspace rdtsc is not available on this system")
        return ts_tsc, ts_tsc, ts_raw, ts_mono
    if clock == "mono_raw":
        return ts_raw, ts_tsc, ts_raw, ts_mono
    if clock == "mono":
        return ts_mono, ts_tsc, ts_raw, ts_mono
    die("unsupported primary clock: {}".format(clock))


def convert_delta_to_us(delta, primary_clock, tsc_hz):
    if delta is None:
        return None
    if primary_clock == "x86-tsc":
        if not tsc_hz:
            return None
        return float(delta) * 1e6 / float(tsc_hz)
    return float(delta) / 1000.0


# -----------------------------------------------------------------------------
# MSR / topology helpers for robust package+dram energy sampling
# -----------------------------------------------------------------------------

def open_msr(cpu):
    return os.open("/dev/cpu/{}/msr".format(cpu), os.O_RDONLY)


def rdmsr(fd, addr):
    return struct.unpack("<Q", os.pread(fd, 8, addr))[0]


def _read_sysfs_socket_map():
    per_socket = {}
    for p in glob_glob("/sys/devices/system/cpu/cpu[0-9]*/topology/physical_package_id"):
        try:
            sid = int(open(p, "r").read().strip())
            cpu = int(p.split("/cpu")[1].split("/")[0])
            if sid not in per_socket or cpu < per_socket[sid]:
                per_socket[sid] = cpu
        except Exception:
            continue
    return per_socket


def _read_lscpu_socket_map():
    try:
        out = subprocess.check_output(["lscpu", "-p=CPU,SOCKET"], text=True)
    except Exception:
        return {}
    per_socket = {}
    for ln in out.splitlines():
        if not ln or ln.startswith("#"):
            continue
        try:
            cpu_str, sock_str = ln.split(",")[:2]
            cpu = int(cpu_str)
            sid = int(sock_str)
            if sid not in per_socket or cpu < per_socket[sid]:
                per_socket[sid] = cpu
        except Exception:
            continue
    return per_socket


def _read_cpuinfo_socket_map():
    try:
        txt = open("/proc/cpuinfo", "r", errors="ignore").read()
    except Exception:
        return {}
    per_socket = {}
    cur_cpu = None
    cur_sock = None
    for ln in txt.splitlines():
        if ln.startswith("processor"):
            m = re.search(r"processor\s*:\s*(\d+)", ln)
            if m:
                cur_cpu = int(m.group(1))
        elif ln.startswith("physical id"):
            m = re.search(r"physical id\s*:\s*(\d+)", ln)
            if m:
                cur_sock = int(m.group(1))
        elif ln.strip() == "":
            if cur_cpu is not None and cur_sock is not None:
                if cur_sock not in per_socket or cur_cpu < per_socket[cur_sock]:
                    per_socket[cur_sock] = cur_cpu
            cur_cpu = None
            cur_sock = None
    if cur_cpu is not None and cur_sock is not None:
        if cur_sock not in per_socket or cur_cpu < per_socket[cur_sock]:
            per_socket[cur_sock] = cur_cpu
    return per_socket


def discover_socket_first_cpus():
    per_socket = _read_sysfs_socket_map() or _read_lscpu_socket_map() or _read_cpuinfo_socket_map()
    if per_socket:
        sockets = sorted(per_socket.keys())
        cpus = [per_socket[s] for s in sockets]
        return sockets, cpus
    return [0], [0]


def _safe_modprobe(module_name):
    try:
        subprocess.run(["modprobe", module_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass


def _read_energy_unit_from_fd(fd):
    pu = rdmsr(fd, MSR_RAPL_POWER_UNIT)
    return 1.0 / (2 ** ((pu >> 8) & 0x1F))


def detect_rapl_paths():
    root = "/sys/class/powercap"
    pkg = []
    dram = []
    if not os.path.isdir(root):
        return pkg, dram

    for path in sorted([p for p in glob_glob(os.path.join(root, "intel-rapl:*")) if os.path.isdir(p)]):
        name_path = os.path.join(path, "name")
        energy_path = os.path.join(path, "energy_uj")
        if not (os.path.exists(name_path) and os.path.exists(energy_path)):
            continue
        try:
            name = open(name_path, "r").read().strip().lower()
        except Exception:
            continue
        if "package" in name:
            pkg.append(energy_path)
        for sub in sorted(glob_glob(os.path.join(path, "intel-rapl:*:*"))):
            n2 = os.path.join(sub, "name")
            e2 = os.path.join(sub, "energy_uj")
            if not (os.path.exists(n2) and os.path.exists(e2)):
                continue
            try:
                nm = open(n2, "r").read().strip().lower()
            except Exception:
                continue
            if "dram" in nm:
                dram.append(e2)
    return pkg[:2], dram[:2]


def glob_glob(pattern):
    import glob
    return glob.glob(pattern)


def read_energy_j(path):
    if not path:
        return None
    try:
        return float(open(path, "r").read().strip()) / 1e6
    except Exception:
        return None


def find_acpi_power_average_path():
    import glob
    cands = sorted(glob.glob("/sys/class/hwmon/hwmon*/device/power1_average"))
    if cands:
        return cands[0]
    cands = sorted(glob.glob("/sys/class/hwmon/hwmon*/power1_average"))
    return cands[0] if cands else None


def read_acpi_uw(path):
    if not path:
        return None
    try:
        return int(open(path, "r").read().strip())
    except Exception:
        return None


def acpi_refresh_ns_for(path, min_default_ns=1000000000):
    if not path:
        return min_default_ns
    base = os.path.dirname(path)
    iv_paths = [
        os.path.join(base, "power1_average_interval"),
        os.path.join(os.path.dirname(base), "power1_average_interval"),
    ]
    refresh = min_default_ns
    for p in iv_paths:
        if os.path.exists(p):
            try:
                ms = int(open(p, "r").read().strip())
                refresh = max(refresh, int(ms * 1e6))
            except Exception:
                pass
    return refresh


def energy_sampler_loop(clock, tsc_hz, interval_ms, csv_path, stop_event, duration_s=None, start_delay_s=0.0):
    rdtsc = RdtscReader()
    if clock == "x86-tsc" and not rdtsc.available:
        die("energy sampling with --clock x86-tsc requires userspace rdtsc support")

    primary_clock, primary_unit = primary_clock_and_unit(clock)

    _safe_modprobe("msr")
    _safe_modprobe("acpi_power_meter")

    sockets, cpus = discover_socket_first_cpus()
    msr_fds = []
    energy_unit = None
    for cpu in cpus:
        try:
            fd = open_msr(cpu)
            msr_fds.append((cpu, fd))
        except Exception as e:
            warn("could not open /dev/cpu/{}/msr: {}".format(cpu, e))

    if msr_fds:
        try:
            energy_unit = _read_energy_unit_from_fd(msr_fds[0][1])
        except Exception as e:
            warn("failed to read RAPL energy unit from MSR: {}".format(e))
            energy_unit = None

    acpi_path = find_acpi_power_average_path()
    acpi_refresh_ns = acpi_refresh_ns_for(acpi_path)

    log("energy primary clock={}".format(primary_clock))
    log("energy primary unit={}".format(primary_unit))
    log("msr sockets discovered={}".format(sockets))
    log("msr cpu per socket={}".format(cpus))
    log("msr fds opened={}".format([cpu for cpu, _fd in msr_fds]))
    log("msr energy unit J/tick={}".format("" if energy_unit is None else repr(energy_unit)))
    log("acpi path={}".format(acpi_path))
    log("acpi refresh ns={}".format(acpi_refresh_ns))
    log("energy start delay s={}".format(start_delay_s))

    if start_delay_s and start_delay_s > 0:
        log("energy sampler waiting {} seconds before first sample".format(start_delay_s))
        deadline = monotonic_raw_ns() + int(float(start_delay_s) * 1e9)
        while not stop_event.is_set() and monotonic_raw_ns() < deadline:
            time.sleep(0.05)
        if stop_event.is_set():
            warn("energy sampler stopped before delay elapsed")
            for _cpu, fd in msr_fds:
                try:
                    os.close(fd)
                except Exception:
                    pass
            return
    else:
        log("energy sampler starts immediately in parallel with trace-cmd")

    interval_ns = int(round(float(interval_ms) * 1e6))
    next_deadline = monotonic_raw_ns()
    start_wall = monotonic_raw_ns()
    end_wall = start_wall + int(duration_s * 1e9) if duration_s is not None else None

    acpi_last_val = None
    acpi_last_read_ns = 0

    try:
        with open(csv_path, "w") as fp:
            fp.write(
                "primary_clock,primary_unit,ts_primary,ts_tsc,ts_raw_ns,ts_mono_ns,"
                "pkg_j_sock0,pkg_j_sock1,dram_j_sock0,dram_j_sock1,acpi_uW\n"
            )
            fp.flush()

            while not stop_event.is_set():
                now = monotonic_raw_ns()
                if end_wall is not None and now >= end_wall:
                    break
                if now < next_deadline:
                    rem_s = max(0.0, float(next_deadline - now) / 1e9)
                    time.sleep(min(rem_s, 0.05))
                    continue
                next_deadline += interval_ns

                ts_primary, ts_tsc, ts_raw_ns, ts_mono_ns = get_primary_stamp(clock, rdtsc)

                pkg_vals = []
                dram_vals = []
                if energy_unit is not None:
                    for _cpu, fd in msr_fds[:2]:
                        try:
                            cur_pkg = rdmsr(fd, MSR_PKG_ENERGY) & 0xffffffff
                            pkg_vals.append(cur_pkg * energy_unit)
                        except Exception:
                            pkg_vals.append(None)
                        try:
                            cur_dram = rdmsr(fd, MSR_DRAM_ENERGY) & 0xffffffff
                            dram_vals.append(cur_dram * energy_unit)
                        except Exception:
                            dram_vals.append(None)

                while len(pkg_vals) < 2:
                    pkg_vals.append(None)
                while len(dram_vals) < 2:
                    dram_vals.append(None)

                if acpi_path:
                    if (ts_raw_ns - acpi_last_read_ns) >= acpi_refresh_ns or acpi_last_val is None:
                        acpi_last_val = read_acpi_uw(acpi_path)
                        acpi_last_read_ns = ts_raw_ns
                acpi_val = acpi_last_val

                row = [
                    primary_clock,
                    primary_unit,
                    str(ts_primary),
                    "" if ts_tsc is None else str(ts_tsc),
                    str(ts_raw_ns),
                    str(ts_mono_ns),
                    "" if pkg_vals[0] is None else repr(pkg_vals[0]),
                    "" if pkg_vals[1] is None else repr(pkg_vals[1]),
                    "" if dram_vals[0] is None else repr(dram_vals[0]),
                    "" if dram_vals[1] is None else repr(dram_vals[1]),
                    "" if acpi_val is None else str(acpi_val),
                ]
                fp.write(",".join(row) + "\n")
                fp.flush()
    finally:
        for _cpu, fd in msr_fds:
            try:
                os.close(fd)
            except Exception:
                pass

    ok("energy sampling finished: {}".format(csv_path))


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-200000")

    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kernel_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " primary_clock TEXT NOT NULL,"
        " primary_unit TEXT NOT NULL,"
        " raw_ts_text TEXT NOT NULL,"
        " ts_primary INTEGER NOT NULL,"
        " ts_cycles INTEGER,"
        " ts_time_ns INTEGER,"
        " rel_primary INTEGER,"
        " rel_primary_us REAL,"
        " cpu INTEGER,"
        " task TEXT,"
        " pid INTEGER,"
        " flags TEXT,"
        " event_name TEXT,"
        " raw_body TEXT,"
        " field_cpu_id INTEGER,"
        " field_state INTEGER,"
        " fields_json TEXT"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kernel_events_ts_primary ON kernel_events(ts_primary)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kernel_events_cpu ON kernel_events(cpu)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kernel_events_name ON kernel_events(event_name)")

    conn.execute("CREATE TABLE IF NOT EXISTS kernel_event_counts (event_name TEXT PRIMARY KEY, cnt INTEGER NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kernel_idle_state_counts ("
        " cpu_id INTEGER, state INTEGER, cnt INTEGER NOT NULL, PRIMARY KEY(cpu_id, state))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kernel_frequency_state_counts ("
        " cpu_id INTEGER, state INTEGER, cnt INTEGER NOT NULL, PRIMARY KEY(cpu_id, state))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kernel_pstate_event_counts ("
        " event_name TEXT PRIMARY KEY, cnt INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS energy_samples ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " primary_clock TEXT NOT NULL,"
        " primary_unit TEXT NOT NULL,"
        " ts_primary INTEGER NOT NULL,"
        " ts_tsc INTEGER,"
        " ts_raw_ns INTEGER,"
        " ts_mono_ns INTEGER,"
        " rel_primary INTEGER,"
        " rel_primary_us REAL,"
        " pkg_j_sock0 REAL,"
        " pkg_j_sock1 REAL,"
        " dram_j_sock0 REAL,"
        " dram_j_sock1 REAL,"
        " acpi_uW INTEGER"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_energy_ts_primary ON energy_samples(ts_primary)")
    conn.commit()
    return conn


def put_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))


def write_trace_dat_check(summary_path, trace_file, report_stdout, parsed_lines, skipped_lines, cpu_event_counts, empty_cpus):
    try:
        trace_size = os.path.getsize(trace_file) if os.path.exists(trace_file) else -1
    except Exception:
        trace_size = -1

    cpus_with_data = sorted(cpu_event_counts.keys())
    total_events = sum(cpu_event_counts.values())

    with open(summary_path, "w") as fp:
        fp.write("TRACE.DAT CHECK\n")
        fp.write("===============\n\n")
        fp.write("trace_file: {}\n".format(trace_file))
        fp.write("trace_size_bytes: {}\n".format(trace_size))
        fp.write("trace_empty_by_size: {}\n".format(1 if trace_size == 0 else 0))
        fp.write("parsed_event_lines: {}\n".format(parsed_lines))
        fp.write("skipped_lines: {}\n".format(skipped_lines))
        fp.write("empty_cpu_count: {}\n".format(len(empty_cpus)))
        fp.write("cpus_with_data_count: {}\n".format(len(cpus_with_data)))
        fp.write("total_event_count: {}\n\n".format(total_events))

        fp.write("empty_cpus:\n")
        if empty_cpus:
            fp.write("  {}\n\n".format(", ".join(str(x) for x in empty_cpus)))
        else:
            fp.write("  none\n\n")

        fp.write("cpus_with_data:\n")
        if cpus_with_data:
            for cpu in cpus_with_data:
                fp.write("  cpu {} -> {} event(s)\n".format(cpu, cpu_event_counts[cpu]))
        else:
            fp.write("  none\n")

    if trace_size == 0:
        warn("trace.dat exists but file size is 0 bytes")
    else:
        log("trace.dat size bytes={}".format(trace_size))

    if cpus_with_data:
        summary = ", ".join("cpu{}={}".format(cpu, cpu_event_counts[cpu]) for cpu in cpus_with_data)
        log("trace.dat cpus with data: {}".format(summary))
    else:
        warn("trace.dat has no parsed event lines")

    if empty_cpus:
        log("trace.dat empty cpu count={}".format(len(empty_cpus)))


def export_trace(trace_cmd, trace_file, report_txt, report_stderr_txt, jsonl_path, db_path, clock, tsc_hz, summary_path):
    primary_clock, primary_unit = primary_clock_and_unit(clock)
    conn = init_db(db_path)
    put_meta(conn, "trace_file", os.path.abspath(trace_file))
    put_meta(conn, "primary_clock", primary_clock)
    put_meta(conn, "primary_unit", primary_unit)
    put_meta(conn, "tsc_hz", tsc_hz if tsc_hz is not None else "")

    cmd = [trace_cmd, "report", "-t", "-i", trace_file]
    log("exporting report: {}".format(" ".join(cmd)))
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    with open(report_txt, "w") as fp:
        fp.write(proc.stdout or "")
    with open(report_stderr_txt, "w") as fp:
        fp.write(proc.stderr or "")
    if proc.returncode != 0:
        die("trace-cmd report failed: {}".format((proc.stderr or "").strip() or proc.returncode))

    first_primary = None
    batch = []
    event_counts = defaultdict(int)
    idle_counts = defaultdict(int)
    freq_counts = defaultdict(int)
    pstate_counts = defaultdict(int)
    cpu_event_counts = defaultdict(int)
    empty_cpus = []
    parsed_lines = 0
    skipped_lines = 0

    jsonl_fp = open(jsonl_path, "w") if jsonl_path else None
    for line in (proc.stdout or "").splitlines():
        m_empty = EMPTY_CPU_RE.match(line.strip())
        if m_empty:
            empty_cpus.append(int(m_empty.group(1)))
            continue

        rec = parse_report_line(line)
        if rec is None:
            skipped_lines += 1
            continue
        parsed_lines += 1

        ts_primary = None
        ts_cycles = None
        ts_time_ns = None
        raw_ts = rec["raw_ts_text"]
        if primary_clock == "x86-tsc":
            try:
                ts_primary = mask_tsc_value(int(raw_ts))
                ts_cycles = ts_primary
            except Exception:
                warn("non-integer timestamp for x86-tsc line skipped: {}".format(raw_ts))
                continue
        else:
            try:
                ts_primary = int(round(float(raw_ts) * 1e9))
                ts_time_ns = ts_primary
            except Exception:
                warn("non-float timestamp for {} line skipped: {}".format(primary_clock, raw_ts))
                continue

        if first_primary is None:
            first_primary = ts_primary
        rel_primary = ts_primary - first_primary
        rel_primary_us = convert_delta_to_us(rel_primary, primary_clock, tsc_hz)

        row = (
            primary_clock,
            primary_unit,
            rec["raw_ts_text"],
            ts_primary,
            ts_cycles,
            ts_time_ns,
            rel_primary,
            rel_primary_us,
            rec["cpu"],
            rec["task"],
            rec["pid"],
            rec["flags"],
            rec["event_name"],
            rec["raw_body"],
            rec["field_cpu_id"],
            rec["field_state"],
            json.dumps(rec["fields"], sort_keys=True),
        )
        batch.append(row)

        cpu_event_counts[rec["cpu"]] += 1
        event_counts[rec["event_name"]] += 1
        if rec["event_name"].endswith("cpu_idle") or rec["event_name"] == "cpu_idle":
            cpu_id = rec["field_cpu_id"] if rec["field_cpu_id"] is not None else rec["cpu"]
            state = rec["field_state"]
            if state is not None:
                idle_counts[(cpu_id, state)] += 1
        elif rec["event_name"].endswith("cpu_frequency") or rec["event_name"] == "cpu_frequency":
            cpu_id = rec["field_cpu_id"] if rec["field_cpu_id"] is not None else rec["cpu"]
            state = rec["field_state"]
            if state is not None:
                freq_counts[(cpu_id, state)] += 1
        elif "pstate" in rec["event_name"]:
            pstate_counts[rec["event_name"]] += 1

        if jsonl_fp:
            out = dict(rec)
            out["primary_clock"] = primary_clock
            out["primary_unit"] = primary_unit
            out["ts_primary"] = ts_primary
            out["ts_cycles"] = ts_cycles
            out["ts_time_ns"] = ts_time_ns
            out["rel_primary"] = rel_primary
            out["rel_primary_us"] = rel_primary_us
            jsonl_fp.write(json.dumps(out, sort_keys=True) + "\n")

        if len(batch) >= 5000:
            conn.executemany(
                "INSERT INTO kernel_events("
                " primary_clock, primary_unit, raw_ts_text, ts_primary, ts_cycles, ts_time_ns, rel_primary, rel_primary_us,"
                " cpu, task, pid, flags, event_name, raw_body, field_cpu_id, field_state, fields_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            conn.commit()
            batch = []

    if batch:
        conn.executemany(
            "INSERT INTO kernel_events("
            " primary_clock, primary_unit, raw_ts_text, ts_primary, ts_cycles, ts_time_ns, rel_primary, rel_primary_us,"
            " cpu, task, pid, flags, event_name, raw_body, field_cpu_id, field_state, fields_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )

    conn.execute("DELETE FROM kernel_event_counts")
    conn.executemany(
        "INSERT INTO kernel_event_counts(event_name, cnt) VALUES(?, ?)",
        sorted(event_counts.items()),
    )

    conn.execute("DELETE FROM kernel_idle_state_counts")
    conn.executemany(
        "INSERT INTO kernel_idle_state_counts(cpu_id, state, cnt) VALUES(?, ?, ?)",
        [(cpu_id, state, cnt) for (cpu_id, state), cnt in sorted(idle_counts.items())],
    )

    conn.execute("DELETE FROM kernel_frequency_state_counts")
    conn.executemany(
        "INSERT INTO kernel_frequency_state_counts(cpu_id, state, cnt) VALUES(?, ?, ?)",
        [(cpu_id, state, cnt) for (cpu_id, state), cnt in sorted(freq_counts.items())],
    )

    conn.execute("DELETE FROM kernel_pstate_event_counts")
    conn.executemany(
        "INSERT INTO kernel_pstate_event_counts(event_name, cnt) VALUES(?, ?)",
        sorted(pstate_counts.items()),
    )

    put_meta(conn, "kernel_report_parsed_lines", parsed_lines)
    put_meta(conn, "kernel_report_skipped_lines", skipped_lines)
    put_meta(conn, "kernel_first_primary", first_primary if first_primary is not None else "")
    put_meta(conn, "kernel_record_count", conn.execute("SELECT COUNT(*) FROM kernel_events").fetchone()[0])
    conn.commit()
    conn.close()
    if jsonl_fp:
        jsonl_fp.close()

    write_trace_dat_check(summary_path, trace_file, proc.stdout or "", parsed_lines, skipped_lines, cpu_event_counts, empty_cpus)
    ok("kernel export complete: {}".format(db_path))


def import_energy_csv(conn, csv_path, primary_clock, tsc_hz):
    if not os.path.exists(csv_path):
        warn("energy csv not found, skipping import: {}".format(csv_path))
        return
    conn.execute("DELETE FROM energy_samples")
    first_primary = None
    batch = []
    with open(csv_path, "r") as fp:
        header = fp.readline()
        for line in fp:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != 11:
                continue
            p_clock, p_unit, ts_primary_s, ts_tsc_s, ts_raw_s, ts_mono_s, pkg0_s, pkg1_s, dram0_s, dram1_s, acpi_s = parts
            ts_primary = int(ts_primary_s)
            ts_tsc = int(ts_tsc_s) if ts_tsc_s else None
            ts_raw = int(ts_raw_s) if ts_raw_s else None
            ts_mono = int(ts_mono_s) if ts_mono_s else None
            pkg0 = float(pkg0_s) if pkg0_s else None
            pkg1 = float(pkg1_s) if pkg1_s else None
            dram0 = float(dram0_s) if dram0_s else None
            dram1 = float(dram1_s) if dram1_s else None
            acpi = int(acpi_s) if acpi_s else None
            if first_primary is None:
                first_primary = ts_primary
            rel_primary = ts_primary - first_primary
            rel_primary_us = convert_delta_to_us(rel_primary, primary_clock, tsc_hz)
            batch.append((p_clock, p_unit, ts_primary, ts_tsc, ts_raw, ts_mono, rel_primary, rel_primary_us, pkg0, pkg1, dram0, dram1, acpi))
            if len(batch) >= 5000:
                conn.executemany(
                    "INSERT INTO energy_samples("
                    " primary_clock, primary_unit, ts_primary, ts_tsc, ts_raw_ns, ts_mono_ns, rel_primary, rel_primary_us,"
                    " pkg_j_sock0, pkg_j_sock1, dram_j_sock0, dram_j_sock1, acpi_uW"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
                conn.commit()
                batch = []
    if batch:
        conn.executemany(
            "INSERT INTO energy_samples("
            " primary_clock, primary_unit, ts_primary, ts_tsc, ts_raw_ns, ts_mono_ns, rel_primary, rel_primary_us,"
            " pkg_j_sock0, pkg_j_sock1, dram_j_sock0, dram_j_sock1, acpi_uW"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
    put_meta(conn, "energy_first_primary", first_primary if first_primary is not None else "")
    put_meta(conn, "energy_record_count", conn.execute("SELECT COUNT(*) FROM energy_samples").fetchone()[0])
    conn.commit()
    ok("energy import complete")


def dump_db_structure(conn, path):
    with open(path, "w") as fp:
        fp.write("DATABASE STRUCTURE\n")
        fp.write("==================\n\n")
        tables = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for name, sql in tables:
            fp.write(name + "\n")
            fp.write("-" * len(name) + "\n")
            fp.write((sql or "") + "\n\n")
            cols = conn.execute("PRAGMA table_info('{}')".format(name.replace("'", "''"))).fetchall()
            for col in cols:
                fp.write("  - {} {} notnull={} pk={} default={}\n".format(col[1], col[2], col[3], col[5], col[4]))
            fp.write("\n")
            idxs = conn.execute("PRAGMA index_list('{}')".format(name.replace("'", "''"))).fetchall()
            if idxs:
                fp.write("  Indexes:\n")
                for idx in idxs:
                    fp.write("    - {} unique={} origin={} partial={}\n".format(idx[1], idx[2], idx[3], idx[4]))
                fp.write("\n")


def write_output_hierarchy(base_dir, path):
    with open(path, "w") as fp:
        fp.write("OUTPUT HIERARCHY\n")
        fp.write("================\n\n")
        for root, dirs, files in os.walk(base_dir):
            dirs.sort()
            files.sort()
            rel = os.path.relpath(root, base_dir)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            indent = "  " * depth
            name = os.path.basename(root) if rel != "." else os.path.basename(base_dir.rstrip(os.sep)) or base_dir
            fp.write("{}{}{}/\n".format(indent, "" if depth == 0 else "- ", name))
            for fn in files:
                fpath = os.path.join(root, fn)
                try:
                    size = os.path.getsize(fpath)
                except Exception:
                    size = -1
                fp.write("{}  - {} ({})\n".format(indent, fn, size))


def build_record_cmd(args, trace_file):
    events = args.event if args.event else list(DEFAULT_EVENTS)
    cmd = []
    if args.taskset_cpus:
        taskset = shutil.which("taskset")
        if not taskset:
            die("taskset not found, but --taskset-cpus was provided")
        cmd.extend([taskset, "-c", args.taskset_cpus])

    cmd.extend([args.trace_cmd, "record", "-o", trace_file, "-C", args.clock])
    if args.buffer_size_kb:
        cmd.extend(["-b", str(args.buffer_size_kb)])
    if args.sleep_us is not None:
        cmd.extend(["-s", str(args.sleep_us)])
    if args.priority is not None:
        cmd.extend(["-r", str(args.priority)])
    if args.cpulist:
        cmd.extend(["-M", normalize_hex_mask(args.cpulist)])
    for ev in events:
        cmd.extend(["-e", ev])
    for raw in args.trace_cmd_arg:
        if raw:
            cmd.extend(shlex.split(raw))

    if args.duration is not None and args.command:
        die("use either -d/--duration or a command after --, not both")
    if args.duration is not None:
        cmd.extend(["--", "sleep", str(args.duration)])
    elif args.command:
        cmd.append("--")
        cmd.extend(args.command)
    return cmd


def cmd_record(args):
    trace_cmd = shutil.which(args.trace_cmd)
    if not trace_cmd:
        die("trace-cmd not found: {}".format(args.trace_cmd))
    args.trace_cmd = trace_cmd

    if args.clock not in SUPPORTED_PRIMARY_CLOCKS:
        die("unsupported --clock {} for ltracer (supported: {})".format(args.clock, ", ".join(SUPPORTED_PRIMARY_CLOCKS)))
    if args.clock == "x86-tsc" and not args.tsc_hz:
        die("--clock x86-tsc requires --tsc-hz so rel_primary_us can be computed")

    base_dir = os.path.abspath(args.run_dir)
    linux_dir = os.path.join(base_dir, "linux")
    ensure_dir(linux_dir)
    open_logger(os.path.join(linux_dir, "ltracer.log"))

    trace_file = os.path.join(linux_dir, "trace.dat")
    report_txt = os.path.join(linux_dir, "kernel_report.txt")
    report_stderr_txt = os.path.join(linux_dir, "kernel_report.stderr.txt")
    jsonl_path = os.path.join(linux_dir, "kernel_events.jsonl")
    energy_csv = os.path.join(linux_dir, "energy_samples.csv")
    trace_check = os.path.join(linux_dir, "trace_dat_check.txt")
    db_path = os.path.join(linux_dir, "linux.sqlite")
    trace_stdout = os.path.join(linux_dir, "trace_cmd.stdout.log")
    trace_stderr = os.path.join(linux_dir, "trace_cmd.stderr.log")
    db_struct = os.path.join(linux_dir, "db_structure.txt")
    out_hierarchy = os.path.join(linux_dir, "output_hierarchy.txt")

    log("run_dir={}".format(base_dir))
    log("linux_dir={}".format(linux_dir))
    log("trace_file={}".format(trace_file))
    log("db_path={}".format(db_path))
    log("clock={}".format(args.clock))
    log("duration={}".format(args.duration))
    log("taskset_cpus={}".format(args.taskset_cpus))
    log("cpulist={}".format(args.cpulist))
    log("mask_via_sysfs={}".format(args.mask_via_sysfs))
    log("set_tracing_on={}".format(args.set_tracing_on))
    log("energy_interval_ms={}".format(args.energy_interval_ms))
    log("energy_start_delay_s={}".format(args.energy_start_delay_s))
    log("tsc_hz={}".format(args.tsc_hz))

    if args.mask_via_sysfs:
        if not args.cpulist:
            die("--mask-via-sysfs requires --cpulist")
        set_tracing_cpumask(args.cpulist, args.tracing_cpumask_path)

    if args.set_tracing_on:
        set_tracing_on(1, args.tracing_on_path)

    cmd = build_record_cmd(args, trace_file)
    log("record command: {}".format(" ".join(cmd)))
    if args.duration is not None:
        log("trace-cmd will stop automatically after {} seconds".format(args.duration))

    out_fp = open(trace_stdout, "w")
    err_fp = open(trace_stderr, "w")
    proc = None
    rc = None
    stop_event = threading.Event()
    energy_thread = None
    try:
        proc = subprocess.Popen(cmd, stdout=out_fp, stderr=err_fp, preexec_fn=os.setsid)

        if args.energy_interval_ms is not None:
            log("energy sampling enabled: interval_ms={}".format(args.energy_interval_ms))
            log("energy sampler thread starts in parallel after trace-cmd Popen")
            energy_thread = threading.Thread(
                target=energy_sampler_loop,
                args=(args.clock, args.tsc_hz, args.energy_interval_ms, energy_csv, stop_event, args.duration, args.energy_start_delay_s),
                daemon=True,
            )
            energy_thread.start()
        else:
            log("energy sampling disabled")

        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            warn("KeyboardInterrupt received; asking trace-cmd to stop cleanly")
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except Exception as e:
                warn("failed to send SIGINT to trace-cmd group: {}".format(e))
            try:
                rc = proc.wait(timeout=20)
            except Exception:
                warn("trace-cmd did not exit after SIGINT; sending SIGTERM")
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception as e:
                    warn("failed to send SIGTERM to trace-cmd group: {}".format(e))
                rc = proc.wait()
    finally:
        stop_event.set()
        if energy_thread is not None:
            energy_thread.join(timeout=5)
        out_fp.close()
        err_fp.close()
        if args.set_tracing_on:
            try:
                set_tracing_on(0, args.tracing_on_path)
            except Exception as e:
                warn("failed to set tracing_on=0: {}".format(e))

    log("trace-cmd record returned rc={}".format(rc))
    if rc != 0:
        die("trace-cmd record failed with rc={}".format(rc))
    if not os.path.exists(trace_file):
        die("trace.dat was not produced: {}".format(trace_file))

    log("starting post-record export")
    export_trace(args.trace_cmd, trace_file, report_txt, report_stderr_txt, jsonl_path, db_path, args.clock, args.tsc_hz, trace_check)
    conn = sqlite3.connect(db_path)
    put_meta(conn, "run_dir", base_dir)
    put_meta(conn, "trace_clock", args.clock)
    put_meta(conn, "trace_cpulist", args.cpulist or "")
    put_meta(conn, "mask_via_sysfs", int(bool(args.mask_via_sysfs)))
    put_meta(conn, "set_tracing_on", int(bool(args.set_tracing_on)))
    put_meta(conn, "energy_interval_ms", args.energy_interval_ms if args.energy_interval_ms is not None else "")
    put_meta(conn, "energy_start_delay_s", args.energy_start_delay_s if args.energy_interval_ms is not None else "")
    if os.path.exists(energy_csv):
        import_energy_csv(conn, energy_csv, args.clock, args.tsc_hz)
    dump_db_structure(conn, db_struct)
    conn.commit()
    conn.close()
    write_output_hierarchy(linux_dir, out_hierarchy)
    ok("ltracer finished successfully")
    close_logger()


def cmd_export(args):
    trace_cmd = shutil.which(args.trace_cmd)
    if not trace_cmd:
        die("trace-cmd not found: {}".format(args.trace_cmd))
    if args.clock not in SUPPORTED_PRIMARY_CLOCKS:
        die("unsupported --clock {} for ltracer export".format(args.clock))
    if args.clock == "x86-tsc" and not args.tsc_hz:
        die("--clock x86-tsc requires --tsc-hz")

    base_dir = os.path.abspath(args.run_dir)
    linux_dir = os.path.join(base_dir, "linux")
    ensure_dir(linux_dir)
    open_logger(os.path.join(linux_dir, "ltracer.log"))

    trace_file = os.path.abspath(args.input)
    report_txt = os.path.join(linux_dir, "kernel_report.txt")
    report_stderr_txt = os.path.join(linux_dir, "kernel_report.stderr.txt")
    jsonl_path = os.path.join(linux_dir, "kernel_events.jsonl")
    trace_check = os.path.join(linux_dir, "trace_dat_check.txt")
    db_path = os.path.join(linux_dir, "linux.sqlite")
    db_struct = os.path.join(linux_dir, "db_structure.txt")
    out_hierarchy = os.path.join(linux_dir, "output_hierarchy.txt")

    export_trace(trace_cmd, trace_file, report_txt, report_stderr_txt, jsonl_path, db_path, args.clock, args.tsc_hz, trace_check)
    conn = sqlite3.connect(db_path)
    put_meta(conn, "run_dir", base_dir)
    put_meta(conn, "trace_clock", args.clock)
    dump_db_structure(conn, db_struct)
    conn.commit()
    conn.close()
    write_output_hierarchy(linux_dir, out_hierarchy)
    ok("export finished successfully")
    close_logger()


def make_parser():
    parser = argparse.ArgumentParser(
        description="Kernel tracer/exporter with same primary unit for kernel and energy timestamps."
    )
    sub = parser.add_subparsers(dest="subcmd")
    sub.required = True

    rec = sub.add_parser("record", help="Record a kernel trace and export it.")
    rec.add_argument("--run-dir", required=True, help="Experiment root directory")
    rec.add_argument("--trace-cmd", default="trace-cmd", help="trace-cmd binary")
    rec.add_argument("--clock", default="x86-tsc", choices=SUPPORTED_PRIMARY_CLOCKS, help="primary trace clock")
    rec.add_argument("--taskset-cpus", default=None, help="CPU list for the user-space trace-cmd recorder")
    rec.add_argument("--cpulist", default=None, help="HEX cpumask for trace-cmd -M and optional sysfs tracing_cpumask write")
    rec.add_argument("--mask-via-sysfs", action="store_true", help="also set tracing_cpumask directly via sysfs write before trace-cmd starts")
    rec.add_argument("--tracing-cpumask-path", default=None, help="explicit tracing_cpumask sysfs path")
    rec.add_argument("--set-tracing-on", action="store_true", help="also force tracing_on=1 before record and tracing_on=0 after record")
    rec.add_argument("--tracing-on-path", default=None, help="explicit tracing_on sysfs path")
    rec.add_argument("--buffer-size-kb", type=int, default=None, help="trace-cmd -b")
    rec.add_argument("--sleep-us", type=int, default=1000, help="trace-cmd -s in microseconds")
    rec.add_argument("--priority", type=int, default=None, help="trace-cmd -r")
    rec.add_argument("--tsc-hz", type=int, default=None, help="TSC frequency in cycles per second")
    rec.add_argument("--energy-interval-ms", type=float, default=None, help="energy sampling interval in milliseconds")
    rec.add_argument("--energy-start-delay-s", type=float, default=0.0, help="optional delay before energy sampling starts; default disabled")
    rec.add_argument("-d", "--duration", type=float, default=None, help="record duration in seconds; uses 'trace-cmd record -- sleep DURATION'")
    rec.add_argument("-e", "--event", action="append", default=[], help="event to record; repeatable")
    rec.add_argument("--trace-cmd-arg", action="append", default=[], help="extra raw argument string forwarded to trace-cmd")
    rec.add_argument("command", nargs=argparse.REMAINDER, help="optional command after --; mutually exclusive with -d")
    rec.set_defaults(func=cmd_record)

    exp = sub.add_parser("export", help="Export an existing trace.dat into JSONL + SQLite.")
    exp.add_argument("--run-dir", required=True, help="Experiment root directory")
    exp.add_argument("-i", "--input", required=True, help="Existing trace.dat")
    exp.add_argument("--trace-cmd", default="trace-cmd", help="trace-cmd binary")
    exp.add_argument("--clock", default="x86-tsc", choices=SUPPORTED_PRIMARY_CLOCKS, help="primary trace clock used when the trace was recorded")
    exp.add_argument("--tsc-hz", type=int, default=None, help="TSC frequency in cycles per second")
    exp.set_defaults(func=cmd_export)
    return parser


def main():
    parser = make_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
