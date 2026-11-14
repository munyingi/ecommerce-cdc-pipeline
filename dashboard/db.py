"""
Shared helpers for every dashboard page: warehouse queries, the period filter,
KPI deltas and the "What this means" explanation boxes.

Two backends, same SQL:
  - postgres: the live local warehouse (used when WH_HOST is set, e.g. from .env)
  - duckdb:   dashboard/data/warehouse.duckdb, a snapshot exported by
              scripts/export_duckdb.py (used online on Streamlit Community Cloud)
Force one with DASHBOARD_BACKEND=postgres or DASHBOARD_BACKEND=duckdb.
"""
import os
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

DUCKDB_FILE = Path(__file__).resolve().parent / "data" / "warehouse.duckdb"
BACKEND = os.getenv("DASHBOARD_BACKEND") or ("postgres" if os.getenv("WH_HOST") else "duckdb")

PERIODS = {"Last 30 days": 30, "Last 90 days": 90, "Last 365 days": 365, "All time": None}


def _to_duckdb(sql: str) -> str:
    """The few spots where Postgres and DuckDB SQL differ."""
    sql = sql.replace("%s", "?")                                   # parameter style
    sql = re.sub(r"::float\b(?!8)", "::double", sql)               # float = 4 bytes in DuckDB
    # a snapshot has no "now": measure against the moment it was exported
    return sql.replace("now()", "(SELECT exported_at FROM meta.export_info)")


@st.cache_data(ttl=60, show_spinner=False)
def query(sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Run a query against the warehouse and return a DataFrame (cached 60 s)."""
    if BACKEND == "duckdb":
        import duckdb
        with duckdb.connect(str(DUCKDB_FILE), read_only=True) as con:
            con.execute("SET TimeZone = 'UTC'")   # same session time zone as the Postgres warehouse
            return con.execute(_to_duckdb(sql), list(params or ())).df()

    import psycopg
    with psycopg.connect(
        host=os.getenv("WH_HOST", "localhost"),
        port=os.getenv("WH_PORT", "5434"),
        dbname=os.getenv("WH_DB", "warehouse"),
        user=os.getenv("WH_USER", "warehouse"),
        password=os.getenv("WH_PASSWORD", "warehouse_pw"),
    ) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def snapshot_time():
    """When the DuckDB snapshot was exported (None when running live on Postgres)."""
    if BACKEND != "duckdb":
        return None
    ts = query("SELECT exported_at AT TIME ZONE 'Europe/Helsinki' AS t FROM meta.export_info").iloc[0].t
    return pd.Timestamp(ts)


def page_setup(title: str, subtitle: str) -> None:
    """Page heading, plus a note on where the data comes from."""
    head, side = st.columns([5, 1], vertical_alignment="bottom")
    with head:
        st.title(title)
        st.caption(subtitle)
    snap = snapshot_time()
    if snap is not None:
        st.info(f"**Online demo.** Data is a snapshot of the warehouse exported on "
                f"**{snap:%d %b %Y %H:%M}** (Helsinki). The live CDC pipeline "
                f"(Postgres → Debezium → Kafka → dbt) runs locally in Docker.",
                icon=":material/info:")
    else:
        with side:
            if st.button("Refresh data", icon=":material/refresh:", use_container_width=True):
                st.cache_data.clear()


def period_filter() -> dict:
    """Period selector at the top of the page. Returns current and previous period bounds."""
    bounds = query("""
        SELECT min(date_day) AS first_day, max(date_day) AS last_day
        FROM marts.mart_daily_kpis
    """).iloc[0]
    first_day: date = pd.Timestamp(bounds.first_day).date()
    last_day: date = pd.Timestamp(bounds.last_day).date()

    # remember the choice across pages
    options = list(PERIODS)
    saved = st.session_state.get("period_choice", "Last 90 days")
    choice = st.radio("Period", options, index=options.index(saved), horizontal=True)
    st.session_state.period_choice = choice

    days = PERIODS[choice]
    start = first_day if days is None else max(first_day, last_day - timedelta(days=days - 1))
    length = (last_day - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    compare = "" if days is None else f", compared with the {length} days before"
    st.caption(f"Showing **{start:%d %b %Y} to {last_day:%d %b %Y}**{compare}.")
    return {"start": start, "end": last_day, "prev_start": prev_start,
            "prev_end": prev_end, "label": choice, "days": length}


def pct_change(current, previous):
    if previous in (None, 0) or pd.isna(previous):
        return None
    return (current - previous) / previous


def delta(current, previous, points: bool = False):
    """Text for st.metric's delta: % change, or percentage-point change for rates."""
    if previous is None or pd.isna(previous):
        return None
    if points:
        return f"{(current - previous) * 100:+.1f} pp"
    change = pct_change(current, previous)
    return None if change is None else f"{change:+.1%}"


def explain(title: str, body: str, expanded: bool = False) -> None:
    with st.expander(f"What this means: {title}", expanded=expanded):
        st.markdown(body)


def eur(value: float) -> str:
    return f"€{value:,.0f}" if abs(value) >= 1000 else f"€{value:,.2f}"
