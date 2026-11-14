"""
Export the finished warehouse (dbt marts + a slim copy of the raw CDC events)
from the local Postgres warehouse into one DuckDB file:

    dashboard/data/warehouse.duckdb

The online dashboard (Streamlit Community Cloud) reads this file, so the public
demo needs no database server, no passwords and never goes to sleep.

Run after `dbt build`, from the project root with the venv active:
    python scripts/export_duckdb.py
Then commit and push the .duckdb file to refresh the online dashboard.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dashboard" / "data" / "warehouse.duckdb"

# raw events: only the columns the Data pipeline page needs (no JSON payloads)
RAW_COLUMNS = ["event_id", "topic", "kafka_partition", "kafka_offset", "table_name",
               "op", "is_snapshot", "source_ts_ms", "loaded_at"]


def duck_type(pg_type: str) -> str:
    """Map a Postgres column type to the matching DuckDB type."""
    t = pg_type.lower()
    if t.startswith("numeric("):
        return t.replace("numeric", "DECIMAL")
    return {
        "integer": "INTEGER", "bigint": "BIGINT", "smallint": "SMALLINT",
        "boolean": "BOOLEAN", "date": "DATE", "text": "VARCHAR",
        "timestamp with time zone": "TIMESTAMPTZ",
        "timestamp without time zone": "TIMESTAMP",
        "double precision": "DOUBLE", "real": "FLOAT",
        "numeric": "DOUBLE",          # unbounded numeric (e.g. ratios) -> double
    }.get(t, "VARCHAR")               # text, varchar, char(n), anything else


def pg_columns(cur, schema: str, table: str) -> list[tuple[str, str]]:
    cur.execute("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        JOIN pg_class c     ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """, (schema, table))
    return cur.fetchall()


def copy_table(cur, duck, schema: str, table: str, only: list[str] | None = None) -> int:
    cols = [(n, t) for n, t in pg_columns(cur, schema, table) if only is None or n in only]
    names = [n for n, _ in cols]

    # numbers and timestamps travel as text and are cast exactly by DuckDB
    # (keeps exact decimals, and dates like 9999-12-31 that pandas can't hold)
    def select_expr(name, pg_type):
        exact = duck_type(pg_type).startswith(("DECIMAL", "TIMESTAMP", "DATE", "DOUBLE"))
        return f'"{name}"::text' if exact else f'"{name}"'

    cur.execute(f"SELECT {', '.join(select_expr(n, t) for n, t in cols)} FROM {schema}.{table}")
    df = pd.DataFrame(cur.fetchall(), columns=names)

    ddl = ", ".join(f'"{n}" {duck_type(t)}' for n, t in cols)
    duck.execute(f'CREATE TABLE {schema}."{table}" ({ddl})')
    duck.register("src", df)
    casts = ", ".join(f'CAST("{n}" AS {duck_type(t)})' for n, t in cols)
    duck.execute(f'INSERT INTO {schema}."{table}" SELECT {casts} FROM src')
    duck.unregister("src")
    return len(df)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)

    with psycopg.connect(
        host=os.getenv("WH_HOST", "localhost"),
        port=os.getenv("WH_PORT", "5434"),
        dbname=os.getenv("WH_DB", "warehouse"),
        user=os.getenv("WH_USER", "warehouse"),
        password=os.getenv("WH_PASSWORD", "warehouse_pw"),
    ) as conn, conn.cursor() as cur, duckdb.connect(str(tmp)) as duck:
        duck.execute("CREATE SCHEMA marts; CREATE SCHEMA raw; CREATE SCHEMA meta;")

        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'marts' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        for (table,) in cur.fetchall():
            rows = copy_table(cur, duck, "marts", table)
            print(f"  marts.{table:<30} {rows:>8,} rows")

        rows = copy_table(cur, duck, "raw", "cdc_events", only=RAW_COLUMNS)
        print(f"  raw.cdc_events (slim){'':<12} {rows:>8,} rows")

        duck.execute("CREATE TABLE meta.export_info AS SELECT ?::TIMESTAMPTZ AS exported_at",
                     [datetime.now(timezone.utc)])

    tmp.replace(OUT)   # swap in atomically, so a half-written file is never used
    print(f"\nWrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")
    print("Commit and push it to update the online dashboard.")


if __name__ == "__main__":
    main()
