
#!/usr/bin/env python3
# -*- coding: utf-8 -*-


from __future__ import print_function

import argparse
import bisect
import math
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


def fmt_us(v):
    if v is None:
        return "N/A"
    return "{:.3f} us".format(float(v))


def fmt_ns(v):
    if v is None:
        return "N/A"
    return "{} ns".format(int(round(v)))


def fmt_pct(part, whole):
    if whole is None or whole == 0 or part is None:
        return "N/A"
    return "{:.2f}%".format(100.0 * float(part) / float(whole))


def fmt_num(v):
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return "{:.6f}".format(v)
    return str(v)


def print_header(title):
    print("=" * 96)
    print(title)
    print("=" * 96)


def print_sub(title):
    print("-" * 96)
    print(title)


def print_kv(label, value):
    print("  {:30s}: {}".format(label, value))


def connect_db(path):
    if not os.path.isfile(path):
        die("input db not found: {}".format(path))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def get_meta(conn, key, default=None):
    if not has_table(conn, "meta"):
        return default
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def to_int(value, default=None):
    if value is None or value == "":
        return default
    try:
        if str(value).lower().startswith("0x"):
            return int(value, 16)
        return int(value)
    except Exception:
        return default


def to_float(value, default=None):
    if value is None or value == "":
        return default
    try:
        return float(value)
    except Exception:
        return default


def table_count(conn, name):
    return conn.execute('SELECT COUNT(*) AS c FROM "{}"'.format(name)).fetchone()["c"]


def get_range_events(conn, source):
    row = conn.execute(
        "SELECT "
        " COUNT(*) AS rows, "
        " MIN(rel_anchor_us) AS start_us, "
        " MAX(rel_anchor_us) AS end_us "
        "FROM unified_events "
        "WHERE source=? AND rel_anchor_us IS NOT NULL",
        (source,),
    ).fetchone()
    rows = int(row["rows"] or 0)
    start_us = row["start_us"]
    end_us = row["end_us"]
    dur_us = None
    if start_us is not None and end_us is not None:
        dur_us = float(end_us) - float(start_us)
    return {
        "rows": rows,
        "start_us": start_us,
        "end_us": end_us,
        "dur_us": dur_us,
    }


def get_range_energy(conn):
    row = conn.execute(
        "SELECT "
        " COUNT(*) AS rows, "
        " MIN(rel_anchor_us) AS start_us, "
        " MAX(rel_anchor_us) AS end_us "
        "FROM unified_energy_samples "
        "WHERE rel_anchor_us IS NOT NULL"
    ).fetchone()
    rows = int(row["rows"] or 0)
    start_us = row["start_us"]
    end_us = row["end_us"]
    dur_us = None
    if start_us is not None and end_us is not None:
        dur_us = float(end_us) - float(start_us)
    return {
        "rows": rows,
        "start_us": start_us,
        "end_us": end_us,
        "dur_us": dur_us,
    }


def overlap(a, b):
    if a["start_us"] is None or b["start_us"] is None or a["end_us"] is None or b["end_us"] is None:
        return {"start_us": None, "end_us": None, "dur_us": 0.0}
    s = max(float(a["start_us"]), float(b["start_us"]))
    e = min(float(a["end_us"]), float(b["end_us"]))
    if e <= s:
        return {"start_us": s, "end_us": e, "dur_us": 0.0}
    return {"start_us": s, "end_us": e, "dur_us": e - s}


def triple_overlap(a, b, c):
    if (
        a["start_us"] is None or b["start_us"] is None or c["start_us"] is None or
        a["end_us"] is None or b["end_us"] is None or c["end_us"] is None
    ):
        return {"start_us": None, "end_us": None, "dur_us": 0.0}
    s = max(float(a["start_us"]), float(b["start_us"]), float(c["start_us"]))
    e = min(float(a["end_us"]), float(b["end_us"]), float(c["end_us"]))
    if e <= s:
        return {"start_us": s, "end_us": e, "dur_us": 0.0}
    return {"start_us": s, "end_us": e, "dur_us": e - s}


