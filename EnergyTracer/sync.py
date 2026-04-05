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


def die(msg, code=1):
    print("[ERROR] {}".format(msg), file=sys.stderr)
    sys.exit(code)


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


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
        if str(value).lower().startswith("0x"):
            return int(value, 16)
        return int(value)
    except Exception:
        return default


def read_anchor(path):
    vals = read_key_value_file(path)
    out = {
        "tsc": parse_int(vals.get("tsc")),
        "tsc_hz": parse_int(vals.get("tsc_hz")),
        "mono_raw_ns": parse_int(vals.get("mono_raw_ns")),
        "path": os.path.abspath(path),
    }
    if out["tsc"] is None or out["tsc_hz"] is None:
        die("anchor file must contain at least tsc=... and tsc_hz=...")
    return out


def init_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta ("
        " key TEXT PRIMARY KEY,"
        " value TEXT NOT NULL"
        ")"
    )

    conn.execute(
        "CREATE TABLE IF NOT EXISTS unified_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " source TEXT NOT NULL,"
        " event_name TEXT NOT NULL,"
        " raw_cycles INTEGER,"
        " rel_source_us REAL,"
        " rel_anchor_us REAL,"
        " rel_start_us REAL,"
        " mono_raw_est_ns INTEGER,"
        " trace_ns INTEGER,"
        " cpu INTEGER,"
        " lcore INTEGER,"
        " port INTEGER,"
        " field_cpu_id INTEGER,"
        " nb_rx INTEGER,"
        " task TEXT,"
        " pid INTEGER,"
        " field_state INTEGER,"
        " prev_rx_class TEXT,"
        " this_rx_class TEXT,"
        " gap_from_prev_same_port_ns INTEGER,"
        " raw_body TEXT,"
        " fields_json TEXT"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_cycles ON unified_events(raw_cycles)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_source ON unified_events(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_name ON unified_events(event_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_port ON unified_events(port)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_cpu ON unified_events(cpu)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_lcore ON unified_events(lcore)")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS unified_energy_samples ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " source TEXT NOT NULL,"
        " raw_cycles INTEGER,"
        " rel_source_us REAL,"
        " rel_anchor_us REAL,"
        " rel_start_us REAL,"
        " mono_raw_ns INTEGER,"
        " mono_ns INTEGER,"
        " pkg_j_sock0 REAL,"
        " pkg_j_sock1 REAL,"
        " dram_j_sock0 REAL,"
        " dram_j_sock1 REAL,"
        " acpi_uW INTEGER"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_energy_cycles ON unified_energy_samples(raw_cycles)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_energy_raw_ns ON unified_energy_samples(mono_raw_ns)")
    return conn


def put_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))


def copy_table(conn, src_alias, src_table, dst_table):
    exists = conn.execute(
        "SELECT name FROM {}.sqlite_master WHERE type='table' AND name=?".format(src_alias),
        (src_table,),
    ).fetchone()
    if not exists:
        return False
    conn.execute('DROP TABLE IF EXISTS "{}"'.format(dst_table))
    conn.execute('CREATE TABLE "{}" AS SELECT * FROM {}."{}"'.format(dst_table, src_alias, src_table))
    return True


def scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def main():
    parser = argparse.ArgumentParser(description="Create unified timeline SQLite DB from linux + dpdk DBs.")
    parser.add_argument("--run-dir", required=True, help="Experiment root directory")
    parser.add_argument("--linux-db", default=None, help="Override linux.sqlite path")
    parser.add_argument("--dpdk-db", default=None, help="Override dpdk.sqlite path")
    parser.add_argument("--anchor", default=None, help="Override anchor.txt path")
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    linux_db = os.path.abspath(args.linux_db or os.path.join(run_dir, "linux", "linux.sqlite"))
    dpdk_db = os.path.abspath(args.dpdk_db or os.path.join(run_dir, "dpdk", "dpdk.sqlite"))
    anchor_path = os.path.abspath(args.anchor or os.path.join(run_dir, "dpdk", "anchor.txt"))
    unified_dir = os.path.join(run_dir, "unified")
    ensure_dir(unified_dir)
    unified_db = os.path.join(unified_dir, "unified.sqlite")

    if not os.path.isfile(linux_db):
        die("linux db not found: {}".format(linux_db))
    if not os.path.isfile(dpdk_db):
        die("dpdk db not found: {}".format(dpdk_db))
    if not os.path.isfile(anchor_path):
        die("anchor file not found: {}".format(anchor_path))

    anchor = read_anchor(anchor_path)

    conn = init_db(unified_db)
    conn.execute("ATTACH DATABASE ? AS linuxdb", (linux_db,))
    conn.execute("ATTACH DATABASE ? AS dpdkdb", (dpdk_db,))

    put_meta(conn, "run_dir", run_dir)
    put_meta(conn, "linux_db", linux_db)
    put_meta(conn, "dpdk_db", dpdk_db)
    put_meta(conn, "anchor_path", anchor["path"])
    put_meta(conn, "anchor_tsc", anchor["tsc"])
    put_meta(conn, "anchor_tsc_hz", anchor["tsc_hz"])
    put_meta(conn, "anchor_mono_raw_ns", anchor.get("mono_raw_ns", ""))

    linux_trace_clock = conn.execute("SELECT value FROM linuxdb.meta WHERE key='trace_clock'").fetchone()
    if linux_trace_clock:
        put_meta(conn, "linux_trace_clock", linux_trace_clock[0])

    linux_primary_clock = conn.execute("SELECT value FROM linuxdb.meta WHERE key='primary_clock'").fetchone()
    if linux_primary_clock:
        put_meta(conn, "linux_primary_clock", linux_primary_clock[0])

    conn.execute("DELETE FROM unified_events")
    conn.execute("DELETE FROM unified_energy_samples")

    log("copying kernel events")
    conn.execute(
        "INSERT INTO unified_events("
        " source, event_name, raw_cycles, rel_source_us, rel_anchor_us, rel_start_us, mono_raw_est_ns,"
        " trace_ns, cpu, lcore, port, field_cpu_id, nb_rx, task, pid, field_state, prev_rx_class, this_rx_class,"
        " gap_from_prev_same_port_ns, raw_body, fields_json"
        ") "
        "SELECT "
        " 'linux',"
        " event_name,"
        " ts_cycles,"
        " rel_primary_us,"
        " CASE "
        "   WHEN ts_cycles IS NOT NULL THEN ((ts_cycles - ?) * 1000000.0 / ?) "
        "   WHEN primary_clock = 'mono_raw' AND ? IS NOT NULL AND ts_time_ns IS NOT NULL THEN ((ts_time_ns - ?) / 1000.0) "
        "   ELSE NULL "
        " END,"
        " NULL,"
        " CASE "
        "   WHEN primary_clock = 'mono_raw' THEN ts_time_ns "
        "   WHEN ts_cycles IS NOT NULL AND ? IS NOT NULL THEN CAST(ROUND(? + ((ts_cycles - ?) * 1000000000.0 / ?)) AS INTEGER) "
        "   ELSE NULL "
        " END,"
        " NULL,"
        " cpu,"
        " NULL,"
        " NULL,"
        " field_cpu_id,"
        " NULL,"
        " task,"
        " pid,"
        " field_state,"
        " NULL,"
        " NULL,"
        " NULL,"
        " raw_body,"
        " fields_json "
        "FROM linuxdb.kernel_events",
        (
            anchor["tsc"],
            anchor["tsc_hz"],
            anchor.get("mono_raw_ns"),
            anchor.get("mono_raw_ns"),
            anchor.get("mono_raw_ns"),
            anchor.get("mono_raw_ns"),
            anchor["tsc"],
            anchor["tsc_hz"],
        ),
    )

    log("copying dpdk events")
    conn.execute(
        "INSERT INTO unified_events("
        " source, event_name, raw_cycles, rel_source_us, rel_anchor_us, rel_start_us, mono_raw_est_ns,"
        " trace_ns, cpu, lcore, port, field_cpu_id, nb_rx, task, pid, field_state, prev_rx_class, this_rx_class,"
        " gap_from_prev_same_port_ns, raw_body, fields_json"
        ") "
        "SELECT "
        " 'dpdk',"
        " event_name,"
        " raw_cycles,"
        " rel_us,"
        " COALESCE(rel_anchor_us, ((raw_cycles - ?) * 1000000.0 / ?)),"
        " NULL,"
        " COALESCE(mono_raw_est_ns, CASE WHEN ? IS NOT NULL THEN CAST(ROUND(? + ((raw_cycles - ?) * 1000000000.0 / ?)) AS INTEGER) ELSE NULL END),"
        " trace_ns,"
        " NULL,"
        " lcore,"
        " port,"
        " NULL,"
        " nb_rx,"
        " NULL,"
        " NULL,"
        " NULL,"
        " prev_rx_class,"
        " this_rx_class,"
        " gap_from_prev_same_port_ns,"
        " NULL,"
        " fields_json "
        "FROM dpdkdb.dpdk_events",
        (
            anchor["tsc"],
            anchor["tsc_hz"],
            anchor.get("mono_raw_ns"),
            anchor.get("mono_raw_ns"),
            anchor["tsc"],
            anchor["tsc_hz"],
        ),
    )

    log("copying energy samples")
    conn.execute(
        "INSERT INTO unified_energy_samples("
        " source, raw_cycles, rel_source_us, rel_anchor_us, rel_start_us, mono_raw_ns, mono_ns,"
        " pkg_j_sock0, pkg_j_sock1, dram_j_sock0, dram_j_sock1, acpi_uW"
        ") "
        "SELECT "
        " 'linux',"
        " COALESCE(ts_tsc, CASE WHEN primary_clock = 'x86-tsc' THEN ts_primary ELSE NULL END),"
        " rel_primary_us,"
        " CASE "
        "   WHEN COALESCE(ts_tsc, CASE WHEN primary_clock = 'x86-tsc' THEN ts_primary ELSE NULL END) IS NOT NULL "
        "     THEN ((COALESCE(ts_tsc, CASE WHEN primary_clock = 'x86-tsc' THEN ts_primary ELSE NULL END) - ?) * 1000000.0 / ?) "
        "   WHEN ? IS NOT NULL AND ts_raw_ns IS NOT NULL "
        "     THEN ((ts_raw_ns - ?) / 1000.0) "
        "   ELSE NULL "
        " END,"
        " NULL,"
        " ts_raw_ns,"
        " ts_mono_ns,"
        " pkg_j_sock0, pkg_j_sock1, dram_j_sock0, dram_j_sock1, acpi_uW "
        "FROM linuxdb.energy_samples",
        (
            anchor["tsc"],
            anchor["tsc_hz"],
            anchor.get("mono_raw_ns"),
            anchor.get("mono_raw_ns"),
        ),
    )

    first_cycle_events = scalar(conn, "SELECT MIN(raw_cycles) FROM unified_events WHERE raw_cycles IS NOT NULL")
    first_cycle_energy = scalar(conn, "SELECT MIN(raw_cycles) FROM unified_energy_samples WHERE raw_cycles IS NOT NULL")
    first_cycle = None
    if first_cycle_events is not None and first_cycle_energy is not None:
        first_cycle = min(first_cycle_events, first_cycle_energy)
    elif first_cycle_events is not None:
        first_cycle = first_cycle_events
    elif first_cycle_energy is not None:
        first_cycle = first_cycle_energy

    if first_cycle is not None:
        conn.execute(
            "UPDATE unified_events "
            "SET rel_start_us = ((raw_cycles - ?) * 1000000.0 / ?) "
            "WHERE raw_cycles IS NOT NULL",
            (first_cycle, anchor["tsc_hz"]),
        )
        conn.execute(
            "UPDATE unified_energy_samples "
            "SET rel_start_us = ((raw_cycles - ?) * 1000000.0 / ?) "
            "WHERE raw_cycles IS NOT NULL",
            (first_cycle, anchor["tsc_hz"]),
        )
        put_meta(conn, "unified_first_cycle", first_cycle)

    for src_alias, src_table, dst_table in [
        ("linuxdb", "kernel_event_counts", "src_kernel_event_counts"),
        ("linuxdb", "kernel_idle_state_counts", "src_kernel_idle_state_counts"),
        ("linuxdb", "kernel_frequency_state_counts", "src_kernel_frequency_state_counts"),
        ("linuxdb", "kernel_pstate_event_counts", "src_kernel_pstate_event_counts"),
        ("linuxdb", "energy_samples", "src_energy_samples"),
        ("dpdkdb", "dpdk_event_counts", "src_dpdk_event_counts"),
        ("dpdkdb", "dpdk_rx_port_stats", "src_dpdk_rx_port_stats"),
        ("dpdkdb", "dpdk_rx_transition_stats", "src_dpdk_rx_transition_stats"),
    ]:
        copy_table(conn, src_alias, src_table, dst_table)

    conn.execute("DROP VIEW IF EXISTS rx_events")
    conn.execute(
        "CREATE VIEW rx_events AS "
        "SELECT * FROM unified_events WHERE source='dpdk' AND nb_rx IS NOT NULL"
    )

    conn.execute("DROP VIEW IF EXISTS kernel_power_events")
    conn.execute(
        "CREATE VIEW kernel_power_events AS "
        "SELECT * FROM unified_events "
        "WHERE source='linux' "
        "  AND (event_name IN ('cpu_idle', 'cpu_frequency') OR event_name LIKE '%pstate%')"
    )

    conn.execute("DROP VIEW IF EXISTS energy_timeline")
    conn.execute(
        "CREATE VIEW energy_timeline AS "
        "SELECT * FROM unified_energy_samples ORDER BY COALESCE(raw_cycles, mono_raw_ns)"
    )

    put_meta(conn, "unified_event_count", scalar(conn, "SELECT COUNT(*) FROM unified_events") or 0)
    put_meta(conn, "unified_energy_count", scalar(conn, "SELECT COUNT(*) FROM unified_energy_samples") or 0)

    conn.commit()
    conn.close()
    ok("unified db created: {}".format(unified_db))


if __name__ == "__main__":
    main()
