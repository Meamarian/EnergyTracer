#!/usr/bin/env python3
# -*- coding: utf-8 -*-


from __future__ import print_function

import argparse
import ast
import math
import os
import sqlite3
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


# -----------------------------------------------------------------------------
# USER SWITCHES: change True -> False to disable a specific chart family.
# -----------------------------------------------------------------------------
ENABLE_CHARTS = {
    # general
    "general_hist_event_counts": True,
    "general_hist_rx_count": True,
    "general_hist_zero_nonzero_by_port": True,
    "general_hist_gap_by_port": True,
    "general_hist_idle_counts": True,
    "general_hist_freq_counts": True,
    "general_hist_event_density": True,
    "general_energy_acpi": True,
    "general_energy_pkg_energy": True,
    "general_energy_dram_energy": True,
    "general_energy_pkg_power": True,
    "general_energy_dram_power": True,

    # cpu-wise
    "cpu_timeline_mixed": True,
    "cpu_timeline_stacked": True,
    "cpu_timeline_stacked_rxnz": True,
    "cpu_hist_event_counts": True,
    "cpu_hist_rx_count": True,
    "cpu_hist_idle": True,
    "cpu_hist_freq": True,

    # port-wise
    "port_timeline_mixed": True,
    "port_timeline_stacked": True,
    "port_timeline_stacked_rxnz": True,
    "port_hist_event_counts": True,
    "port_hist_rx_count": True,
    "port_hist_gap_zero": True,
    "port_hist_gap_nonzero": True,
}


# -----------------------------------------------------------------------------
# FIGURE / STYLE CONFIG
# -----------------------------------------------------------------------------
CONFIG = {
    "font_family": "DejaVu Sans",

    # Based on the largest idea from your old code, but kept safely under Agg limits.
    "timeline_width_in": 180.0,
    "timeline_height_in": 18.0,
    "stack_width_in": 180.0,
    "stack_panel_height_in": 8.0,
    "stack_gap_in": 2.8,
    "hist_width_in": 16.0,
    "hist_height_in": 9.5,
    "dpi": 150,

    "bar_width": 0.40,
    "bar_alpha": 0.97,
    "bar_edgewidth": 0.8,
    "headroom_frac": 0.12,

    "grid_alpha": 0.22,
    "grid_linewidth": 0.7,

    "title_fs": 24,
    "label_fs": 21,
    "tick_fs": 16,
    "legend_fs": 15,
    "annot_fs": 8,

    # IEEE-friendly muted but distinct colors + hatches.
    "rx_color": "#B22222",
    "rx_zero_color": "#7F7F7F",
    "zero_color": "#B0B0B0",
    "nonzero_color": "#4C78A8",
    "idle_color": "#72B7B2",
    "freq_color": "#F28E2B",
    "event_count_color": "#4E79A7",
    "density_color": "#9C755F",
    "power_pkg0": "#D95F02",
    "power_pkg1": "#7570B3",
    "power_acpi": "#1B9E77",
    "power_dram": "#E6AB02",

    "cstate_colors": {
        -1: "#228B22",  # wake
        0:  "#08306B",
        1:  "#2171B5",
        2:  "#6BAED6",
        3:  "#9ECAE1",
        4:  "#BDBDBD",
        5:  "#C7C7C7",
        6:  "#D9D9D9",
        7:  "#E0E0E0",
        8:  "#EFEFEF",
        9:  "#FDD0A2",
        10: "#FFF7BC",
    },
}

_FREQ_STEPS = [round(0.8 + 0.1 * i, 1) for i in range(31)]
_FREQ_PALETTE = [
    "#FFF3B0", "#FFE1A8", "#FFD2A1", "#FFC3A0", "#FFB3B3", "#F7A1C4",
    "#EFA0E6", "#D795E8", "#C77DFF", "#B56576", "#E26D5A", "#F29E4C",
    "#F1C453", "#EE6352", "#D64550", "#C81D25", "#A51C30", "#8C1D40",
    "#7A1E48", "#6D213C", "#5E2A2C", "#7B3F00", "#8B4000", "#9C5518",
    "#A45A52", "#A6378B", "#912F56", "#7F1D1D", "#5C0A0A", "#4A0E0E",
    "#330000",
]
FREQ_COLORS = dict((_FREQ_STEPS[i], _FREQ_PALETTE[i]) for i in range(len(_FREQ_STEPS)))


def log(msg):
    print("[INFO] {}".format(msg), flush=True)


def ok(msg):
    print("[OK] {}".format(msg), flush=True)


def warn(msg):
    print("[WARN] {}".format(msg), flush=True)


def die(msg, code=1):
    print("[ERROR] {}".format(msg), file=sys.stderr)
    sys.exit(code)


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def setup_style():
    plt.rcParams.update({
        "font.family": CONFIG["font_family"],
        "font.size": 18,
        "axes.titlesize": CONFIG["title_fs"],
        "axes.labelsize": CONFIG["label_fs"],
        "xtick.labelsize": CONFIG["tick_fs"],
        "ytick.labelsize": CONFIG["tick_fs"],
        "legend.fontsize": CONFIG["legend_fs"],
        "axes.linewidth": 1.0,
    })


def add_grid(ax):
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", linestyle="--", alpha=CONFIG["grid_alpha"], linewidth=CONFIG["grid_linewidth"])


def add_legend(ax):
    handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.005, 1.0),
        frameon=True,
        fancybox=False,
        framealpha=1.0,
        borderaxespad=0.0,
    )


def safe_inches(fig):
    max_px = 65000.0
    max_w_in = max_px / float(CONFIG["dpi"])
    max_h_in = max_px / float(CONFIG["dpi"])
    w, h = fig.get_size_inches()
    scale = min(1.0, max_w_in / w if w > 0 else 1.0, max_h_in / h if h > 0 else 1.0)
    if scale < 1.0:
        fig.set_size_inches(w * scale, h * scale, forward=True)
        warn("figure auto-scaled to avoid renderer limit: {:.3f}x".format(scale))


def save_fig(fig, path):
    safe_inches(fig)
    fig.savefig(path, format="svg", bbox_inches="tight", dpi=CONFIG["dpi"])
    plt.close(fig)
    ok("saved {}".format(path))


def annotate_bars(ax, bars, fmt="{:.0f}", fontsize=12, rotation=0):
    for bar in bars:
        h = bar.get_height()
        if h is None:
            continue
        ax.annotate(
            fmt.format(h),
            xy=(bar.get_x() + bar.get_width() / 2.0, h),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=fontsize,
            rotation=rotation,
            clip_on=False,
        )


def _apply_headroom(ax, frac=None):
    frac = CONFIG["headroom_frac"] if frac is None else frac
    ymin, ymax = ax.get_ylim()
    rng = (ymax - ymin) if ymax > ymin else 1.0
    ax.set_ylim(ymin, ymax + rng * frac)