def arithmetic_event_checks(conn, anchor_tsc, anchor_tsc_hz, anchor_mono_raw_ns, first_cycle):
    stats = {
        "rows_rel_anchor": 0,
        "max_rel_anchor_err_us": None,
        "avg_rel_anchor_err_us": None,
        "rows_rel_start": 0,
        "max_rel_start_err_us": None,
        "avg_rel_start_err_us": None,
        "rows_mono_raw": 0,
        "max_mono_raw_err_ns": None,
        "avg_mono_raw_err_ns": None,
    }

    rows = conn.execute(
        "SELECT raw_cycles, rel_anchor_us, rel_start_us, mono_raw_est_ns "
        "FROM unified_events "
        "WHERE raw_cycles IS NOT NULL"
    ).fetchall()

    rel_anchor_errs = []
    rel_start_errs = []
    mono_errs = []

    for r in rows:
        raw_cycles = r["raw_cycles"]

        if r["rel_anchor_us"] is not None and anchor_tsc is not None and anchor_tsc_hz:
            exp = (float(raw_cycles) - float(anchor_tsc)) * 1000000.0 / float(anchor_tsc_hz)
            err = abs(float(r["rel_anchor_us"]) - exp)
            rel_anchor_errs.append(err)

        if r["rel_start_us"] is not None and first_cycle is not None and anchor_tsc_hz:
            exp = (float(raw_cycles) - float(first_cycle)) * 1000000.0 / float(anchor_tsc_hz)
            err = abs(float(r["rel_start_us"]) - exp)
            rel_start_errs.append(err)

        if r["mono_raw_est_ns"] is not None and anchor_mono_raw_ns is not None and anchor_tsc is not None and anchor_tsc_hz:
            exp = int(round(float(anchor_mono_raw_ns) + (float(raw_cycles) - float(anchor_tsc)) * 1000000000.0 / float(anchor_tsc_hz)))
            err = abs(int(r["mono_raw_est_ns"]) - exp)
            mono_errs.append(err)

    if rel_anchor_errs:
        stats["rows_rel_anchor"] = len(rel_anchor_errs)
        stats["max_rel_anchor_err_us"] = max(rel_anchor_errs)
        stats["avg_rel_anchor_err_us"] = sum(rel_anchor_errs) / float(len(rel_anchor_errs))

    if rel_start_errs:
        stats["rows_rel_start"] = len(rel_start_errs)
        stats["max_rel_start_err_us"] = max(rel_start_errs)
        stats["avg_rel_start_err_us"] = sum(rel_start_errs) / float(len(rel_start_errs))

    if mono_errs:
        stats["rows_mono_raw"] = len(mono_errs)
        stats["max_mono_raw_err_ns"] = max(mono_errs)
        stats["avg_mono_raw_err_ns"] = sum(mono_errs) / float(len(mono_errs))

    return stats


