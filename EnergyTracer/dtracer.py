#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function

import argparse
import collections
import glob
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile

import bt2


EVENT_MSG_TYPE = bt2._EventMessageConst
ROOT_NONE = -1
ROOT_PAYLOAD = 0
ROOT_SPEC_CTX = 1
ROOT_COMMON_CTX = 2

NB_FIELD_CANDIDATES = ("nb_rx", "nb_pkts", "count")
PORT_FIELD_CANDIDATES = ("port", "port_id", "portid")
LCORE_FIELD_CANDIDATES = ("lcore", "queue_id", "qid")
_CTF_FS_CC = None


def log(msg):
    print("[INFO] {}".format(msg), flush=True)


def ok(msg):
    print("[OK] {}".format(msg), flush=True)


def warn(msg):
    print("[WARN] {}".format(msg), flush=True)


def die(msg, code=1):
    print("[ERROR] {}".format(msg), file=sys.stderr)
    sys.exit(code)


def run_cmd(cmd):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )


def ns_to_s(ns):
    return float(ns) / 1e9


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def _get_ctf_fs_cc():
    global _CTF_FS_CC
    if _CTF_FS_CC is None:
        plugin = bt2.find_plugin("ctf")
        if plugin is None:
            die("bt2 could not find the 'ctf' plugin")
        try:
            _CTF_FS_CC = plugin.source_component_classes["fs"]
        except Exception:
            die("bt2 'ctf' plugin does not expose source component class 'fs'")
    return _CTF_FS_CC


def path_access_looks_ok(trace_dir):
    run_dir = os.path.dirname(trace_dir)
    metadata = os.path.join(trace_dir, "metadata")
    chans = sorted(glob.glob(os.path.join(trace_dir, "channel0_*")))

    if not os.path.isdir(run_dir):
        return False
    if not os.path.isdir(trace_dir):
        return False
    if not os.path.isfile(metadata):
        return False
    if not os.access(run_dir, os.R_OK | os.X_OK):
        return False
    if not os.access(trace_dir, os.R_OK | os.X_OK):
        return False
    if not os.access(metadata, os.R_OK):
        return False
    if chans and not os.access(chans[0], os.R_OK):
        return False
    return True


def try_recursive_chmod(path):
    r = run_cmd(["chmod", "-R", "a+rwx", path])
    if r.returncode == 0:
        return True
    r = run_cmd(["sudo", "chmod", "-R", "a+rwx", path])
    return r.returncode == 0


def ensure_access(trace_dir):
    run_dir = os.path.dirname(trace_dir)
    if path_access_looks_ok(trace_dir):
        return
    try_recursive_chmod(run_dir)
    try_recursive_chmod(trace_dir)
    if not path_access_looks_ok(trace_dir):
        die("trace path still does not look readable after chmod")


def patch_metadata_size_t(trace_dir):
    metadata_path = os.path.join(trace_dir, "metadata")
    if not os.path.exists(metadata_path):
        die("metadata not found: {}".format(metadata_path))

    with open(metadata_path, "r") as fp:
        text = fp.read()

    if ":= size_t;" in text:
        return

    needle = "string_bounded_t;"
    if needle not in text:
        warn("metadata does not contain '{}' anchor; skipping auto-patch".format(needle))
        return

    patched = text.replace(
        needle,
        needle + "\n" + "typealias integer {size = 64; base = x;} := size_t;",
        1,
    )

    try:
        with open(metadata_path, "w") as fp:
            fp.write(patched)
        ok("metadata patched with size_t alias")
        return
    except Exception:
        pass

    fd, tmp_path = tempfile.mkstemp(prefix="metadata_patched_", text=True)
    os.close(fd)
    try:
        with open(tmp_path, "w") as fp:
            fp.write(patched)
        r = run_cmd(["sudo", "cp", tmp_path, metadata_path])
        if r.returncode != 0:
            die("failed to patch metadata with sudo: {}".format(r.stderr.strip()))
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def open_trace(trace_dir):
    return bt2.TraceCollectionMessageIterator(
        bt2.ComponentSpec(_get_ctf_fs_cc(), {"inputs": [trace_dir]})
    )


def is_rx_burst_family_name(name):
    s = name.lower()
    return ("ethdev" in s) and ("rx" in s) and ("burst" in s)