def query_rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def get_window_bounds_us(args, conn):
    if args.auto or args.autof:
        row = conn.execute(
            "SELECT rel_start_us, port FROM unified_events "
            "WHERE source='dpdk' AND nb_rx IS NOT NULL AND nb_rx > 0 AND rel_start_us IS NOT NULL "
            "ORDER BY rel_start_us LIMIT 1"
        ).fetchone()
        if row is None:
            die("--auto/--autof requested, but no DPDK nb_rx > 0 event exists in unified_events")
        first_nz_us = float(row[0])
        first_port = int(row[1]) if row[1] is not None else None
        cpu_map = getattr(args, "_port_to_cpu", {})
        cpu_for_first = cpu_map.get(first_port)
        log("auto window trigger: first nb_rx>0 at {:.3f} us (port={}, mapped_cpu={})".format(
            first_nz_us, first_port, cpu_for_first
        ))
        if args.autof:
            if cpu_for_first is None:
                die("--autof needs the triggering port to exist in --porttocpu mapping")
            prev_rows = conn.execute(
                "SELECT rel_start_us FROM unified_events "
                "WHERE source='linux' AND cpu=? AND event_name='cpu_frequency' "
                "AND rel_start_us IS NOT NULL AND rel_start_us <= ? "
                "ORDER BY rel_start_us DESC LIMIT ?",
                (cpu_for_first, first_nz_us, int(args.autof_prev)),
            ).fetchall()
            prev_us = [float(r[0]) for r in prev_rows]
            if prev_us:
                start_us = min(prev_us)
                log("autof start from previous {} cpu_frequency events of cpu {} -> {:.3f} us".format(
                    len(prev_us), cpu_for_first, start_us
                ))
            else:
                start_us = first_nz_us
                warn("autof found no previous cpu_frequency events; falling back to first nb_rx>0")
        else:
            start_us = first_nz_us
        end_us = start_us + float(args.auto_d) * 1e6
        log("selected window: {:.3f} us -> {:.3f} us (duration {:.3f} us)".format(
            start_us, end_us, end_us - start_us
        ))
        return start_us, end_us

    start_us = None if args.start_s is None else float(args.start_s) * 1e6
    end_us = None if args.end_s is None else float(args.end_s) * 1e6
    if start_us is not None and end_us is not None and end_us < start_us:
        die("--end-s must be >= --start-s")
    if start_us is None:
        row = conn.execute("SELECT MIN(rel_start_us) FROM unified_events WHERE rel_start_us IS NOT NULL").fetchone()
        start_us = float(row[0]) if row and row[0] is not None else 0.0
    if end_us is None:
        row = conn.execute("SELECT MAX(rel_start_us) FROM unified_events WHERE rel_start_us IS NOT NULL").fetchone()
        end_us = float(row[0]) if row and row[0] is not None else start_us
    log("selected window: {:.3f} us -> {:.3f} us (duration {:.3f} us)".format(start_us, end_us, end_us - start_us))
    return start_us, end_us


def normalize_cstate_value(raw):
    if raw is None:
        return None
    try:
        x = int(float(raw))
    except Exception:
        return None
    if x in (-1, 4294967295):
        return -1
    return x


def cstate_label(state):
    val = normalize_cstate_value(state)
    if val is None:
        return "N/A"
    if val == -1:
        return "wake"
    base = "/sys/devices/system/cpu/cpu0/cpuidle/state{}".format(val)
    name_path = os.path.join(base, "name")
    if os.path.exists(name_path):
        try:
            name = open(name_path, "r").read().strip()
            if name:
                return "C{}".format(val)
        except Exception:
            pass
    return "C{}".format(val)


def normalize_freq_to_ghz(raw):
    if raw is None:
        return None
    v = float(raw)
    if v >= 1e9:
        return v / 1e9
    if v >= 1e6:
        return v / 1e6
    if v > 30:
        return v / 1000.0
    return v


def quantize_freq_step(ghz):
    if ghz is None:
        return None
    return round(float(ghz) + 1e-9, 1)


def freq_label(raw):
    ghz = quantize_freq_step(normalize_freq_to_ghz(raw))
    if ghz is None:
        return "N/A"
    return "{:.1f} GHz".format(ghz)


def c_color(cstate):
    cstate = normalize_cstate_value(cstate)
    return CONFIG["cstate_colors"].get(cstate, "#CCCCCC")


def f_color(freq_raw):
    ghz = quantize_freq_step(normalize_freq_to_ghz(freq_raw))
    if ghz is None:
        return "#CCCCCC"
    return FREQ_COLORS.get(ghz, "#CCCCCC")


def parse_porttocpu(text):
    if text is None:
        die("--porttocpu is mandatory")
    raw = text.strip()
    if not raw:
        die("--porttocpu is empty")
    try:
        parsed = ast.literal_eval("[{}]".format(raw))
    except Exception:
        die("failed to parse --porttocpu. expected format like '(0,4),(1,5),(2,6)'")
    mapping = {}
    for item in parsed:
        if not isinstance(item, tuple) and not isinstance(item, list):
            die("each --porttocpu item must be a pair like (port,cpu)")
        if len(item) != 2:
            die("each --porttocpu item must have exactly 2 elements")
        port = int(item[0])
        cpu = int(item[1])
        if port in mapping and mapping[port] != cpu:
            die("port {} appears multiple times with different cpu values".format(port))
        mapping[port] = cpu
    if not mapping:
        die("--porttocpu produced empty mapping")
    return mapping


def validate_mapping(conn, port_to_cpu):
    db_ports = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT port FROM unified_events WHERE source='dpdk' AND port IS NOT NULL ORDER BY port"
    ).fetchall()]
    if not db_ports:
        die("no DPDK ports found in unified_events")
    missing = [p for p in db_ports if p not in port_to_cpu]
    if missing:
        die("ports found in DB but missing from --porttocpu: {}".format(
            ", ".join(str(x) for x in missing)
        ))
    log("validated --porttocpu against DB ports: {}".format(
        ", ".join("{}->{}".format(p, port_to_cpu[p]) for p in sorted(port_to_cpu))
    ))
    return db_ports


def selected_unified_events(conn, start_us, end_us):
    rows = conn.execute(
        "SELECT source, event_name, rel_start_us, cpu, lcore, port, nb_rx, field_state, prev_rx_class "
        "FROM unified_events WHERE rel_start_us IS NOT NULL AND rel_start_us >= ? AND rel_start_us <= ? "
        "ORDER BY rel_start_us, raw_cycles",
        (start_us, end_us),
    ).fetchall()
    return rows


def energy_rows(conn, start_us, end_us):
    if not table_exists(conn, "unified_energy_samples"):
        return []
    cols = [r[1] for r in conn.execute('PRAGMA table_info("unified_energy_samples")').fetchall()]
    wanted = [
        "rel_start_us", "rel_anchor_us", "raw_cycles", "mono_raw_ns", "mono_ns",
        "pkg_j_sock0", "pkg_j_sock1", "dram_j_sock0", "dram_j_sock1", "acpi_uW",
    ]
    select_cols = [c for c in wanted if c in cols]
    if "rel_start_us" not in select_cols:
        return []
    sql = (
        "SELECT {} FROM unified_energy_samples WHERE rel_start_us IS NOT NULL "
        "AND rel_start_us >= ? AND rel_start_us <= ? ORDER BY rel_start_us"
    ).format(", ".join(select_cols))
    raw = conn.execute(sql, (start_us, end_us)).fetchall()
    out = []
    for r in raw:
        row = {}
        for idx, col in enumerate(select_cols):
            row[col] = r[idx]
        out.append(row)
    return out


def event_scope_name(kind, scope_id):
    if kind == "cpu":
        return "cpu_{}".format(scope_id)
    return "port_{}".format(scope_id)


def scope_events(rows, scope_kind, scope_id, port_to_cpu):
    out = []
    for r in rows:
        source, event_name, rel_start_us, cpu, lcore, port, nb_rx, field_state, prev_rx_class = r
        if scope_kind == "cpu":
            include = False
            if source == "linux" and cpu is not None and int(cpu) == int(scope_id):
                include = True
            elif source == "dpdk" and port is not None and port_to_cpu.get(int(port)) == int(scope_id):
                include = True
            if include:
                out.append(r)
        else:
            map_cpu = port_to_cpu.get(int(scope_id))
            if source == "dpdk" and port is not None and int(port) == int(scope_id):
                out.append(r)
            elif source == "linux" and cpu is not None and map_cpu is not None and int(cpu) == int(map_cpu):
                out.append(r)
    return out


