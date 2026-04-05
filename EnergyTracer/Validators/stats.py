#!/usr/bin/env python3
# -*- coding: utf-8 -*-


from __future__ import print_function

import argparse
import os
import sqlite3
import sys


def log(msg):
    print("[INFO] {}".format(msg), flush=True)


def ok(msg):
    print("[OK] {}".format(msg), flush=True)


def warn(msg):
    print("[WARN] {}".format(msg), flush=True)


def die(msg, code=1):
    print("[ERROR] {}".format(msg), file=sys.stderr)
    sys.exit(code)


def ns_to_s(ns):
    if ns is None:
        return None
    return float(ns) / 1e9


def ns_to_us(ns):
    if ns is None:
        return None
    return float(ns) / 1000.0


def pct(part, whole):
    if not whole:
        return 0.0
    return 100.0 * float(part) / float(whole)


def fmt_us_from_ns(ns):
    if ns is None:
        return "N/A"
    return "{:.1f} ns ({:.3f} us)".format(float(ns), ns_to_us(ns))


def fmt_bytes(n):
    if n is None or n < 0:
        return "N/A"
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(n)
    i = 0
    while x >= 1024.0 and i < len(units) - 1:
        x /= 1024.0
        i += 1
    return "{:.1f} {}".format(x, units[i])


def connect_db(path):
    if not path:
        return None
    if not os.path.exists(path):
        die("database not found: {}".format(path))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone()
    return row is not None


def table_count(conn, name):
    try:
        row = conn.execute('SELECT COUNT(*) AS c FROM "{}"'.format(name)).fetchone()
        return int(row["c"]) if row else 0
    except Exception:
        return 0


def get_columns(conn, table):
    try:
        rows = conn.execute('PRAGMA table_info("{}")'.format(table)).fetchall()
        return [r["name"] for r in rows]
    except Exception:
        return []


def print_header(title):
    print("=" * 88)
    print(title)
    print("=" * 88)


def print_sub(title):
    print("-" * 88)
    print(title)


def print_kv(label, value):
    print("  {:24s}: {}".format(label, value))


def print_db_input(label, path):
    print_sub("{} INPUT".format(label))
    print_kv("path", path)
    try:
        print_kv("size", fmt_bytes(os.path.getsize(path)))
    except Exception:
        print_kv("size", "N/A")


def print_meta_summary(conn, label):
    if not has_table(conn, "meta"):
        return
    print_sub("{} META".format(label))
    keys = [
        "primary_clock", "primary_unit", "trace_clock", "tsc_hz",
        "kernel_record_count", "energy_record_count",
        "trace_cpulist", "run_dir"
    ]
    any_printed = False
    for k in keys:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
        if row is not None:
            print_kv(k, row["value"])
            any_printed = True
    if not any_printed:
        print("  none")


def read_text_file(path):
    try:
        with open(path, "r") as fp:
            return fp.read().strip()
    except Exception:
        return None


def get_cpuidle_driver():
    return read_text_file("/sys/devices/system/cpu/cpuidle/current_driver")


def get_cstate_info(cpu_id, state):
    try:
        sval = int(state)
    except Exception:
        return "unknown"

    if sval in (-1, 4294967295):
        return "idle exit (-1 / 4294967295)"

    base = "/sys/devices/system/cpu/cpu{}/cpuidle/state{}".format(cpu_id, sval)
    name = read_text_file(os.path.join(base, "name"))
    desc = read_text_file(os.path.join(base, "desc"))
    latency = read_text_file(os.path.join(base, "latency"))

    parts = []
    if name:
        parts.append(name)
    if desc:
        parts.append(desc)
    if latency is not None:
        parts.append("latency={} us".format(latency))
    if parts:
        return " | ".join(parts)
    return "state{} meaning not found in sysfs".format(sval)


def fmt_freq_state_khz(state):
    if state is None:
        return "N/A"
    try:
        khz = int(state)
    except Exception:
        return str(state)

    mhz = float(khz) / 1000.0
    ghz = float(khz) / 1000000.0
    if ghz >= 1.0:
        return "{} kHz ({:.3f} GHz / {:.1f} MHz)".format(khz, ghz, mhz)
    return "{} kHz ({:.1f} MHz)".format(khz, mhz)