def arithmetic_energy_checks(conn, anchor_tsc, anchor_tsc_hz, anchor_mono_raw_ns, first_cycle):
    if not has_table(conn, "unified_energy_samples"):
        return None

    stats = {
        "rows_rel_anchor": 0,
        "max_rel_anchor_err_us": None,
        "avg_rel_anchor_err_us": None,
        "rows_rel_start": 0,
        "max_rel_start_err_us": None,
        "avg_rel_start_err_us": None,
    }

    rows = conn.execute(
        "SELECT raw_cycles, rel_anchor_us, rel_start_us "
        "FROM unified_energy_samples "
        "WHERE raw_cycles IS NOT NULL"
    ).fetchall()

    rel_anchor_errs = []
    rel_start_errs = []

    for r in rows:
        raw_cycles = r["raw_cycles"]

        if r["rel_anchor_us"] is not None and anchor_tsc is not None and anchor_tsc_hz:
            exp = (float(raw_cycles) - float(anchor_tsc)) * 1000000.0 / float(anchor_tsc_hz)
            err = abs(float(r["rel_anchor_us"]) - exp)
            rel_anchor_errs.append(err)

        if r["rel_start_us"] is not None and first_cycle is not None and anchor_tsc_hz:
            exp = (float(raw_cycles) - float(first_cycle)) * 1000000.0 / float(anchor_tsc_hz)
            err = abs(float(r["rel_start_us"]) - exp)
            rel_start_errs.append(err)

    if rel_anchor_errs:
        stats["rows_rel_anchor"] = len(rel_anchor_errs)
        stats["max_rel_anchor_err_us"] = max(rel_anchor_errs)
        stats["avg_rel_anchor_err_us"] = sum(rel_anchor_errs) / float(len(rel_anchor_errs))

    if rel_start_errs:
        stats["rows_rel_start"] = len(rel_start_errs)
        stats["max_rel_start_err_us"] = max(rel_start_errs)
        stats["avg_rel_start_err_us"] = sum(rel_start_errs) / float(len(rel_start_errs))

    return stats


def monotonicity_check(conn):
    out = {}

    for source in ("linux", "dpdk"):
        rows = conn.execute(
            "SELECT raw_cycles FROM unified_events "
            "WHERE source=? AND raw_cycles IS NOT NULL "
            "ORDER BY id",
            (source,),
        ).fetchall()
        prev = None
        bad = 0
        for r in rows:
            cur = r["raw_cycles"]
            if prev is not None and cur < prev:
                bad += 1
            prev = cur
        out[source] = {
            "rows": len(rows),
            "backward": bad,
        }

    if has_table(conn, "unified_energy_samples"):
        rows = conn.execute(
            "SELECT raw_cycles FROM unified_energy_samples "
            "WHERE raw_cycles IS NOT NULL "
            "ORDER BY id"
        ).fetchall()
        prev = None
        bad = 0
        for r in rows:
            cur = r["raw_cycles"]
            if prev is not None and cur < prev:
                bad += 1
            prev = cur
        out["energy"] = {
            "rows": len(rows),
            "backward": bad,
        }

    return out


def energy_gap_stats(conn):
    if not has_table(conn, "unified_energy_samples"):
        return None

    rows = conn.execute(
        "SELECT rel_anchor_us FROM unified_energy_samples "
        "WHERE rel_anchor_us IS NOT NULL "
        "ORDER BY rel_anchor_us"
    ).fetchall()
    vals = [float(r["rel_anchor_us"]) for r in rows]
    if len(vals) < 2:
        return {
            "count": len(vals),
            "avg_gap_us": None,
            "min_gap_us": None,
            "max_gap_us": None,
        }

    gaps = []
    prev = vals[0]
    for x in vals[1:]:
        gaps.append(x - prev)
        prev = x

    return {
        "count": len(vals),
        "avg_gap_us": sum(gaps) / float(len(gaps)),
        "min_gap_us": min(gaps),
        "max_gap_us": max(gaps),
    }


def percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def nearest_energy_distance(conn, source, overlap_range):
    if not has_table(conn, "unified_energy_samples"):
        return None

    e_rows = conn.execute(
        "SELECT rel_anchor_us FROM unified_energy_samples "
        "WHERE rel_anchor_us IS NOT NULL "
        "ORDER BY rel_anchor_us"
    ).fetchall()
    energy = [float(r["rel_anchor_us"]) for r in e_rows]
    if not energy:
        return None

    if overlap_range["dur_us"] <= 0.0:
        return None

    s = overlap_range["start_us"]
    e = overlap_range["end_us"]

    ev_rows = conn.execute(
        "SELECT rel_anchor_us FROM unified_events "
        "WHERE source=? AND rel_anchor_us IS NOT NULL "
        "  AND rel_anchor_us BETWEEN ? AND ? "
        "ORDER BY rel_anchor_us",
        (source, s, e),
    ).fetchall()

    dists = []
    for r in ev_rows:
        x = float(r["rel_anchor_us"])
        pos = bisect.bisect_left(energy, x)
        cand = []
        if pos < len(energy):
            cand.append(abs(energy[pos] - x))
        if pos > 0:
            cand.append(abs(energy[pos - 1] - x))
        if cand:
            dists.append(min(cand))

    if not dists:
        return None

    dists.sort()
    return {
        "count": len(dists),
        "min_us": dists[0],
        "median_us": percentile(dists, 0.5),
        "p95_us": percentile(dists, 0.95),
        "max_us": dists[-1],
    }