def build_unified_seq(rows, start_us, end_us, pack_rx_zero):
    """
    Build old-style mixed timeline bars using DB events.
    Each event produces a segment whose height is the time to the next event.
    """
    seq = []
    if not rows:
        return seq
    prev_kind = None
    prev_start = None
    prev_meta = None

    def flush_segment(kind, seg_start, seg_end, meta):
        if kind is None or seg_start is None or seg_end is None or seg_end <= seg_start:
            return
        seq.append({
            "idx": len(seq),
            "dt_us": float(seg_end - seg_start),
            "start_kind": kind,
            "start_us": float(seg_start),
            "end_us": float(seg_end),
            "cstate": meta.get("cstate"),
            "freq": meta.get("freq"),
            "pkts": meta.get("pkts"),
            "port": meta.get("port"),
            "cpu": meta.get("cpu"),
            "batch_zero_count": meta.get("batch_zero_count", 0),
        })

    for row in rows:
        source, event_name, rel_us, cpu, lcore, port, nb_rx, field_state, prev_rx_class = row
        kind = None
        meta = {"cpu": cpu, "port": port, "batch_zero_count": 0}
        if source == "linux" and event_name == "cpu_idle":
            kind = "C"
            meta["cstate"] = normalize_cstate_value(field_state)
        elif source == "linux" and event_name == "cpu_frequency":
            kind = "F"
            meta["freq"] = quantize_freq_step(normalize_freq_to_ghz(field_state))
        elif source == "dpdk" and nb_rx is not None:
            if int(nb_rx) == 0:
                kind = "R0" if pack_rx_zero else "R"
                meta["pkts"] = 0
                meta["batch_zero_count"] = 1
            else:
                kind = "R"
                meta["pkts"] = int(nb_rx)
        else:
            continue

        if prev_kind is None:
            prev_kind = kind
            prev_start = float(rel_us)
            prev_meta = meta
            continue

        flush_segment(prev_kind, prev_start, float(rel_us), prev_meta)
        prev_kind = kind
        prev_start = float(rel_us)
        prev_meta = meta

    flush_segment(prev_kind, prev_start, float(end_us), prev_meta)

    if pack_rx_zero and seq:
        packed = []
        run = None
        for s in seq:
            if s["start_kind"] == "R0":
                if run is None:
                    run = dict(s)
                    run["batch_zero_count"] = int(s.get("batch_zero_count", 1) or 1)
                else:
                    run["end_us"] = s["end_us"]
                    run["dt_us"] += s["dt_us"]
                    run["batch_zero_count"] += int(s.get("batch_zero_count", 1) or 1)
                continue
            if run is not None:
                run["idx"] = len(packed)
                packed.append(run)
                run = None
            s = dict(s)
            s["idx"] = len(packed)
            packed.append(s)
        if run is not None:
            run["idx"] = len(packed)
            packed.append(run)
        seq = packed

    for i, s in enumerate(seq):
        s["idx"] = i
    return seq


def build_packed_seq_from_full(seq, igrxz):
    out = []
    i = 0
    n = len(seq)
    igrxz_int = int(igrxz)
    while i < n:
        s = seq[i]
        if s.get("start_kind") != "R" or int(s.get("pkts") or 0) <= 0:
            d = dict(s)
            d["idx"] = len(out)
            out.append(d)
            i += 1
            continue

        first = s
        start_us = float(first["start_us"])
        last_end_us = float(first["end_us"])
        pkts_total = int(first.get("pkts") or 0)
        packed_r_count = 1
        ignored_r0_count = 0
        ignored_r0_us = 0.0

        j = i + 1
        while j < n:
            sj = seq[j]
            kind = sj.get("start_kind")
            if kind == "R" and int(sj.get("pkts") or 0) > 0:
                last_end_us = float(sj["end_us"])
                pkts_total += int(sj.get("pkts") or 0)
                packed_r_count += 1
                j += 1
                continue
            if kind == "R0":
                k = j
                run_count = 0
                run_us = 0.0
                while k < n and seq[k].get("start_kind") == "R0":
                    run_count += int(seq[k].get("batch_zero_count") or 1)
                    run_us += float(seq[k].get("dt_us") or 0.0)
                    k += 1
                next_is_r = (k < n and seq[k].get("start_kind") == "R" and int(seq[k].get("pkts") or 0) > 0)
                if next_is_r and run_count <= igrxz_int:
                    ignored_r0_count += run_count
                    ignored_r0_us += run_us
                    last_end_us = float(seq[k]["end_us"])
                    pkts_total += int(seq[k].get("pkts") or 0)
                    packed_r_count += 1
                    j = k + 1
                    continue
                break
            break

        out.append({
            "idx": len(out),
            "dt_us": max(0.0, last_end_us - start_us),
            "start_kind": "R",
            "start_us": start_us,
            "end_us": last_end_us,
            "cstate": first.get("cstate"),
            "freq": first.get("freq"),
            "pkts": pkts_total,
            "port": first.get("port"),
            "cpu": first.get("cpu"),
            "batch_zero_count": 0,
            "packed_r_count": packed_r_count,
            "ignored_r0_count": ignored_r0_count,
            "ignored_r0_us": ignored_r0_us,
        })
        i = j
    return out


def _event_label(seg, packed=False):
    kind = seg.get("start_kind")
    if kind == "C":
        return cstate_label(seg.get("cstate"))
    if kind == "F":
        return freq_label(seg.get("freq"))
    if kind == "R0":
        port_txt = " p{}".format(seg.get("port")) if seg.get("port") is not None else ""
        return "rx zero{}".format(port_txt)
    if kind == "R":
        port_txt = " p{}".format(seg.get("port")) if seg.get("port") is not None else ""
        if packed and seg.get("packed_r_count") is not None:
            return "rxnz{}".format(port_txt)
        return "rx nz{}".format(port_txt)
    return str(kind)


def draw_mixed(ax, seq, packed=False):
    bw = CONFIG["bar_width"]
    idxs = [s["idx"] for s in seq]
    for s in seq:
        x = s["idx"]
        h = float(s["dt_us"])
        kind = s["start_kind"]
        hatch = None
        if kind == "C":
            color = c_color(s.get("cstate"))
            hatch = "//"
            bars = ax.bar(x, h, width=bw, color=color, alpha=CONFIG["bar_alpha"],
                          edgecolor="black", linewidth=CONFIG["bar_edgewidth"], hatch=hatch)
            txt = "{}\n{:.0f} us".format(cstate_label(s.get("cstate")), h)
        elif kind == "F":
            color = f_color(s.get("freq"))
            hatch = "\\\\"
            bars = ax.bar(x, h, width=bw, color=color, alpha=CONFIG["bar_alpha"],
                          edgecolor="black", linewidth=CONFIG["bar_edgewidth"], hatch=hatch)
            txt = "{}\n{:.0f} us".format(freq_label(s.get("freq")), h)
        elif kind == "R0":
            color = CONFIG["rx_zero_color"]
            hatch = ".."
            bars = ax.bar(x, h, width=bw, color=color, alpha=CONFIG["bar_alpha"],
                          edgecolor="black", linewidth=CONFIG["bar_edgewidth"], hatch=hatch)
            txt = "rx zero\n{} pulls\n{:.0f} us".format(int(s.get("batch_zero_count") or 1), h)
        else:
            color = CONFIG["rx_color"]
            hatch = "xx" if packed else ""
            bars = ax.bar(x, h, width=bw, color=color, alpha=CONFIG["bar_alpha"],
                          edgecolor="black", linewidth=CONFIG["bar_edgewidth"], hatch=hatch)
            port_txt = " p{}".format(s.get("port")) if s.get("port") is not None else ""
            if packed and s.get("packed_r_count") is not None:
                txt = "rxnz{}\n{} pkts\n{} pulls\n{:.0f} us\nigr0:{} / {:.0f} us".format(
                    port_txt,
                    int(s.get("pkts") or 0),
                    int(s.get("packed_r_count") or 1),
                    h,
                    int(s.get("ignored_r0_count") or 0),
                    float(s.get("ignored_r0_us") or 0.0),
                )
            else:
                txt = "rx nz{}\n{} pkts\n{:.0f} us".format(port_txt, int(s.get("pkts") or 0), h)
        ax.text(x, h, txt, ha="center", va="bottom", fontsize=CONFIG["annot_fs"], clip_on=False)

    ax.set_ylabel("Δt (us)")
    ax.set_xticks(idxs)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    add_grid(ax)
    _apply_headroom(ax)