def detect_field_location(ev, candidates):
    for root_code, getter in [
        (ROOT_PAYLOAD, lambda e: e.payload_field),
        (ROOT_SPEC_CTX, lambda e: e.specific_context_field),
        (ROOT_COMMON_CTX, lambda e: e.common_context_field),
    ]:
        try:
            root = getter(ev)
            if root is not None:
                for fn in candidates:
                    if fn in root:
                        return root_code, fn
        except Exception:
            pass
    return ROOT_NONE, None


def get_field_value(ev, root_code, field_name):
    try:
        if root_code == ROOT_PAYLOAD:
            return ev.payload_field[field_name]
        if root_code == ROOT_SPEC_CTX:
            return ev.specific_context_field[field_name]
        if root_code == ROOT_COMMON_CTX:
            return ev.common_context_field[field_name]
    except Exception:
        pass
    return None


def normalize_scalar(value):
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return float(value)
        text = str(value)
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)
    except Exception:
        try:
            return float(value)
        except Exception:
            try:
                return str(value)
            except Exception:
                return None


def simple_root_to_dict(root):
    out = {}
    if root is None:
        return out
    try:
        for key in root:
            val = normalize_scalar(root[key])
            if val is not None:
                out[str(key)] = val
    except Exception:
        pass
    return out


def collect_event_fields(ev):
    merged = {}
    try:
        merged.update(simple_root_to_dict(ev.common_context_field))
    except Exception:
        pass
    try:
        merged.update(simple_root_to_dict(ev.specific_context_field))
    except Exception:
        pass
    try:
        merged.update(simple_root_to_dict(ev.payload_field))
    except Exception:
        pass
    return merged


def read_key_value_file(path):
    vals = {}
    with open(path, "r") as fp:
        for line in fp:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            vals[key.strip()] = value.strip()
    return vals


def parse_int(value, default=None):
    if value is None:
        return default
    try:
        if isinstance(value, int):
            return value
        if str(value).lower().startswith("0x"):
            return int(value, 16)
        return int(value)
    except Exception:
        return default


def read_anchor(path):
    vals = read_key_value_file(path)
    out = {
        "path": os.path.abspath(path),
        "tsc": parse_int(vals.get("tsc")),
        "tsc_hz": parse_int(vals.get("tsc_hz")),
        "mono_raw_ns": parse_int(vals.get("mono_raw_ns")),
    }
    if out["tsc"] is None or out["tsc_hz"] is None:
        die("anchor file must contain at least tsc=... and tsc_hz=...")
    return out


def default_bounds_file_for_trace(trace_dir):
    return os.path.join(trace_dir, "trace_bounds.txt")


def read_bounds_file(path):
    vals = read_key_value_file(path)
    begin_ns = parse_int(vals.get("trace_begin_ns", vals.get("begin_ns")))
    end_ns = parse_int(vals.get("trace_end_ns", vals.get("end_ns")))
    if begin_ns is None or end_ns is None:
        die("bounds file missing begin/end ns: {}".format(path), 20)
    if end_ns < begin_ns:
        die("bounds file has end < begin: {}".format(path), 21)
    return {
        "trace_begin_ns": begin_ns,
        "trace_end_ns": end_ns,
        "source": "bounds-file",
        "path": path,
    }