def print_dpdk_general(conn):
    if conn is None:
        return

    print_header("DPDK CHECK")

    if has_table(conn, "dpdk_events"):
        cols = set(get_columns(conn, "dpdk_events"))

        total_events = table_count(conn, "dpdk_events")
        print_sub("SCOPE / GENERAL")
        print_kv("dpdk_events rows", total_events)

        if "trace_ns" in cols:
            row = conn.execute(
                "SELECT MIN(trace_ns), MAX(trace_ns) FROM dpdk_events"
            ).fetchone()
            t0, t1 = row[0], row[1]
            print_kv("first trace_ns", t0 if t0 is not None else "N/A")
            print_kv("last trace_ns", t1 if t1 is not None else "N/A")
            print_kv(
                "actual scope secs",
                "{:.6f}".format(ns_to_s(t1 - t0)) if (t0 is not None and t1 is not None) else "N/A"
            )
        elif "ts_primary" in cols:
            row = conn.execute(
                "SELECT MIN(ts_primary), MAX(ts_primary) FROM dpdk_events"
            ).fetchone()
            t0, t1 = row[0], row[1]
            print_kv("first ts_primary", t0 if t0 is not None else "N/A")
            print_kv("last ts_primary", t1 if t1 is not None else "N/A")

        if "rel_us" in cols:
            row = conn.execute(
                "SELECT MIN(rel_us), MAX(rel_us) FROM dpdk_events"
            ).fetchone()
            r0, r1 = row[0], row[1]
            print_kv("first rel_us", "{:.3f}".format(r0) if r0 is not None else "N/A")
            print_kv("last rel_us", "{:.3f}".format(r1) if r1 is not None else "N/A")

        print_sub("EVENT COUNTS IN COLLECTED SCOPE")
        if has_table(conn, "dpdk_event_counts"):
            cols_ec = set(get_columns(conn, "dpdk_event_counts"))
            count_col = "event_count" if "event_count" in cols_ec else ("cnt" if "cnt" in cols_ec else None)
            if count_col:
                rows = conn.execute(
                    'SELECT event_name, "{}" AS c FROM dpdk_event_counts ORDER BY "{}" DESC, event_name'.format(count_col, count_col)
                ).fetchall()
            else:
                rows = []
            if not rows:
                print("  none")
            else:
                total = sum(int(r["c"]) for r in rows)
                for r in rows:
                    print("  {:10d}  {:6.2f}%  {}".format(
                        int(r["c"]),
                        pct(int(r["c"]), total),
                        r["event_name"]
                    ))
        elif "event_name" in cols:
            rows = conn.execute(
                "SELECT event_name, COUNT(*) AS c FROM dpdk_events "
                "GROUP BY event_name ORDER BY c DESC, event_name"
            ).fetchall()
            if not rows:
                print("  none")
            else:
                total = sum(int(r["c"]) for r in rows)
                for r in rows:
                    print("  {:10d}  {:6.2f}%  {}".format(
                        int(r["c"]),
                        pct(int(r["c"]), total),
                        r["event_name"]
                    ))

        if "nb_rx" in cols:
            print_sub("RX BURST QUICK STATS IN COLLECTED SCOPE")
            row = conn.execute(
                "SELECT "
                " COUNT(*) AS rx_with_count, "
                " SUM(CASE WHEN nb_rx = 0 THEN 1 ELSE 0 END) AS nb0, "
                " SUM(CASE WHEN nb_rx > 0 THEN 1 ELSE 0 END) AS nbgt0, "
                " SUM(CASE WHEN nb_rx IS NOT NULL THEN nb_rx ELSE 0 END) AS pkt_sum, "
                " MIN(nb_rx) AS nb_min, "
                " MAX(nb_rx) AS nb_max "
                "FROM dpdk_events "
                "WHERE nb_rx IS NOT NULL"
            ).fetchone()
            rx_with_count = int(row["rx_with_count"] or 0)
            nb0 = int(row["nb0"] or 0)
            nbgt0 = int(row["nbgt0"] or 0)
            pkt_sum = int(row["pkt_sum"] or 0)
            nb_min = row["nb_min"]
            nb_max = row["nb_max"]

            print_kv("matched rx events", rx_with_count)
            print_kv("rx events w/ count fld", rx_with_count)
            print_kv("rx events nb_rx = 0", nb0)
            print_kv("rx events nb_rx > 0", nbgt0)

            if rx_with_count > 0:
                print_kv("total packets", pkt_sum)
                print_kv("avg pkts/rx event", "{:.3f}".format(float(pkt_sum) / float(rx_with_count)))
                print_kv("avg pkts/nonzero rx", "{:.3f}".format(float(pkt_sum) / float(nbgt0)) if nbgt0 > 0 else "N/A")
                print_kv("min pkts/rx event", nb_min)
                print_kv("max pkts/rx event", nb_max)
                print_kv("empty poll ratio", "{:.3f}".format(float(nb0) / float(rx_with_count)))
            else:
                print_kv("total packets", "N/A")
                print_kv("avg pkts/rx event", "N/A")
                print_kv("avg pkts/nonzero rx", "N/A")
                print_kv("min pkts/rx event", "N/A")
                print_kv("max pkts/rx event", "N/A")
                print_kv("empty poll ratio", "N/A")

    if has_table(conn, "dpdk_rx_port_stats"):
        print_sub("RX COST ESTIMATES BY PORT")
        rows = conn.execute(
            "SELECT * FROM dpdk_rx_port_stats ORDER BY port"
        ).fetchall()
        if not rows:
            print("  none")
        else:
            for r in rows:
                keys = set(r.keys())
                avg0 = r["avg_gap_after_prev_zero_ns"] if "avg_gap_after_prev_zero_ns" in keys else None
                avg1 = r["avg_gap_after_prev_nonzero_ns"] if "avg_gap_after_prev_nonzero_ns" in keys else None
                extra = None
                if avg0 is not None and avg1 is not None:
                    extra = float(avg1) - float(avg0)

                print("  port {}".format(r["port"]))
                print("    rx events           : {}".format(r["rx_events"]) if "rx_events" in keys else "    rx events           : N/A")
                print("    zero polls          : {}".format(r["zero_polls"]) if "zero_polls" in keys else "    zero polls          : N/A")
                print("    nonzero polls       : {}".format(r["nonzero_polls"]) if "nonzero_polls" in keys else "    nonzero polls       : N/A")
                print("    total packets       : {}".format(r["total_packets"]) if "total_packets" in keys else "    total packets       : N/A")
                print("    avg nb              : {:.3f}".format(r["avg_nb"]) if ("avg_nb" in keys and r["avg_nb"] is not None) else "    avg nb              : N/A")
                print("    rx-zero poll cost   : {}".format(fmt_us_from_ns(avg0)))
                print("    rx-nonzero path     : {}".format(fmt_us_from_ns(avg1)))
                print("    burst extra cost    : {}".format(fmt_us_from_ns(extra)))

    if has_table(conn, "dpdk_rx_transition_stats"):
        print_sub("RX TRANSITION STATS")
        rows = conn.execute(
            "SELECT * FROM dpdk_rx_transition_stats "
            "ORDER BY port, prev_class, this_class"
        ).fetchall()
        if not rows:
            print("  none")
        else:
            for r in rows:
                print("  port={}  {} -> {}  samples={}  avg={}  min={}  max={}".format(
                    r["port"],
                    r["prev_class"],
                    r["this_class"],
                    r["sample_count"],
                    fmt_us_from_ns(r["avg_gap_ns"]),
                    fmt_us_from_ns(r["min_gap_ns"]),
                    fmt_us_from_ns(r["max_gap_ns"]),
                ))