def nearest_index(ts, t):
    if not ts:
        return None
    lo = 0
    hi = len(ts)
    while lo < hi:
        mid = (lo + hi) // 2
        if ts[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    idx = lo
    if idx <= 0:
        return 0
    if idx >= len(ts):
        return len(ts) - 1
    prev_ts = ts[idx - 1]
    next_ts = ts[idx]
    return idx - 1 if abs(t - prev_ts) <= abs(next_ts - t) else idx


def avg_pkg_power_nearest(energy, t0, t1, key):
    if len(energy) < 2:
        return 0.0, None, None
    ts = [float(r.get("rel_start_us")) for r in energy if r.get("rel_start_us") is not None]
    vals = [r.get(key) for r in energy if r.get("rel_start_us") is not None]
    if len(ts) < 2:
        return 0.0, None, None
    i0 = nearest_index(ts, t0)
    i1 = nearest_index(ts, t1)
    if i0 is None or i1 is None or i0 == i1:
        return 0.0, ts[i0] if i0 is not None else None, ts[i1] if i1 is not None else None
    e0 = vals[i0]
    e1 = vals[i1]
    if e0 is None or e1 is None:
        return 0.0, ts[i0], ts[i1]
    dt_s = (float(ts[i1]) - float(ts[i0])) / 1e6
    if dt_s <= 0:
        return 0.0, ts[i0], ts[i1]
    p = max(0.0, (float(e1) - float(e0)) / dt_s)
    return p, ts[i0], ts[i1]


def avg_acpi_power_nearest(energy, t0, t1):
    if not energy:
        return 0.0, None, None
    ts = [float(r.get("rel_start_us")) for r in energy if r.get("rel_start_us") is not None and r.get("acpi_uW") is not None]
    vals = [float(r.get("acpi_uW")) / 1e6 for r in energy if r.get("rel_start_us") is not None and r.get("acpi_uW") is not None]
    if not ts:
        return 0.0, None, None
    i0 = nearest_index(ts, t0)
    i1 = nearest_index(ts, t1)
    if i0 is None or i1 is None:
        return 0.0, None, None
    if i0 == i1:
        return vals[i0], ts[i0], ts[i1]
    lo = min(i0, i1)
    hi = max(i0, i1)
    sample = vals[lo:hi + 1]
    if not sample:
        return 0.0, ts[i0], ts[i1]
    return sum(sample) / float(len(sample)), ts[i0], ts[i1]


def avg_dram_power_nearest(energy, t0, t1):
    if len(energy) < 2:
        return 0.0, None, None
    ts = []
    vals = []
    for r in energy:
        t = r.get("rel_start_us")
        if t is None:
            continue
        d0 = r.get("dram_j_sock0")
        d1 = r.get("dram_j_sock1")
        total = None
        if d0 is not None or d1 is not None:
            total = (0.0 if d0 is None else float(d0)) + (0.0 if d1 is None else float(d1))
        ts.append(float(t))
        vals.append(total)
    if len(ts) < 2:
        return 0.0, None, None
    i0 = nearest_index(ts, t0)
    i1 = nearest_index(ts, t1)
    if i0 is None or i1 is None or i0 == i1:
        return 0.0, ts[i0] if i0 is not None else None, ts[i1] if i1 is not None else None
    e0 = vals[i0]
    e1 = vals[i1]
    if e0 is None or e1 is None:
        return 0.0, ts[i0], ts[i1]
    dt_s = (float(ts[i1]) - float(ts[i0])) / 1e6
    if dt_s <= 0:
        return 0.0, ts[i0], ts[i1]
    p = max(0.0, (float(e1) - float(e0)) / dt_s)
    return p, ts[i0], ts[i1]


def render_stacked_with_power(scope_title, seq, out_path, energy, packed=False):
    if not seq:
        return
    n_panels = 5
    total_h = n_panels * CONFIG["stack_panel_height_in"] + (n_panels - 1) * CONFIG["stack_gap_in"]
    fig, axes = plt.subplots(n_panels, 1, figsize=(CONFIG["stack_width_in"], total_h), sharex=False)
    fig.subplots_adjust(hspace=0.45, top=0.96, bottom=0.05, left=0.04, right=0.90)

    draw_mixed(axes[0], seq, packed=packed)
    axes[0].set_title(scope_title)
    axes[0].set_xlabel("event index")

    panels = [
        ("pkg_j_sock0", CONFIG["power_pkg0"], "CPU0 package power (W)"),
        ("pkg_j_sock1", CONFIG["power_pkg1"], "CPU1 package power (W)"),
        ("acpi", CONFIG["power_acpi"], "ACPI power (W)"),
        ("dram", CONFIG["power_dram"], "DRAM total power (W)"),
    ]

    for ax, (key, color, title) in zip(axes[1:], panels):
        xs = [s["idx"] for s in seq]
        ys = []
        labels = []
        for s in seq:
            t0 = float(s["start_us"])
            t1 = float(s["end_us"])
            if key == "acpi":
                p, p0, p1 = avg_acpi_power_nearest(energy, t0, t1)
            elif key == "dram":
                p, p0, p1 = avg_dram_power_nearest(energy, t0, t1)
            else:
                p, p0, p1 = avg_pkg_power_nearest(energy, t0, t1, key)
            ys.append(p)
            if p0 is not None and p1 is not None:
                labels.append("{:.2f} W\n{:.2f} ms".format(float(p), max(0.0, (float(p1) - float(p0)) / 1000.0)))
            else:
                labels.append("{:.2f} W".format(float(p)))
        bars = ax.bar(xs, ys, width=CONFIG["bar_width"], color=color, alpha=0.97,
                      edgecolor="black", linewidth=CONFIG["bar_edgewidth"], hatch="//")
        for x, y, txt in zip(xs, ys, labels):
            ax.text(x, y, txt, ha="center", va="bottom", fontsize=CONFIG["annot_fs"], clip_on=False)
        ax.set_title(title)
        ax.set_ylabel("W")
        ax.set_xlabel("event index")
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        add_grid(ax)
        _apply_headroom(ax)

    save_fig(fig, out_path)


def render_mixed(scope_title, seq, out_path, packed=False):
    if not seq:
        return
    fig, ax = plt.subplots(figsize=(CONFIG["timeline_width_in"], CONFIG["timeline_height_in"]))
    fig.subplots_adjust(top=0.92, bottom=0.10, left=0.04, right=0.90)
    draw_mixed(ax, seq, packed=packed)
    ax.set_title(scope_title)
    ax.set_xlabel("event index")
    save_fig(fig, out_path)


# -----------------------------------------------------------------------------
# GENERAL CHARTS
# -----------------------------------------------------------------------------
def general_hist_event_counts(conn, out_dir, start_us, end_us):
    if not ENABLE_CHARTS["general_hist_event_counts"]:
        return
    rows = query_rows(
        conn,
        "SELECT source || ':' || event_name AS label, COUNT(*) AS cnt "
        "FROM unified_events WHERE rel_start_us >= ? AND rel_start_us <= ? "
        "GROUP BY source, event_name ORDER BY cnt DESC LIMIT 16",
        (start_us, end_us),
    )
    if not rows:
        return
    labels = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
    bars = ax.bar(range(len(labels)), vals, color=CONFIG["event_count_color"], edgecolor="black", linewidth=0.8, hatch="//")
    ax.set_title("Event type counts")
    ax.set_ylabel("count")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=28, ha="right")
    add_grid(ax)
    annotate_bars(ax, bars, fontsize=10)
    _apply_headroom(ax)
    save_fig(fig, os.path.join(out_dir, "histogram_event_type_count.svg"))


