#!/usr/bin/env python3
# -*- coding: utf-8 -*-



from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Dict, List, Tuple

EVENT_COL_CANDIDATES = [
    "event_name",
    "event",
    "type",
    "name",
]

MAX_EVENT_VALUES_DEFAULT = 20


def die(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def fmt_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(n)
    idx = 0
    while x >= 1024.0 and idx < len(units) - 1:
        x /= 1024.0
        idx += 1
    return f"{x:.1f} {units[idx]}"


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def fetch_one(conn: sqlite3.Connection, sql: str, params: Tuple = ()) -> sqlite3.Row:
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row


def list_tables(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    return rows


def list_columns(conn: sqlite3.Connection, table: str) -> List[dict]:
    cur = conn.execute(f"PRAGMA table_info({qident(table)})")
    out = []
    for cid, name, coltype, notnull, dflt, pk in cur.fetchall():
        out.append({
            "cid": cid,
            "name": name,
            "type": coltype or "",
            "notnull": bool(notnull),
            "default": dflt,
            "pk": bool(pk),
        })
    cur.close()
    return out


def table_row_count(conn: sqlite3.Connection, table: str) -> int:
    row = fetch_one(conn, f"SELECT COUNT(*) FROM {qident(table)}")
    return int(row[0]) if row else 0


def detect_event_column(columns: List[dict]) -> str | None:
    names = [c["name"] for c in columns]
    lower_to_real = {n.lower(): n for n in names}

    for cand in EVENT_COL_CANDIDATES:
        if cand in lower_to_real:
            return lower_to_real[cand]

    for n in names:
        low = n.lower()
        if "event" in low:
            return n

    return None


def event_counts(conn: sqlite3.Connection, table: str, event_col: str, limit: int) -> List[Tuple[str, int]]:
    sql = f"""
        SELECT {qident(event_col)} AS ev, COUNT(*) AS cnt
        FROM {qident(table)}
        WHERE {qident(event_col)} IS NOT NULL
        GROUP BY {qident(event_col)}
        ORDER BY cnt DESC, ev
        LIMIT ?
    """
    cur = conn.execute(sql, (limit,))
    rows = []
    for ev, cnt in cur.fetchall():
        rows.append((str(ev), int(cnt)))
    cur.close()
    return rows


def sample_rows(conn: sqlite3.Connection, table: str, count: int) -> Tuple[List[str], List[tuple]]:
    cur = conn.execute(f"SELECT * FROM {qident(table)} LIMIT ?", (count,))
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    cur.close()
    return cols, rows


def compact_value(v, max_len: int = 100) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bytes):
        s = f"<bytes:{len(v)}>"
    elif isinstance(v, (dict, list, tuple)):
        s = json.dumps(v, ensure_ascii=False)
    else:
        s = str(v)
    s = s.replace("\n", " ")
    if len(s) > max_len:
        s = s[:max_len - 3] + "..."
    return s


def parse_sample_table_args(items: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        if ":" not in item:
            die(f"--sample-table must look like table:count, got: {item}")
        table, count_s = item.split(":", 1)
        table = table.strip()
        count_s = count_s.strip()
        if not table:
            die(f"empty table name in --sample-table: {item}")
        try:
            count = int(count_s)
        except Exception:
            die(f"invalid count in --sample-table: {item}")
        if count <= 0:
            die(f"sample count must be > 0 in --sample-table: {item}")
        out[table] = count
    return out


def print_db_report(db_path: str, sample_default: int, sample_per_table: Dict[str, int], max_event_values: int) -> None:
    if not os.path.exists(db_path):
        die(f"database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    file_size = os.path.getsize(db_path)
    page_count = int(fetch_one(conn, "PRAGMA page_count")[0])
    page_size = int(fetch_one(conn, "PRAGMA page_size")[0])
    tables = list_tables(conn)

    print("=" * 88)
    print(f"Database : {db_path}")
    print(f"Size     : {fmt_bytes(file_size)}")
    print(f"Tables   : {len(tables)}")
    print(f"Pages    : {page_count}  (page_size={page_size})")
    print("=" * 88)
    print()

    print("Hierarchy")
    print("-" * 88)
    if not tables:
        print("  (no user tables)")
    for t in tables:
        cols = list_columns(conn, t)
        row_count = table_row_count(conn, t)
        event_col = detect_event_column(cols)

        print(f"|-- {t}")
        print(f"|   |-- rows    : {row_count}")
        print(f"|   |-- columns : {len(cols)}")
        for c in cols:
            extras = []
            if c["pk"]:
                extras.append("PK")
            if c["notnull"]:
                extras.append("NOT NULL")
            if c["default"] is not None:
                extras.append(f"default={c['default']}")
            extra_txt = f" [{' ; '.join(extras)}]" if extras else ""
            ctype = c["type"] if c["type"] else "UNSPEC"
            print(f"|   |   |-- {c['name']} : {ctype}{extra_txt}")

        if event_col:
            top_events = event_counts(conn, t, event_col, max_event_values)
            print(f"|   |-- event counter : {event_col}")
            if top_events:
                for ev, cnt in top_events:
                    print(f"|   |   |-- {ev}  ->  {cnt}")
            else:
                print(f"|   |   `-- none")
        print()

    print("-" * 88)
    print("Quick summary")
    print("-" * 88)
    for t in tables:
        cols = list_columns(conn, t)
        row_count = table_row_count(conn, t)
        col_names = ", ".join(c["name"] for c in cols)
        print(f"- {t}")
        print(f"  rows    : {row_count}")
        print(f"  columns : {col_names}")

        event_col = detect_event_column(cols)
        if event_col:
            top_events = event_counts(conn, t, event_col, max_event_values)
            if top_events:
                preview = ", ".join(f"{ev}={cnt}" for ev, cnt in top_events[:8])
                print(f"  events  : {preview}")
            else:
                print(f"  events  : none")
        print()

    sample_targets: Dict[str, int] = {}
    if sample_default > 0:
        for t in tables:
            sample_targets[t] = sample_default
    for t, count in sample_per_table.items():
        sample_targets[t] = count

    if sample_targets:
        print("-" * 88)
        print("Samples")
        print("-" * 88)
        for t in tables:
            if t not in sample_targets:
                continue
            cnt = sample_targets[t]
            cols, rows = sample_rows(conn, t, cnt)
            print(f"- table: {t}")
            print(f"  showing up to {cnt} row(s)")
            print(f"  columns: {', '.join(cols)}")
            if not rows:
                print("  rows   : none")
                print()
                continue
            for idx, row in enumerate(rows, 1):
                print(f"  row {idx}")
                for col, val in zip(cols, row):
                    print(f"    - {col}: {compact_value(val)}")
            print()

    conn.close()


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Readable overview for one or more SQLite databases."
    )
    p.add_argument(
        "-i", "--input",
        nargs="+",
        required=True,
        help="SQLite database path(s)",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Show N sample rows from every table",
    )
    p.add_argument(
        "--sample-table",
        action="append",
        default=[],
        help="Show sample rows only for one table, format: table:count (repeatable)",
    )
    p.add_argument(
        "--max-event-values",
        type=int,
        default=MAX_EVENT_VALUES_DEFAULT,
        help="Maximum distinct event values to print per event table",
    )
    return p


def main() -> None:
    args = make_parser().parse_args()

    if args.sample < 0:
        die("--sample must be >= 0")
    if args.max_event_values <= 0:
        die("--max-event-values must be > 0")

    sample_per_table = parse_sample_table_args(args.sample_table)

    first = True
    for path in args.input:
        if not first:
            print()
        first = False
        print_db_report(path, args.sample, sample_per_table, args.max_event_values)


if __name__ == "__main__":
    main()