def resolve_fraction_bounds(trace_dir, args):
    if args.trace_begin_ns is not None or args.trace_end_ns is not None:
        if args.trace_begin_ns is None or args.trace_end_ns is None:
            die("use both --trace-begin-ns and --trace-end-ns together", 22)
        if args.trace_end_ns < args.trace_begin_ns:
            die("--trace-end-ns must be >= --trace-begin-ns", 23)
        return {
            "trace_begin_ns": int(args.trace_begin_ns),
            "trace_end_ns": int(args.trace_end_ns),
            "source": "explicit-ns",
            "path": None,
        }

    if args.bounds_file is not None:
        if not os.path.isfile(args.bounds_file):
            die("bounds file not found: {}".format(args.bounds_file), 24)
        return read_bounds_file(args.bounds_file)

    default_path = default_bounds_file_for_trace(trace_dir)
    if os.path.isfile(default_path):
        return read_bounds_file(default_path)

    die("fraction mode needs bounds via --trace-begin-ns/--trace-end-ns or a bounds file", 25)


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-200000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta ("
        " key TEXT PRIMARY KEY,"
        " value TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dpdk_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " trace_ns INTEGER,"
        " raw_cycles INTEGER NOT NULL,"
        " rel_cycles INTEGER,"
        " rel_us REAL,"
        " rel_anchor_cycles INTEGER,"
        " rel_anchor_us REAL,"
        " mono_raw_est_ns INTEGER,"
        " event_name TEXT NOT NULL,"
        " nb_rx INTEGER,"
        " port INTEGER,"
        " lcore INTEGER,"
        " is_rx INTEGER NOT NULL,"
        " prev_rx_class TEXT,"
        " this_rx_class TEXT,"
        " gap_from_prev_same_port_ns INTEGER,"
        " fields_json TEXT"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dpdk_cycles ON dpdk_events(raw_cycles)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dpdk_name ON dpdk_events(event_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dpdk_port ON dpdk_events(port)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dpdk_lcore ON dpdk_events(lcore)")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS dpdk_event_counts ("
        " event_name TEXT PRIMARY KEY,"
        " cnt INTEGER NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dpdk_rx_port_stats ("
        " port INTEGER PRIMARY KEY,"
        " rx_events INTEGER NOT NULL,"
        " zero_polls INTEGER NOT NULL,"
        " nonzero_polls INTEGER NOT NULL,"
        " total_packets INTEGER NOT NULL,"
        " min_nb INTEGER,"
        " max_nb INTEGER,"
        " avg_nb REAL,"
        " avg_gap_after_prev_zero_ns REAL,"
        " avg_gap_after_prev_nonzero_ns REAL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dpdk_rx_transition_stats ("
        " port INTEGER,"
        " prev_class TEXT,"
        " this_class TEXT,"
        " sample_count INTEGER NOT NULL,"
        " avg_gap_ns REAL,"
        " min_gap_ns INTEGER,"
        " max_gap_ns INTEGER,"
        " PRIMARY KEY(port, prev_class, this_class)"
        ")"
    )
    conn.commit()
    return conn


def put_meta(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
        (key, str(value)),
    )


class GapStats(object):
    __slots__ = ("count", "sum_ns", "min_ns", "max_ns")

    def __init__(self):
        self.count = 0
        self.sum_ns = 0
        self.min_ns = None
        self.max_ns = None

    def add(self, delta_ns):
        if delta_ns is None or delta_ns < 0:
            return
        self.count += 1
        self.sum_ns += delta_ns
        if self.min_ns is None or delta_ns < self.min_ns:
            self.min_ns = delta_ns
        if self.max_ns is None or delta_ns > self.max_ns:
            self.max_ns = delta_ns

    @property
    def avg_ns(self):
        if self.count == 0:
            return None
        return float(self.sum_ns) / float(self.count)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export a DPDK CTF trace into an organized SQLite/JSONL dataset."
    )
    parser.add_argument("-t", "--trace", required=True, help="Path to rte-* trace directory")
    parser.add_argument("--run-dir", required=True, help="Experiment root directory")
    parser.add_argument("--anchor", default=None, help="anchor.txt path for rel_anchor_us/mono_raw_est_ns")
    parser.add_argument("-st", "--start", type=float, default=None, help="Base scope start in seconds from first event")
    parser.add_argument("-et", "--end", type=float, default=None, help="Base scope end in seconds from first event")
    parser.add_argument("--start-frac", type=float, default=None, help="Base scope start as fraction of full trace")
    parser.add_argument("--end-frac", type=float, default=None, help="Base scope end as fraction of full trace")
    parser.add_argument("--trace-begin-ns", type=int, default=None, help="Absolute trace begin ns for fraction mode")
    parser.add_argument("--trace-end-ns", type=int, default=None, help="Absolute trace end ns for fraction mode")
    parser.add_argument("--bounds-file", default=None, help="Optional bounds file")
    parser.add_argument("--auto", action="store_true", help="Latch on first nb_rx > 0 in base scope")
    parser.add_argument("--auto-d", type=float, default=1.0, help="Duration after first nb_rx > 0 when --auto is used")
    parser.add_argument("--jsonl", action="store_true", help="Also save JSONL")
    parser.add_argument("--progress-every", type=int, default=1000000, help="Progress line every N messages")
    return parser.parse_args()


def validate_args(args):
    sec_mode = (args.start is not None or args.end is not None)
    frac_mode = (args.start_frac is not None or args.end_frac is not None)

    if sec_mode and frac_mode:
        die("use either -st/-et or --start-frac/--end-frac, not both", 2)
    if (args.start is None) != (args.end is None):
        die("use both -st and -et together, or omit both", 3)
    if (args.start_frac is None) != (args.end_frac is None):
        die("use both --start-frac and --end-frac together, or omit both", 4)
    if args.auto_d < 0:
        die("--auto-d must be >= 0", 7)
    if args.start is not None and args.end < args.start:
        die("-et must be >= -st", 8)
    if args.start_frac is not None:
        if not (0.0 <= args.start_frac <= 1.0 and 0.0 <= args.end_frac <= 1.0):
            die("--start-frac and --end-frac must be in [0,1]", 9)
        if args.end_frac < args.start_frac:
            die("--end-frac must be >= --start-frac", 10)