def general_hist_rx_count(conn, out_dir, start_us, end_us):
    if not ENABLE_CHARTS["general_hist_rx_count"]:
        return
    rows = query_rows(
        conn,
        "SELECT nb_rx FROM unified_events WHERE source='dpdk' AND nb_rx IS NOT NULL AND rel_start_us >= ? AND rel_start_us <= ?",
        (start_us, end_us),
    )
    vals = [int(r[0]) for r in rows]
    if not vals:
        return
    max_bin = max(vals)
    bins = range(0, max_bin + 2) if max_bin <= 64 else min(64, int(math.sqrt(len(vals))) + 1)
    fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
    ax.hist(vals, bins=bins, color=CONFIG["freq_color"], edgecolor="black", hatch="//")
    ax.set_title("RX count distribution")
    ax.set_xlabel("nb_rx")
    ax.set_ylabel("poll count")
    add_grid(ax)
    save_fig(fig, os.path.join(out_dir, "histogram_rx_count.svg"))


def general_hist_zero_nonzero_by_port(conn, out_dir, start_us, end_us):
    if not ENABLE_CHARTS["general_hist_zero_nonzero_by_port"]:
        return
    rows = query_rows(
        conn,
        "SELECT port, "
        "SUM(CASE WHEN nb_rx=0 THEN 1 ELSE 0 END) AS zero_polls, "
        "SUM(CASE WHEN nb_rx>0 THEN 1 ELSE 0 END) AS nonzero_polls "
        "FROM unified_events WHERE source='dpdk' AND port IS NOT NULL AND nb_rx IS NOT NULL "
        "AND rel_start_us >= ? AND rel_start_us <= ? "
        "GROUP BY port ORDER BY port",
        (start_us, end_us),
    )
    if not rows:
        return
    ports = [str(r[0]) for r in rows]
    zero = [r[1] for r in rows]
    nonzero = [r[2] for r in rows]
    x = list(range(len(rows)))
    w = 0.38
    fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
    b1 = ax.bar([i - w / 2.0 for i in x], zero, width=w, color=CONFIG["zero_color"], edgecolor="black", hatch="..", label="zero")
    b2 = ax.bar([i + w / 2.0 for i in x], nonzero, width=w, color=CONFIG["nonzero_color"], edgecolor="black", hatch="//", label="nonzero")
    ax.set_title("Zero vs nonzero by port")
    ax.set_xlabel("port")
    ax.set_ylabel("count")
    ax.set_xticks(x)
    ax.set_xticklabels(ports)
    add_grid(ax)
    add_legend(ax)
    annotate_bars(ax, b1, fontsize=10)
    annotate_bars(ax, b2, fontsize=10)
    _apply_headroom(ax)
    save_fig(fig, os.path.join(out_dir, "histogram_rx_zero_nonzero_by_port.svg"))


def general_hist_gap_by_port(conn, out_dir, start_us, end_us):
    if not ENABLE_CHARTS["general_hist_gap_by_port"]:
        return
    rows = query_rows(
        conn,
        "SELECT port, "
        "AVG(CASE WHEN prev_rx_class='zero' THEN gap_from_prev_same_port_ns END) AS avg_zero_ns, "
        "AVG(CASE WHEN prev_rx_class='nonzero' THEN gap_from_prev_same_port_ns END) AS avg_nonzero_ns "
        "FROM unified_events WHERE source='dpdk' AND port IS NOT NULL AND gap_from_prev_same_port_ns IS NOT NULL "
        "AND rel_start_us >= ? AND rel_start_us <= ? "
        "GROUP BY port ORDER BY port",
        (start_us, end_us),
    )
    rows = [r for r in rows if r[1] is not None or r[2] is not None]
    if not rows:
        return
    ports = [str(r[0]) for r in rows]
    zero_us = [(float(r[1]) / 1000.0) if r[1] is not None else 0.0 for r in rows]
    nonzero_us = [(float(r[2]) / 1000.0) if r[2] is not None else 0.0 for r in rows]
    x = list(range(len(rows)))
    w = 0.38
    fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
    b1 = ax.bar([i - w / 2.0 for i in x], zero_us, width=w, color=CONFIG["zero_color"], edgecolor="black", hatch="..", label="after prev zero")
    b2 = ax.bar([i + w / 2.0 for i in x], nonzero_us, width=w, color=CONFIG["nonzero_color"], edgecolor="black", hatch="//", label="after prev nonzero")
    ax.set_title("Average RX gap by port")
    ax.set_xlabel("port")
    ax.set_ylabel("gap (us)")
    ax.set_xticks(x)
    ax.set_xticklabels(ports)
    add_grid(ax)
    add_legend(ax)
    annotate_bars(ax, b1, fmt="{:.2f}", fontsize=10)
    annotate_bars(ax, b2, fmt="{:.2f}", fontsize=10)
    _apply_headroom(ax)
    save_fig(fig, os.path.join(out_dir, "histogram_rx_gap_by_port.svg"))


def general_hist_idle_counts(conn, out_dir, start_us, end_us):
    if not ENABLE_CHARTS["general_hist_idle_counts"]:
        return
    rows = query_rows(
        conn,
        "SELECT field_state, COUNT(*) FROM unified_events WHERE source='linux' AND event_name='cpu_idle' "
        "AND field_state IS NOT NULL AND rel_start_us >= ? AND rel_start_us <= ? "
        "GROUP BY field_state ORDER BY field_state",
        (start_us, end_us),
    )
    if not rows:
        return
    labels = [cstate_label(r[0]) for r in rows]
    vals = [r[1] for r in rows]
    fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
    colors = [c_color(r[0]) for r in rows]
    bars = ax.bar(range(len(labels)), vals, color=colors, edgecolor="black", hatch="//")
    ax.set_title("C-state counts")
    ax.set_xlabel("state")
    ax.set_ylabel("count")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=22, ha="right")
    add_grid(ax)
    annotate_bars(ax, bars, fontsize=10)
    _apply_headroom(ax)
    save_fig(fig, os.path.join(out_dir, "histogram_linux_cstate_count.svg"))