def verdict_from_checks(mono, ov_lde, ar_ev, ar_en):
    warnings = []
    failed = []

    if mono.get("linux", {}).get("backward", 0) > 0:
        failed.append("linux raw_cycles has backward jumps")
    if mono.get("dpdk", {}).get("backward", 0) > 0:
        failed.append("dpdk raw_cycles has backward jumps")
    if mono.get("energy", {}).get("backward", 0) > 0:
        warnings.append("energy raw_cycles has backward jumps")

    if ov_lde["dur_us"] <= 0.0:
        failed.append("three-way overlap is zero")
    elif ov_lde["dur_us"] < 1000.0:
        warnings.append("three-way overlap is very small (< 1 ms)")

    if ar_ev and ar_ev["max_rel_anchor_err_us"] is not None and ar_ev["max_rel_anchor_err_us"] > 0.05:
        warnings.append("event rel_anchor_us arithmetic error is larger than expected")
    if ar_ev and ar_ev["max_mono_raw_err_ns"] is not None and ar_ev["max_mono_raw_err_ns"] > 5000:
        warnings.append("event mono_raw_est_ns arithmetic error is larger than expected")
    if ar_en and ar_en["max_rel_anchor_err_us"] is not None and ar_en["max_rel_anchor_err_us"] > 0.05:
        warnings.append("energy rel_anchor_us arithmetic error is larger than expected")

    if failed:
        return "FAIL", failed + warnings
    if warnings:
        return "PASS WITH WARNINGS", warnings
    return "PASS", []