def print_linux_general(conn):
    if conn is None:
        return

    print_header("LINUX CHECK")

    if has_table(conn, "kernel_events"):
        print_sub("KERNEL GENERAL")
        print_kv("kernel_events rows", table_count(conn, "kernel_events"))

        cols = set(get_columns(conn, "kernel_events"))
        if "cpu" in cols:
            rows = conn.execute(
                "SELECT cpu, COUNT(*) AS c FROM kernel_events GROUP BY cpu ORDER BY cpu"
            ).fetchall()
            if rows:
                cpu_text = ", ".join("cpu{}={}".format(r["cpu"], r["c"]) for r in rows)
                print_kv("events by cpu", cpu_text)

    if has_table(conn, "kernel_event_counts"):
        print_sub("KERNEL EVENT COUNTS")
        rows = conn.execute(
            "SELECT event_name, cnt FROM kernel_event_counts ORDER BY cnt DESC, event_name"
        ).fetchall()
        if not rows:
            print("  none")
        else:
            total = sum(int(r["cnt"]) for r in rows)
            for r in rows:
                print("  {:10d}  {:6.2f}%  {}".format(
                    int(r["cnt"]),
                    pct(int(r["cnt"]), total),
                    r["event_name"]
                ))

    if has_table(conn, "kernel_idle_state_counts"):
        print_sub("KERNEL IDLE STATE COUNTS")
        rows = conn.execute(
            "SELECT cpu_id, state, cnt FROM kernel_idle_state_counts ORDER BY cpu_id, state"
        ).fetchall()
        if not rows:
            print("  none")
        else:
            total = sum(int(r["cnt"]) for r in rows)
            driver = get_cpuidle_driver()
            print_kv("cpuidle driver", driver if driver else "N/A")
            print_kv("idle state groups", len(rows))
            print_kv("idle state total cnt", total)
            for r in rows:
                meaning = get_cstate_info(r["cpu_id"], r["state"])
                print("  cpu_id={}  state={}  cnt={}  meaning={}".format(
                    r["cpu_id"], r["state"], r["cnt"], meaning
                ))

    if has_table(conn, "kernel_frequency_state_counts"):
        print_sub("KERNEL FREQUENCY STATE COUNTS")
        rows = conn.execute(
            "SELECT cpu_id, state, cnt FROM kernel_frequency_state_counts ORDER BY cpu_id, state"
        ).fetchall()
        if not rows:
            print("  none")
        else:
            total = sum(int(r["cnt"]) for r in rows)
            print_kv("freq state groups", len(rows))
            print_kv("freq state total cnt", total)
            for r in rows:
                print("  cpu_id={}  state={}  cnt={}  meaning={}".format(
                    r["cpu_id"], r["state"], r["cnt"], fmt_freq_state_khz(r["state"])
                ))

    if has_table(conn, "kernel_pstate_event_counts"):
        print_sub("KERNEL PSTATE EVENT COUNTS")
        rows = conn.execute(
            "SELECT event_name, cnt FROM kernel_pstate_event_counts ORDER BY cnt DESC, event_name"
        ).fetchall()
        if not rows:
            print("  none")
        else:
            for r in rows:
                print("  {:10d}  {}".format(int(r["cnt"]), r["event_name"]))

    if has_table(conn, "energy_samples"):
        print_sub("ENERGY SAMPLES")
        rows = table_count(conn, "energy_samples")
        print_kv("energy_samples rows", rows)
        cols = set(get_columns(conn, "energy_samples"))
        if "primary_clock" in cols:
            clocks = conn.execute(
                "SELECT primary_clock, COUNT(*) AS c FROM energy_samples "
                "GROUP BY primary_clock ORDER BY c DESC"
            ).fetchall()
            if clocks:
                print_kv(
                    "primary_clock",
                    ", ".join("{}={}".format(r["primary_clock"], r["c"]) for r in clocks)
                )
        if "rel_primary_us" in cols:
            row = conn.execute(
                "SELECT MIN(rel_primary_us), MAX(rel_primary_us) FROM energy_samples"
            ).fetchone()
            lo, hi = row[0], row[1]
            print_kv("rel_primary_us min", "{:.3f}".format(lo) if lo is not None else "N/A")
            print_kv("rel_primary_us max", "{:.3f}".format(hi) if hi is not None else "N/A")


def main():
    ap = argparse.ArgumentParser(
        description="DB-only RX/Linux checker. Reads dpdk.sqlite and linux.sqlite and prints report-style summaries."
    )
    ap.add_argument("-d", "--dpdk-db", default=None, help="Path to dpdk.sqlite")
    ap.add_argument("-l", "--linux-db", default=None, help="Path to linux.sqlite")
    args = ap.parse_args()

    if not args.dpdk_db and not args.linux_db:
        die("provide at least one of -d/--dpdk-db or -l/--linux-db")

    dpdk = connect_db(args.dpdk_db) if args.dpdk_db else None
    linux = connect_db(args.linux_db) if args.linux_db else None

    print_header("DATABASE INPUTS")
    if args.dpdk_db:
        print_db_input("DPDK", args.dpdk_db)
    if args.linux_db:
        print_db_input("LINUX", args.linux_db)

    if dpdk:
        print_meta_summary(dpdk, "DPDK")
        print_dpdk_general(dpdk)
        dpdk.close()

    if linux:
        print_meta_summary(linux, "LINUX")
        print_linux_general(linux)
        linux.close()

    print("=" * 88)
    ok("done")


if __name__ == "__main__":
    main()