def general_hist_freq_counts(conn, out_dir, start_us, end_us):
    if not ENABLE_CHARTS["general_hist_freq_counts"]:
        return
    rows = query_rows(
        conn,
        "SELECT field_state, COUNT(*) FROM unified_events WHERE source='linux' AND event_name='cpu_frequency' "
        "AND field_state IS NOT NULL AND rel_start_us >= ? AND rel_start_us <= ? "
        "GROUP BY field_state ORDER BY field_state",
        (start_us, end_us),
    )
    if not rows:
        return
    labels = [freq_label(r[0]) for r in rows]
    vals = [r[1] for r in rows]
    colors = [f_color(r[0]) for r in rows]
    fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
    bars = ax.bar(range(len(labels)), vals, color=colors, edgecolor="black", hatch="\\\\")
    ax.set_title("Frequency counts")
    ax.set_xlabel("frequency")
    ax.set_ylabel("count")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=28, ha="right")
    add_grid(ax)
    annotate_bars(ax, bars, fontsize=10)
    _apply_headroom(ax)
    save_fig(fig, os.path.join(out_dir, "histogram_linux_frequency_count.svg"))


def general_hist_event_density(conn, out_dir, start_us, end_us):
    if not ENABLE_CHARTS["general_hist_event_density"]:
        return
    rows = query_rows(
        conn,
        "SELECT rel_start_us FROM unified_events WHERE rel_start_us >= ? AND rel_start_us <= ? ORDER BY rel_start_us",
        (start_us, end_us),
    )
    xs = [float(r[0]) for r in rows]
    if not xs:
        return
    bins = min(120, max(20, int(math.sqrt(len(xs)))))
    fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
    ax.hist(xs, bins=bins, color=CONFIG["density_color"], edgecolor="black", hatch="//")
    ax.set_title("Event density over time")
    ax.set_xlabel("time from unified start (us)")
    ax.set_ylabel("event count")
    add_grid(ax)
    save_fig(fig, os.path.join(out_dir, "histogram_event_density.svg"))


def general_energy_series(energy, out_dir):
    if not energy:
        warn("no unified_energy_samples rows in selected window")
        return
    xs = [float(r.get("rel_start_us")) for r in energy if r.get("rel_start_us") is not None]
    if not xs:
        return

    def plot_series(yvals, title, ylabel, out_name, color, hatch=None):
        if not yvals or all(v is None for v in yvals):
            return
        fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
        ax.plot(xs, yvals, color=color, linewidth=2.2, label=title)
        ax.set_title(title)
        ax.set_xlabel("time from unified start (us)")
        ax.set_ylabel(ylabel)
        add_grid(ax)
        add_legend(ax)
        save_fig(fig, os.path.join(out_dir, out_name))

    if ENABLE_CHARTS["general_energy_acpi"]:
        y = [None if r.get("acpi_uW") is None else float(r.get("acpi_uW")) / 1e6 for r in energy]
        plot_series(y, "ACPI power", "W", "energy_acpi_power_series.svg", CONFIG["power_acpi"])

    if ENABLE_CHARTS["general_energy_pkg_energy"]:
        fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
        did = False
        y0 = [r.get("pkg_j_sock0") for r in energy]
        y1 = [r.get("pkg_j_sock1") for r in energy]
        if any(v is not None for v in y0):
            ax.plot(xs, y0, color=CONFIG["power_pkg0"], linewidth=2.0, label="pkg sock0")
            did = True
        if any(v is not None for v in y1):
            ax.plot(xs, y1, color=CONFIG["power_pkg1"], linewidth=2.0, label="pkg sock1")
            did = True
        if did:
            ax.set_title("Package energy")
            ax.set_xlabel("time from unified start (us)")
            ax.set_ylabel("J")
            add_grid(ax)
            add_legend(ax)
            save_fig(fig, os.path.join(out_dir, "energy_pkg_energy_series.svg"))
        else:
            plt.close(fig)

    if ENABLE_CHARTS["general_energy_dram_energy"]:
        fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
        did = False
        y0 = [r.get("dram_j_sock0") for r in energy]
        y1 = [r.get("dram_j_sock1") for r in energy]
        if any(v is not None for v in y0):
            ax.plot(xs, y0, color=CONFIG["power_dram"], linewidth=2.0, label="dram sock0")
            did = True
        if any(v is not None for v in y1):
            ax.plot(xs, y1, color="#B8860B", linewidth=2.0, label="dram sock1")
            did = True
        if did:
            ax.set_title("DRAM energy")
            ax.set_xlabel("time from unified start (us)")
            ax.set_ylabel("J")
            add_grid(ax)
            add_legend(ax)
            save_fig(fig, os.path.join(out_dir, "energy_dram_energy_series.svg"))
        else:
            plt.close(fig)

    def derive_power(keys):
        out_x = []
        out_y = []
        prev_t = None
        prev_e = None
        for r in energy:
            t = r.get("rel_start_us")
            if t is None:
                continue
            e = 0.0
            anyv = False
            for k in keys:
                v = r.get(k)
                if v is not None:
                    e += float(v)
                    anyv = True
            if not anyv:
                prev_t = None
                prev_e = None
                continue
            if prev_t is not None and prev_e is not None:
                dt = (float(t) - float(prev_t)) / 1e6
                if dt > 0:
                    out_x.append((float(t) + float(prev_t)) / 2.0)
                    out_y.append(max(0.0, (e - prev_e) / dt))
            prev_t = float(t)
            prev_e = e
        return out_x, out_y

    if ENABLE_CHARTS["general_energy_pkg_power"]:
        x, y = derive_power(["pkg_j_sock0", "pkg_j_sock1"])
        if x:
            fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
            ax.plot(x, y, color=CONFIG["power_pkg0"], linewidth=2.2, label="pkg total power")
            ax.set_title("Package power")
            ax.set_xlabel("time from unified start (us)")
            ax.set_ylabel("W")
            add_grid(ax)
            add_legend(ax)
            save_fig(fig, os.path.join(out_dir, "energy_pkg_power_series.svg"))

    if ENABLE_CHARTS["general_energy_dram_power"]:
        x, y = derive_power(["dram_j_sock0", "dram_j_sock1"])
        if x:
            fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
            ax.plot(x, y, color=CONFIG["power_dram"], linewidth=2.2, label="dram total power")
            ax.set_title("DRAM power")
            ax.set_xlabel("time from unified start (us)")
            ax.set_ylabel("W")
            add_grid(ax)
            add_legend(ax)
            save_fig(fig, os.path.join(out_dir, "energy_dram_power_series.svg"))