def main():
    parser = argparse.ArgumentParser(
        description="Check overlap and arithmetic consistency of unified.sqlite."
    )
    parser.add_argument("-i", "--input", required=True, help="Path to unified.sqlite")
    args = parser.parse_args()

    db_path = os.path.abspath(args.input)
    conn = connect_db(db_path)

    if not has_table(conn, "unified_events"):
        die("this database does not contain unified_events")
    if not has_table(conn, "meta"):
        die("this database does not contain meta")

    anchor_tsc = to_int(get_meta(conn, "anchor_tsc"))
    anchor_tsc_hz = to_int(get_meta(conn, "anchor_tsc_hz"))
    anchor_mono_raw_ns = to_int(get_meta(conn, "anchor_mono_raw_ns"))
    first_cycle = to_int(get_meta(conn, "unified_first_cycle"))

    linux_range = get_range_events(conn, "linux")
    dpdk_range = get_range_events(conn, "dpdk")
    energy_range = get_range_energy(conn) if has_table(conn, "unified_energy_samples") else {
        "rows": 0, "start_us": None, "end_us": None, "dur_us": None
    }

    ov_linux_dpdk = overlap(linux_range, dpdk_range)
    ov_linux_energy = overlap(linux_range, energy_range)
    ov_dpdk_energy = overlap(dpdk_range, energy_range)
    ov_all = triple_overlap(linux_range, dpdk_range, energy_range)

    ar_events = arithmetic_event_checks(conn, anchor_tsc, anchor_tsc_hz, anchor_mono_raw_ns, first_cycle)
    ar_energy = arithmetic_energy_checks(conn, anchor_tsc, anchor_tsc_hz, anchor_mono_raw_ns, first_cycle)
    mono = monotonicity_check(conn)
    en_gap = energy_gap_stats(conn)
    nearest_linux = nearest_energy_distance(conn, "linux", ov_all)
    nearest_dpdk = nearest_energy_distance(conn, "dpdk", ov_all)

    verdict, notes = verdict_from_checks(mono, ov_all, ar_events, ar_energy)

    print_header("SYNC CHECK REPORT")

    print_sub("INPUT")
    print_kv("unified db", db_path)
    print_kv("size", fmt_bytes(os.path.getsize(db_path)))

    print_sub("META / ANCHOR")
    print_kv("run_dir", get_meta(conn, "run_dir", "N/A"))
    print_kv("linux_db", get_meta(conn, "linux_db", "N/A"))
    print_kv("dpdk_db", get_meta(conn, "dpdk_db", "N/A"))
    print_kv("anchor_path", get_meta(conn, "anchor_path", "N/A"))
    print_kv("anchor_tsc", fmt_num(anchor_tsc))
    print_kv("anchor_tsc_hz", fmt_num(anchor_tsc_hz))
    print_kv("anchor_mono_raw_ns", fmt_num(anchor_mono_raw_ns))
    print_kv("unified_first_cycle", fmt_num(first_cycle))
    print_kv("linux_primary_clock", get_meta(conn, "linux_primary_clock", get_meta(conn, "linux_trace_clock", "N/A")))

    print_sub("ROW COUNTS")
    print_kv("unified_events", table_count(conn, "unified_events"))
    print_kv("unified_energy_samples", table_count(conn, "unified_energy_samples") if has_table(conn, "unified_energy_samples") else 0)
    print_kv("rx_events view", table_count(conn, "rx_events") if has_table(conn, "rx_events") else "N/A")
    print_kv("kernel_power_events view", table_count(conn, "kernel_power_events") if has_table(conn, "kernel_power_events") else "N/A")

    print_sub("SOURCE TIME RANGES (rel_anchor_us)")
    for name, rng in [("linux", linux_range), ("dpdk", dpdk_range), ("energy", energy_range)]:
        print("  {}".format(name.upper()))
        print_kv("rows", rng["rows"])
        print_kv("start", fmt_us(rng["start_us"]))
        print_kv("end", fmt_us(rng["end_us"]))
        print_kv("duration", fmt_us(rng["dur_us"]))
        print()

    print_sub("PAIRWISE OVERLAP")
    for name, ov, a, b in [
        ("linux ∩ dpdk", ov_linux_dpdk, linux_range, dpdk_range),
        ("linux ∩ energy", ov_linux_energy, linux_range, energy_range),
        ("dpdk ∩ energy", ov_dpdk_energy, dpdk_range, energy_range),
    ]:
        print("  {}".format(name))
        print_kv("overlap start", fmt_us(ov["start_us"]))
        print_kv("overlap end", fmt_us(ov["end_us"]))
        print_kv("overlap duration", fmt_us(ov["dur_us"]))
        print_kv("coverage of first source", fmt_pct(ov["dur_us"], a["dur_us"]))
        print_kv("coverage of second source", fmt_pct(ov["dur_us"], b["dur_us"]))
        print()

    print_sub("THREE-WAY OVERLAP")
    print_kv("linux ∩ dpdk ∩ energy start", fmt_us(ov_all["start_us"]))
    print_kv("linux ∩ dpdk ∩ energy end", fmt_us(ov_all["end_us"]))
    print_kv("linux ∩ dpdk ∩ energy duration", fmt_us(ov_all["dur_us"]))
    print_kv("coverage of linux", fmt_pct(ov_all["dur_us"], linux_range["dur_us"]))
    print_kv("coverage of dpdk", fmt_pct(ov_all["dur_us"], dpdk_range["dur_us"]))
    print_kv("coverage of energy", fmt_pct(ov_all["dur_us"], energy_range["dur_us"]))

    print_sub("ARITHMETIC CONSISTENCY - EVENTS")
    print_kv("rows checked rel_anchor_us", ar_events["rows_rel_anchor"])
    print_kv("max |rel_anchor_us error|", fmt_us(ar_events["max_rel_anchor_err_us"]))
    print_kv("avg |rel_anchor_us error|", fmt_us(ar_events["avg_rel_anchor_err_us"]))
    print_kv("rows checked rel_start_us", ar_events["rows_rel_start"])
    print_kv("max |rel_start_us error|", fmt_us(ar_events["max_rel_start_err_us"]))
    print_kv("avg |rel_start_us error|", fmt_us(ar_events["avg_rel_start_err_us"]))
    print_kv("rows checked mono_raw_est_ns", ar_events["rows_mono_raw"])
    print_kv("max |mono_raw_est_ns error|", fmt_ns(ar_events["max_mono_raw_err_ns"]))
    print_kv("avg |mono_raw_est_ns error|", fmt_ns(ar_events["avg_mono_raw_err_ns"]))

    print_sub("ARITHMETIC CONSISTENCY - ENERGY")
    if ar_energy is None:
        print("  no unified_energy_samples table")
    else:
        print_kv("rows checked rel_anchor_us", ar_energy["rows_rel_anchor"])
        print_kv("max |rel_anchor_us error|", fmt_us(ar_energy["max_rel_anchor_err_us"]))
        print_kv("avg |rel_anchor_us error|", fmt_us(ar_energy["avg_rel_anchor_err_us"]))
        print_kv("rows checked rel_start_us", ar_energy["rows_rel_start"])
        print_kv("max |rel_start_us error|", fmt_us(ar_energy["max_rel_start_err_us"]))
        print_kv("avg |rel_start_us error|", fmt_us(ar_energy["avg_rel_start_err_us"]))

    print_sub("MONOTONICITY")
    for src in ("linux", "dpdk", "energy"):
        if src in mono:
            print("  {}".format(src.upper()))
            print_kv("rows checked", mono[src]["rows"])
            print_kv("backward jumps", mono[src]["backward"])
            print()

    print_sub("ENERGY SAMPLING USEFULNESS")
    if en_gap is None:
        print("  no energy table")
    else:
        print_kv("energy sample count", en_gap["count"])
        print_kv("avg energy gap", fmt_us(en_gap["avg_gap_us"]))
        print_kv("min energy gap", fmt_us(en_gap["min_gap_us"]))
        print_kv("max energy gap", fmt_us(en_gap["max_gap_us"]))
        print()
        print("  nearest energy sample distance to events inside the 3-way overlap")
        if nearest_linux is None:
            print("    linux : N/A")
        else:
            print("    linux")
            print_kv("pairs checked", nearest_linux["count"])
            print_kv("min distance", fmt_us(nearest_linux["min_us"]))
            print_kv("median distance", fmt_us(nearest_linux["median_us"]))
            print_kv("p95 distance", fmt_us(nearest_linux["p95_us"]))
            print_kv("max distance", fmt_us(nearest_linux["max_us"]))
        print()
        if nearest_dpdk is None:
            print("    dpdk  : N/A")
        else:
            print("    dpdk")
            print_kv("pairs checked", nearest_dpdk["count"])
            print_kv("min distance", fmt_us(nearest_dpdk["min_us"]))
            print_kv("median distance", fmt_us(nearest_dpdk["median_us"]))
            print_kv("p95 distance", fmt_us(nearest_dpdk["p95_us"]))
            print_kv("max distance", fmt_us(nearest_dpdk["max_us"]))

    print_sub("FINAL VERDICT")
    print_kv("result", verdict)
    if notes:
        print("  notes")
        for n in notes:
            print("    - {}".format(n))
    else:
        print("  notes                         : none")

    print("=" * 96)
    ok("done")
    conn.close()


if __name__ == "__main__":
    main()