def maybe_progress(progress_every, processed, state):
    if progress_every > 0 and processed % progress_every == 0:
        log("processed={} state={}".format(processed, state))


def main():
    args = parse_args()
    validate_args(args)

    trace_dir = os.path.abspath(args.trace)
    run_dir = os.path.abspath(args.run_dir)
    dpdk_dir = os.path.join(run_dir, "dpdk")
    ensure_dir(dpdk_dir)
    db_path = os.path.join(dpdk_dir, "dpdk.sqlite")
    jsonl_path = os.path.join(dpdk_dir, "dpdk_events.jsonl")
    anchor_path = args.anchor or os.path.join(dpdk_dir, "anchor.txt")
    anchor = read_anchor(anchor_path) if os.path.isfile(anchor_path) else None

    if not os.path.isdir(trace_dir):
        die("trace dir not found: {}".format(trace_dir), 30)
    if not os.path.exists(os.path.join(trace_dir, "metadata")):
        die("metadata not found in trace dir: {}".format(trace_dir), 31)

    ensure_access(trace_dir)
    patch_metadata_size_t(trace_dir)

    frac_info = None
    if args.start_frac is not None:
        frac_info = resolve_fraction_bounds(trace_dir, args)

    conn = init_db(db_path)
    put_meta(conn, "trace_dir", trace_dir)
    if anchor:
        put_meta(conn, "anchor_path", anchor["path"])
        put_meta(conn, "anchor_tsc", anchor["tsc"])
        put_meta(conn, "anchor_tsc_hz", anchor["tsc_hz"])
        put_meta(conn, "anchor_mono_raw_ns", anchor.get("mono_raw_ns", ""))

    base_window_enabled = False
    base_start_ns = None
    base_end_ns = None
    base_mode = "whole-file"

    if frac_info is not None:
        span_ns = frac_info["trace_end_ns"] - frac_info["trace_begin_ns"]
        base_window_enabled = True
        base_mode = "fraction"
        base_start_ns = frac_info["trace_begin_ns"] + int(round(span_ns * args.start_frac))
        base_end_ns = frac_info["trace_begin_ns"] + int(round(span_ns * args.end_frac))
        put_meta(conn, "bounds_source", frac_info["source"])
        put_meta(conn, "bounds_path", frac_info["path"] or "")
    elif args.start is not None:
        base_window_enabled = True
        base_mode = "seconds"

    put_meta(conn, "base_mode", base_mode)
    put_meta(conn, "auto_enabled", int(args.auto))

    first_trace_ns = None
    first_cycles = None
    trace_begin_ns = frac_info["trace_begin_ns"] if frac_info is not None else None

    auto_latched = False
    auto_end_ns = None
    auto_d_ns = int(round(args.auto_d * 1e9))

    processed = 0
    selected = 0
    batch = []
    jsonl_fp = open(jsonl_path, "w") if args.jsonl else None

    event_counts = collections.Counter()
    port_rx_counts = collections.Counter()
    port_zero_counts = collections.Counter()
    port_nonzero_counts = collections.Counter()
    port_total_pkts = collections.Counter()
    port_min_nb = {}
    port_max_nb = {}
    gap_after_prev_zero = collections.defaultdict(GapStats)
    gap_after_prev_nonzero = collections.defaultdict(GapStats)
    transition_stats = collections.defaultdict(GapStats)
    prev_by_port = {}

    event_info_cache = {}
    it = open_trace(trace_dir)

    for msg in it:
        processed += 1
        if type(msg) is not EVENT_MSG_TYPE:
            maybe_progress(args.progress_every, processed, "skip-not-event")
            continue

        try:
            dcs = msg.default_clock_snapshot
            trace_ns = int(dcs.ns_from_origin)
            raw_cycles = int(dcs.value)
        except Exception:
            maybe_progress(args.progress_every, processed, "skip-no-clock")
            continue

        if trace_begin_ns is None:
            trace_begin_ns = trace_ns
            if base_mode == "seconds":
                base_start_ns = trace_begin_ns + int(round(args.start * 1e9))
                base_end_ns = trace_begin_ns + int(round(args.end * 1e9))

        if base_window_enabled and trace_ns < base_start_ns:
            maybe_progress(args.progress_every, processed, "pre-window")
            continue

        if args.auto and auto_latched:
            if trace_ns > auto_end_ns:
                break
        else:
            if base_window_enabled and trace_ns > base_end_ns:
                break

        ev = msg.event
        name = ev.name

        info = event_info_cache.get(name)
        if info is None:
            is_rx = is_rx_burst_family_name(name)
            nb_loc = detect_field_location(ev, NB_FIELD_CANDIDATES)
            port_loc = detect_field_location(ev, PORT_FIELD_CANDIDATES)
            lcore_loc = detect_field_location(ev, LCORE_FIELD_CANDIDATES)
            info = (is_rx, nb_loc, port_loc, lcore_loc)
            event_info_cache[name] = info

        is_rx, nb_loc, port_loc, lcore_loc = info
        nb_raw = get_field_value(ev, nb_loc[0], nb_loc[1]) if nb_loc[1] is not None else None
        port_raw = get_field_value(ev, port_loc[0], port_loc[1]) if port_loc[1] is not None else None
        lcore_raw = get_field_value(ev, lcore_loc[0], lcore_loc[1]) if lcore_loc[1] is not None else None

        nb_rx = normalize_scalar(nb_raw)
        port = normalize_scalar(port_raw)
        lcore = normalize_scalar(lcore_raw)

        if args.auto and not auto_latched:
            if isinstance(nb_rx, int) and nb_rx > 0:
                auto_latched = True
                auto_end_ns = trace_ns + auto_d_ns
                if base_window_enabled and auto_end_ns > base_end_ns:
                    auto_end_ns = base_end_ns
            else:
                maybe_progress(args.progress_every, processed, "search-auto")
                continue

        if first_trace_ns is None:
            first_trace_ns = trace_ns
        if first_cycles is None:
            first_cycles = raw_cycles

        rel_cycles = raw_cycles - first_cycles
        rel_us = None
        rel_anchor_cycles = None
        rel_anchor_us = None
        mono_raw_est_ns = None
        if anchor and anchor["tsc_hz"]:
            rel_anchor_cycles = raw_cycles - anchor["tsc"]
            rel_anchor_us = float(rel_anchor_cycles) * 1e6 / float(anchor["tsc_hz"])
            if anchor.get("mono_raw_ns") is not None:
                mono_raw_est_ns = int(round(anchor["mono_raw_ns"] + (float(rel_anchor_cycles) * 1e9 / float(anchor["tsc_hz"]))))
            rel_us = float(rel_cycles) * 1e6 / float(anchor["tsc_hz"])

        fields = collect_event_fields(ev)
        prev_class = None
        this_class = None
        gap_same_port = None

        if is_rx and isinstance(nb_rx, int):
            this_class = "nonzero" if nb_rx > 0 else "zero"

            if isinstance(port, int):
                port_rx_counts[port] += 1
                port_total_pkts[port] += max(nb_rx, 0)
                if nb_rx == 0:
                    port_zero_counts[port] += 1
                else:
                    port_nonzero_counts[port] += 1

                if port not in port_min_nb or nb_rx < port_min_nb[port]:
                    port_min_nb[port] = nb_rx
                if port not in port_max_nb or nb_rx > port_max_nb[port]:
                    port_max_nb[port] = nb_rx

                prev = prev_by_port.get(port)
                if prev is not None:
                    prev_ts, prev_nb = prev
                    gap_same_port = trace_ns - prev_ts
                    prev_class = "nonzero" if prev_nb > 0 else "zero"
                    if prev_class == "zero":
                        gap_after_prev_zero[port].add(gap_same_port)
                    else:
                        gap_after_prev_nonzero[port].add(gap_same_port)
                    transition_stats[(port, prev_class, this_class)].add(gap_same_port)

                prev_by_port[port] = (trace_ns, nb_rx)

        row = (
            trace_ns,
            raw_cycles,
            rel_cycles,
            rel_us,
            rel_anchor_cycles,
            rel_anchor_us,
            mono_raw_est_ns,
            name,
            nb_rx if isinstance(nb_rx, int) else None,
            port if isinstance(port, int) else None,
            lcore if isinstance(lcore, int) else None,
            1 if is_rx else 0,
            prev_class,
            this_class,
            gap_same_port,
            json.dumps(fields, sort_keys=True),
        )
        batch.append(row)
        selected += 1
        event_counts[name] += 1

        if jsonl_fp:
            out = {
                "trace_ns": trace_ns,
                "raw_cycles": raw_cycles,
                "rel_cycles": rel_cycles,
                "rel_us": rel_us,
                "rel_anchor_cycles": rel_anchor_cycles,
                "rel_anchor_us": rel_anchor_us,
                "mono_raw_est_ns": mono_raw_est_ns,
                "event_name": name,
                "nb_rx": nb_rx if isinstance(nb_rx, int) else None,
                "port": port if isinstance(port, int) else None,
                "lcore": lcore if isinstance(lcore, int) else None,
                "is_rx": bool(is_rx),
                "prev_rx_class": prev_class,
                "this_rx_class": this_class,
                "gap_from_prev_same_port_ns": gap_same_port,
                "fields": fields,
            }
            jsonl_fp.write(json.dumps(out, sort_keys=True) + "\n")

        if len(batch) >= 5000:
            conn.executemany(
                "INSERT INTO dpdk_events("
                " trace_ns, raw_cycles, rel_cycles, rel_us, rel_anchor_cycles, rel_anchor_us,"
                " mono_raw_est_ns, event_name, nb_rx, port, lcore, is_rx, prev_rx_class,"
                " this_rx_class, gap_from_prev_same_port_ns, fields_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            conn.commit()
            batch = []

        maybe_progress(args.progress_every, processed, "collect")

    if batch:
        conn.executemany(
            "INSERT INTO dpdk_events("
            " trace_ns, raw_cycles, rel_cycles, rel_us, rel_anchor_cycles, rel_anchor_us,"
            " mono_raw_est_ns, event_name, nb_rx, port, lcore, is_rx, prev_rx_class,"
            " this_rx_class, gap_from_prev_same_port_ns, fields_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )

    conn.execute("DELETE FROM dpdk_event_counts")
    conn.executemany(
        "INSERT INTO dpdk_event_counts(event_name, cnt) VALUES(?, ?)",
        [(name, cnt) for name, cnt in sorted(event_counts.items())],
    )

    conn.execute("DELETE FROM dpdk_rx_port_stats")
    rows = []
    for port in sorted(port_rx_counts):
        zero_avg = gap_after_prev_zero[port].avg_ns
        nonzero_avg = gap_after_prev_nonzero[port].avg_ns
        rx_events = port_rx_counts[port]
        total_packets = port_total_pkts[port]
        avg_nb = float(total_packets) / float(rx_events) if rx_events else None
        rows.append((
            port,
            rx_events,
            port_zero_counts[port],
            port_nonzero_counts[port],
            total_packets,
            port_min_nb.get(port),
            port_max_nb.get(port),
            avg_nb,
            zero_avg,
            nonzero_avg,
        ))
    conn.executemany(
        "INSERT INTO dpdk_rx_port_stats("
        " port, rx_events, zero_polls, nonzero_polls, total_packets, min_nb, max_nb, avg_nb,"
        " avg_gap_after_prev_zero_ns, avg_gap_after_prev_nonzero_ns"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    conn.execute("DELETE FROM dpdk_rx_transition_stats")
    rows = []
    for key in sorted(transition_stats):
        port, prev_class, this_class = key
        gs = transition_stats[key]
        rows.append((port, prev_class, this_class, gs.count, gs.avg_ns, gs.min_ns, gs.max_ns))
    conn.executemany(
        "INSERT INTO dpdk_rx_transition_stats("
        " port, prev_class, this_class, sample_count, avg_gap_ns, min_gap_ns, max_gap_ns"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    put_meta(conn, "trace_begin_ns", trace_begin_ns if trace_begin_ns is not None else "")
    put_meta(conn, "selected_event_count", selected)
    put_meta(conn, "processed_message_count", processed)
    put_meta(conn, "first_trace_ns", first_trace_ns if first_trace_ns is not None else "")
    put_meta(conn, "first_cycles", first_cycles if first_cycles is not None else "")
    put_meta(conn, "base_start_ns", base_start_ns if base_start_ns is not None else "")
    put_meta(conn, "base_end_ns", base_end_ns if base_end_ns is not None else "")
    conn.commit()
    conn.close()

    if jsonl_fp:
        jsonl_fp.close()

    ok("dpdk export complete: {}".format(db_path))


if __name__ == "__main__":
    main()