# -----------------------------------------------------------------------------
# CPU-WISE CHARTS
# -----------------------------------------------------------------------------
def cpu_histograms(scope_cpu, rows, out_dir):
    # event counts
    if ENABLE_CHARTS["cpu_hist_event_counts"]:
        counts = {}
        for r in rows:
            label = "{}:{}".format(r[0], r[1])
            counts[label] = counts.get(label, 0) + 1
        if counts:
            items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:16]
            labels = [x[0] for x in items]
            vals = [x[1] for x in items]
            fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
            bars = ax.bar(range(len(labels)), vals, color=CONFIG["event_count_color"], edgecolor="black", hatch="//")
            ax.set_title("CPU {} event counts".format(scope_cpu))
            ax.set_ylabel("count")
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=28, ha="right")
            add_grid(ax)
            annotate_bars(ax, bars, fontsize=10)
            _apply_headroom(ax)
            save_fig(fig, os.path.join(out_dir, "histogram_event_type_count.svg"))

    if ENABLE_CHARTS["cpu_hist_rx_count"]:
        vals = [int(r[6]) for r in rows if r[0] == "dpdk" and r[6] is not None]
        if vals:
            max_bin = max(vals)
            bins = range(0, max_bin + 2) if max_bin <= 64 else min(64, int(math.sqrt(len(vals))) + 1)
            fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
            ax.hist(vals, bins=bins, color=CONFIG["freq_color"], edgecolor="black", hatch="//")
            ax.set_title("CPU {} RX count distribution".format(scope_cpu))
            ax.set_xlabel("nb_rx")
            ax.set_ylabel("poll count")
            add_grid(ax)
            save_fig(fig, os.path.join(out_dir, "histogram_rx_count.svg"))

    if ENABLE_CHARTS["cpu_hist_idle"]:
        vals = [normalize_cstate_value(r[7]) for r in rows if r[0] == "linux" and r[1] == "cpu_idle" and r[7] is not None]
        if vals:
            uniq = sorted(set(vals))
            counts = [vals.count(v) for v in uniq]
            labels = [cstate_label(v) for v in uniq]
            colors = [c_color(v) for v in uniq]
            fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
            bars = ax.bar(range(len(uniq)), counts, color=colors, edgecolor="black", hatch="//")
            ax.set_title("CPU {} C-state counts".format(scope_cpu))
            ax.set_xlabel("state")
            ax.set_ylabel("count")
            ax.set_xticks(range(len(uniq)))
            ax.set_xticklabels(labels, rotation=20, ha="right")
            add_grid(ax)
            annotate_bars(ax, bars, fontsize=10)
            _apply_headroom(ax)
            save_fig(fig, os.path.join(out_dir, "histogram_cstate_count.svg"))

    if ENABLE_CHARTS["cpu_hist_freq"]:
        vals = [r[7] for r in rows if r[0] == "linux" and r[1] == "cpu_frequency" and r[7] is not None]
        if vals:
            uniq = sorted(set(vals))
            counts = [vals.count(v) for v in uniq]
            labels = [freq_label(v) for v in uniq]
            colors = [f_color(v) for v in uniq]
            fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
            bars = ax.bar(range(len(uniq)), counts, color=colors, edgecolor="black", hatch="\\\\")
            ax.set_title("CPU {} frequency counts".format(scope_cpu))
            ax.set_xlabel("frequency")
            ax.set_ylabel("count")
            ax.set_xticks(range(len(uniq)))
            ax.set_xticklabels(labels, rotation=28, ha="right")
            add_grid(ax)
            annotate_bars(ax, bars, fontsize=10)
            _apply_headroom(ax)
            save_fig(fig, os.path.join(out_dir, "histogram_frequency_count.svg"))


def port_histograms(scope_port, rows, out_dir):
    if ENABLE_CHARTS["port_hist_event_counts"]:
        counts = {}
        for r in rows:
            label = "{}:{}".format(r[0], r[1])
            counts[label] = counts.get(label, 0) + 1
        if counts:
            items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:16]
            labels = [x[0] for x in items]
            vals = [x[1] for x in items]
            fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
            bars = ax.bar(range(len(labels)), vals, color=CONFIG["event_count_color"], edgecolor="black", hatch="//")
            ax.set_title("Port {} event counts".format(scope_port))
            ax.set_ylabel("count")
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=28, ha="right")
            add_grid(ax)
            annotate_bars(ax, bars, fontsize=10)
            _apply_headroom(ax)
            save_fig(fig, os.path.join(out_dir, "histogram_event_type_count.svg"))

    if ENABLE_CHARTS["port_hist_rx_count"]:
        vals = [int(r[6]) for r in rows if r[0] == "dpdk" and r[6] is not None]
        if vals:
            max_bin = max(vals)
            bins = range(0, max_bin + 2) if max_bin <= 64 else min(64, int(math.sqrt(len(vals))) + 1)
            fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
            ax.hist(vals, bins=bins, color=CONFIG["freq_color"], edgecolor="black", hatch="//")
            ax.set_title("Port {} RX count distribution".format(scope_port))
            ax.set_xlabel("nb_rx")
            ax.set_ylabel("poll count")
            add_grid(ax)
            save_fig(fig, os.path.join(out_dir, "histogram_rx_count.svg"))

    if ENABLE_CHARTS["port_hist_gap_zero"]:
        vals = [float(r[8]) if False else None for r in []]  # placeholder; kept disabled by no data path
    if ENABLE_CHARTS["port_hist_gap_nonzero"]:
        vals = [float(r[8]) if False else None for r in []]

    # Better direct queries from rows.
    zero_gaps = []
    nonzero_gaps = []
    prev_ts = None
    prev_class = None
    for r in rows:
        source, event_name, rel_us, cpu, lcore, port, nb_rx, field_state, prev_rx_class = r
        if source != "dpdk" or nb_rx is None:
            continue
        cls = "zero" if int(nb_rx) == 0 else "nonzero"
        if prev_ts is not None:
            gap = float(rel_us) - float(prev_ts)
            if prev_class == "zero":
                zero_gaps.append(gap)
            elif prev_class == "nonzero":
                nonzero_gaps.append(gap)
        prev_ts = float(rel_us)
        prev_class = cls

    if ENABLE_CHARTS["port_hist_gap_zero"] and zero_gaps:
        bins = min(80, max(20, int(math.sqrt(len(zero_gaps)))))
        fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
        ax.hist(zero_gaps, bins=bins, color=CONFIG["zero_color"], edgecolor="black", hatch="..")
        ax.set_title("Port {} gap after previous zero".format(scope_port))
        ax.set_xlabel("gap (us)")
        ax.set_ylabel("count")
        add_grid(ax)
        save_fig(fig, os.path.join(out_dir, "histogram_gap_after_zero.svg"))

    if ENABLE_CHARTS["port_hist_gap_nonzero"] and nonzero_gaps:
        bins = min(80, max(20, int(math.sqrt(len(nonzero_gaps)))))
        fig, ax = plt.subplots(figsize=(CONFIG["hist_width_in"], CONFIG["hist_height_in"]))
        ax.hist(nonzero_gaps, bins=bins, color=CONFIG["nonzero_color"], edgecolor="black", hatch="//")
        ax.set_title("Port {} gap after previous nonzero".format(scope_port))
        ax.set_xlabel("gap (us)")
        ax.set_ylabel("count")
        add_grid(ax)
        save_fig(fig, os.path.join(out_dir, "histogram_gap_after_nonzero.svg"))


def list_chart_groups():
    print("Chart families currently supported:")
    print("  - general/histograms")
    print("  - general/energy")
    print("  - cpu-wise/<cpu>/timelines")
    print("  - cpu-wise/<cpu>/histograms")
    print("  - port-wise/<port>/timelines")
    print("  - port-wise/<port>/histograms")


