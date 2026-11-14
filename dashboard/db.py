"""
Shared helpers for every dashboard page: warehouse queries, the period filter,
KPI deltas and the "What this means" explanation boxes.
"""
import os
from datetime import date, timedelta

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

PERIODS = {"Last 30 days": 30, "Last 90 days": 90, "Last 365 days": 365, "All time": None}


@st.cache_data(ttl=60, show_spinner=False)
def query(sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Run a query against the warehouse and return a DataFrame (cached 60 s)."""
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


def page_setup(title: str, subtitle: str) -> None:
    st.set_page_config(page_title=f"{title} | Shop analytics", layout="wide")
    st.title(title)
    st.caption(subtitle)
    with st.sidebar:
        if st.button("Refresh data", use_container_width=True):
            st.cache_data.clear()
        st.caption("Data refreshes every 60 seconds. Run `dbt build` to update the marts.")


def period_filter() -> dict:
    """Sidebar period selector. Returns current and previous period date bounds."""
    bounds = query("""
        SELECT min(date_day) AS first_day, max(date_day) AS last_day
        FROM marts.mart_daily_kpis
    """).iloc[0]
    first_day: date = bounds.first_day
    last_day: date = bounds.last_day

    options = list(PERIODS)
    if "period" not in st.session_state:
        st.session_state.period = "Last 90 days"
    choice = st.sidebar.radio("Period", options, index=options.index(st.session_state.period))
    st.session_state.period = choice

    days = PERIODS[choice]
    start = first_day if days is None else max(first_day, last_day - timedelta(days=days - 1))
    length = (last_day - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    st.sidebar.caption(f"Showing **{start:%d %b %Y} to {last_day:%d %b %Y}**, "
                       f"compared with the {length} days before.")
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