def make_parser():
    epilog = """
Examples:
  sudo python3 chart.py \
    --run-dir ./traces/run0_l3fwd_power_pl12000 \
    --porttocpu '(0,4),(1,5),(2,6),(3,7),(4,4),(5,5),(6,6),(7,7)'

  sudo python3 chart.py \
    --run-dir ./traces/run0_l3fwd_power_pl12000 \
    --porttocpu '(0,4),(1,5),(2,6),(3,7),(4,4),(5,5),(6,6),(7,7)' \
    --auto --auto-d 0.050 \
    --pack-rx-zero --rxnz --igrxz 2 \
    -o ./traces/run0_l3fwd_power_pl12000/charts_auto
"""
    parser = argparse.ArgumentParser(
        description="Generate general / cpu-wise / port-wise SVG charts from unified.sqlite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    parser.add_argument("--run-dir", required=True, help="Experiment root directory")
    parser.add_argument("--db", default=None, help="Override unified.sqlite path")
    parser.add_argument("-o", "--out-dir", default=None, help="Override output charts directory")
    parser.add_argument("--porttocpu", required=False, help="Mandatory mapping string like '(0,4),(1,5),(2,6)' where first is port and second is cpu/lcore id")
    parser.add_argument("--start-s", type=float, default=None, help="Window start in seconds from unified start")
    parser.add_argument("--end-s", type=float, default=None, help="Window end in seconds from unified start")
    parser.add_argument("--auto", action="store_true", help="Use the first DPDK nb_rx > 0 as window start")
    parser.add_argument("--auto-d", type=float, default=0.050, help="Duration in seconds for --auto / --autof")
    parser.add_argument("--autof", action="store_true", help="Start at the earliest of N previous cpu_frequency events before first nb_rx>0 on the mapped CPU")
    parser.add_argument("--autof-prev", type=int, default=6, help="How many previous cpu_frequency events to include for --autof")
    parser.add_argument("--pack-rx-zero", action="store_true", help="Pack consecutive RX-zero events into grouped R0 bars")
    parser.add_argument("--rxnz", action="store_true", help="Also create extra packed RX-nonzero timeline versions")
    parser.add_argument("--igrxz", type=int, default=0, help="When packing RX nonzero bursts, ignore/bridge up to this many intervening RX-zero pulls")
    parser.add_argument("--debug", action="store_true", help="Print extra logs")
    parser.add_argument("--list-charts", action="store_true", help="Print chart families and exit")
    return parser


def main():
    parser = make_parser()
    args = parser.parse_args()

    if args.list_charts:
        list_chart_groups()
        return

    setup_style()

    run_dir = os.path.abspath(args.run_dir)
    db_path = os.path.abspath(args.db or os.path.join(run_dir, "unified", "unified.sqlite"))
    out_root = os.path.abspath(args.out_dir or os.path.join(run_dir, "charts"))

    if not os.path.isfile(db_path):
        die("db not found: {}".format(db_path))

    conn = sqlite3.connect(db_path)

    port_to_cpu = parse_porttocpu(args.porttocpu)
    args._port_to_cpu = port_to_cpu
    db_ports = validate_mapping(conn, port_to_cpu)
    cpus = sorted(set(int(v) for v in port_to_cpu.values()))

    start_us, end_us = get_window_bounds_us(args, conn)

    all_events = selected_unified_events(conn, start_us, end_us)
    energy = energy_rows(conn, start_us, end_us)

    log("selected unified events: {}".format(len(all_events)))
    log("selected energy rows: {}".format(len(energy)))
    if not all_events:
        warn("no unified events found in selected window")

    general_hist_dir = os.path.join(out_root, "general", "histograms")
    general_energy_dir = os.path.join(out_root, "general", "energy")
    ensure_dir(general_hist_dir)
    ensure_dir(general_energy_dir)

    general_hist_event_counts(conn, general_hist_dir, start_us, end_us)
    general_hist_rx_count(conn, general_hist_dir, start_us, end_us)
    general_hist_zero_nonzero_by_port(conn, general_hist_dir, start_us, end_us)
    general_hist_gap_by_port(conn, general_hist_dir, start_us, end_us)
    general_hist_idle_counts(conn, general_hist_dir, start_us, end_us)
    general_hist_freq_counts(conn, general_hist_dir, start_us, end_us)
    general_hist_event_density(conn, general_hist_dir, start_us, end_us)
    general_energy_series(energy, general_energy_dir)

    # CPU-wise
    for cpu in cpus:
        cpu_rows = scope_events(all_events, "cpu", cpu, port_to_cpu)
        if not cpu_rows:
            warn("cpu-wise scope cpu {} has no rows in selected window".format(cpu))
            continue
        cpu_root = os.path.join(out_root, "cpu-wise", event_scope_name("cpu", cpu))
        cpu_timeline_dir = os.path.join(cpu_root, "timelines")
        cpu_hist_dir = os.path.join(cpu_root, "histograms")
        ensure_dir(cpu_timeline_dir)
        ensure_dir(cpu_hist_dir)

        ports_for_cpu = sorted([p for p, c in port_to_cpu.items() if c == cpu])
        log("cpu-wise cpu {} uses mapped ports {} and has {} rows".format(cpu, ports_for_cpu, len(cpu_rows)))

        seq = build_unified_seq(cpu_rows, start_us, end_us, pack_rx_zero=bool(args.pack_rx_zero))
        if seq:
            title = "Unified timeline CPU {} (ports: {})".format(cpu, ",".join(str(p) for p in ports_for_cpu) if ports_for_cpu else "none")
            if ENABLE_CHARTS["cpu_timeline_mixed"]:
                render_mixed(title, seq, os.path.join(cpu_timeline_dir, "unified_timeline_cpu_{}_mixed.svg".format(cpu)), packed=False)
            if ENABLE_CHARTS["cpu_timeline_stacked"]:
                render_stacked_with_power(title + " stacked power", seq, os.path.join(cpu_timeline_dir, "unified_timeline_cpu_{}_stacked_power.svg".format(cpu)), energy, packed=False)
            if args.rxnz and ENABLE_CHARTS["cpu_timeline_stacked_rxnz"]:
                seq_r = build_packed_seq_from_full(seq, args.igrxz)
                render_stacked_with_power(title + " stacked power (rxnz packed)", seq_r, os.path.join(cpu_timeline_dir, "unified_timeline_cpu_{}_rxnzpacked_stacked_power.svg".format(cpu)), energy, packed=True)
        cpu_histograms(cpu, cpu_rows, cpu_hist_dir)

    # Port-wise
    for port in sorted(db_ports):
        port_rows = scope_events(all_events, "port", port, port_to_cpu)
        if not port_rows:
            warn("port-wise port {} has no rows in selected window".format(port))
            continue
        port_root = os.path.join(out_root, "port-wise", event_scope_name("port", port))
        port_timeline_dir = os.path.join(port_root, "timelines")
        port_hist_dir = os.path.join(port_root, "histograms")
        ensure_dir(port_timeline_dir)
        ensure_dir(port_hist_dir)

        cpu_for_port = port_to_cpu.get(port)
        log("port-wise port {} maps to cpu {} and has {} rows".format(port, cpu_for_port, len(port_rows)))

        seq = build_unified_seq(port_rows, start_us, end_us, pack_rx_zero=bool(args.pack_rx_zero))
        if seq:
            title = "Unified timeline port {} (cpu {})".format(port, cpu_for_port)
            if ENABLE_CHARTS["port_timeline_mixed"]:
                render_mixed(title, seq, os.path.join(port_timeline_dir, "unified_timeline_port_{}_mixed.svg".format(port)), packed=False)
            if ENABLE_CHARTS["port_timeline_stacked"]:
                render_stacked_with_power(title + " stacked power", seq, os.path.join(port_timeline_dir, "unified_timeline_port_{}_stacked_power.svg".format(port)), energy, packed=False)
            if args.rxnz and ENABLE_CHARTS["port_timeline_stacked_rxnz"]:
                seq_r = build_packed_seq_from_full(seq, args.igrxz)
                render_stacked_with_power(title + " stacked power (rxnz packed)", seq_r, os.path.join(port_timeline_dir, "unified_timeline_port_{}_rxnzpacked_stacked_power.svg".format(port)), energy, packed=True)
        port_histograms(port, port_rows, port_hist_dir)

    conn.close()
    ok("charts written to {}".format(out_root))


if __name__ == "__main__":
    main()
