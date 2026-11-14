"""Operations: fulfilment speed, delivery promises, backlog and cancellations."""
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))  # find db.py
from db import delta, explain, page_setup, period_filter, query

page_setup("Operations",
           "Are orders paid, packed and delivered fast enough, and where do they get stuck?")
p = period_filter()

OPS_SQL = """
    SELECT
      count(*)                                                        AS orders,
      avg(is_cancelled::int)::float                                   AS cancel_rate,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_to_pay)::float  AS p50_pay_h,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_to_ship)::float AS p50_ship_h,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_to_deliver)::float / 24 AS p50_deliver_d,
      percentile_cont(0.9) WITHIN GROUP (ORDER BY hours_to_deliver)::float / 24 AS p90_deliver_d,
      avg((hours_to_deliver <= 72)::int)
        FILTER (WHERE delivered_at IS NOT NULL)::float                AS within_3_days
    FROM marts.fct_orders
    WHERE (placed_at AT TIME ZONE 'Europe/Helsinki')::date BETWEEN %s AND %s
"""
cur = query(OPS_SQL, (p["start"], p["end"])).iloc[0]
prev = query(OPS_SQL, (p["prev_start"], p["prev_end"])).iloc[0] if p["label"] != "All time" else None


def pv(col):
    return None if prev is None else prev[col]


def fmt(v, spec):
    return "n/a" if v is None or pd.isna(v) else format(v, spec)


c = st.columns(5)
c[0].metric("Median time to pay", f"{fmt(None if cur.p50_pay_h is None else cur.p50_pay_h * 60, '.0f')} min",
            delta(cur.p50_pay_h, pv("p50_pay_h")), delta_color="inverse")
c[1].metric("Median time to ship", f"{fmt(cur.p50_ship_h, '.1f')} h",
            delta(cur.p50_ship_h, pv("p50_ship_h")), delta_color="inverse")
c[2].metric("Median days to deliver", fmt(cur.p50_deliver_d, ".1f"),
            delta(cur.p50_deliver_d, pv("p50_deliver_d")), delta_color="inverse")
c[3].metric("Delivered within 3 days", fmt(cur.within_3_days, ".1%"),
            delta(cur.within_3_days, pv("within_3_days"), points=True))
c[4].metric("Cancellation rate", fmt(cur.cancel_rate, ".1%"),
            delta(cur.cancel_rate, pv("cancel_rate"), points=True), delta_color="inverse")
explain("fulfilment KPIs", f"""
The order journey is **placed → paid → shipped → delivered**. Each step's duration is measured from the
order's status-change history, which exists only because CDC captured every status update.

- **Median** is used instead of average, because a few very late parcels would distort an average.
- **Delivered within 3 days** is an example **SLA** (service-level agreement): the promise made to customers.
  Here {fmt(cur.within_3_days, '.0%')} of delivered orders met it.
- The slowest 10% of deliveries take **{fmt(cur.p90_deliver_d, '.1f')} days** or more (the "p90"). Operations teams
  watch p90 closely: the unhappy customers who write complaints live in that tail.
- For these, a **red** arrow means slower (worse) than the previous period.
""")

st.divider()

# ---------------------------------------------------------------- funnel
st.subheader("Order lifecycle funnel")
f = query("""
    SELECT
      count(*)                                                                  AS placed,
      count(*) FILTER (WHERE paid_at IS NOT NULL OR current_status IN ('paid','shipped','delivered')) AS paid,
      count(*) FILTER (WHERE shipped_at IS NOT NULL OR current_status IN ('shipped','delivered'))     AS shipped,
      count(*) FILTER (WHERE current_status = 'delivered')                     AS delivered,
      count(*) FILTER (WHERE is_cancelled AND paid_at IS NULL)                  AS cancelled_before_payment,
      count(*) FILTER (WHERE is_cancelled AND paid_at IS NOT NULL)              AS cancelled_after_payment
    FROM marts.fct_orders
    WHERE (placed_at AT TIME ZONE 'Europe/Helsinki')::date BETWEEN %s AND %s
""", (p["start"], p["end"])).iloc[0]
funnel = pd.DataFrame({"stage": ["Placed", "Paid", "Shipped", "Delivered"],
                       "orders": [f.placed, f.paid, f.shipped, f.delivered]})
funnel["% of placed"] = funnel.orders / max(f.placed, 1)
left, right = st.columns([3, 2])
with left:
    st.altair_chart(
        alt.Chart(funnel).mark_bar().encode(
            x=alt.X("orders:Q", title="Orders"), y=alt.Y("stage:N", sort=None, title=None),
            tooltip=["stage", "orders", alt.Tooltip("% of placed:Q", format=".1%")]),
        use_container_width=True)
with right:
    st.metric("Cancelled before payment", int(f.cancelled_before_payment))
    st.metric("Cancelled after payment", int(f.cancelled_after_payment))
explain("the funnel", """
Each bar shows how many orders reached that stage. The gaps explain lost revenue:
- **Placed → paid**: failed or abandoned payments. A sudden drop here often means a payment provider outage.
- **Paid → shipped**: cancellations after payment cost the most (refund fees, wasted picking time).
- **Shipped → delivered**: recent orders still in transit, which is normal for the last few days of the period.
""")

st.divider()

# ---------------------------------------------------------------- by city
st.subheader("Delivery time by city")
city = query("""
    SELECT city_at_order AS city, count(*) AS delivered_orders,
           (percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_in_transit) / 24)::float AS median_days_in_transit,
           (percentile_cont(0.9) WITHIN GROUP (ORDER BY hours_in_transit) / 24)::float AS p90_days_in_transit
    FROM marts.fct_orders
    WHERE delivered_at IS NOT NULL
      AND (placed_at AT TIME ZONE 'Europe/Helsinki')::date BETWEEN %s AND %s
    GROUP BY 1 ORDER BY 3
""", (p["start"], p["end"]))
long = city.melt(["city", "delivered_orders"], ["median_days_in_transit", "p90_days_in_transit"],
                 var_name="measure", value_name="days")
st.altair_chart(
    alt.Chart(long).mark_bar().encode(
        x=alt.X("city:N", sort=list(city.city), title=None),
        xOffset="measure:N", y=alt.Y("days:Q", title="Days in transit"),
        color=alt.Color("measure:N", title=None),
        tooltip=["city", "measure", alt.Tooltip("days:Q", format=".1f"), "delivered_orders"]),
    use_container_width=True)
if not city.empty:
    fast, slow = city.iloc[0], city.iloc[-1]
    explain("delivery by city", f"""
Transit time grows with distance from the warehouse: **{fast.city}** gets parcels in about
**{fast.median_days_in_transit:.1f} days**, **{slow.city}** in **{slow.median_days_in_transit:.1f} days**.
Companies use this view to set **honest delivery promises per region**, pick carriers,
and decide where a second warehouse would pay off. The city used is where the customer lived
**when they ordered** (SCD Type 2), which is the address the parcel actually went to.
""")

# ---------------------------------------------------------------- weekly trend
st.subheader("Fulfilment speed over time")
weekly = query("""
    SELECT date_trunc('week', placed_at AT TIME ZONE 'Europe/Helsinki')::date AS week,
           (percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_to_ship))::float AS "Hours to ship (median)",
           (percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_to_deliver) / 24)::float AS "Days to deliver (median)",
           count(*) AS orders
    FROM marts.fct_orders
    WHERE delivered_at IS NOT NULL
    GROUP BY 1 ORDER BY 1
""")
st.altair_chart(
    alt.Chart(weekly.melt(["week", "orders"], var_name="measure", value_name="value"))
    .mark_line(point=False).encode(
        x=alt.X("week:T", title=None), y=alt.Y("value:Q", title=None),
        color=alt.Color("measure:N", title=None),
        tooltip=["week:T", "measure", alt.Tooltip("value:Q", format=".1f"), "orders"]),
    use_container_width=True)
explain("speed over time", """
Watch how shipping slows down when volume peaks (Black Friday, the December rush): the warehouse
has the same people but far more parcels. This is how operations teams justify **seasonal staff**
and plan capacity months ahead.
""")

# ---------------------------------------------------------------- backlog
st.subheader("Open orders right now (backlog)")
backlog = query("""
    WITH latest AS (SELECT max(placed_at) AS now_ts FROM marts.fct_orders)
    SELECT current_status AS status,
           CASE WHEN latest.now_ts - placed_at < interval '1 day'  THEN '< 1 day'
                WHEN latest.now_ts - placed_at < interval '3 days' THEN '1-3 days'
                WHEN latest.now_ts - placed_at < interval '7 days' THEN '3-7 days'
                ELSE '> 7 days' END AS age,
           count(*) AS orders
    FROM marts.fct_orders CROSS JOIN latest
    WHERE current_status IN ('placed', 'paid', 'shipped')
    GROUP BY 1, 2
""")
st.altair_chart(
    alt.Chart(backlog).mark_bar().encode(
        x=alt.X("status:N", sort=["placed", "paid", "shipped"], title=None),
        y=alt.Y("orders:Q", title="Open orders"),
        color=alt.Color("age:N", sort=["< 1 day", "1-3 days", "3-7 days", "> 7 days"],
                        scale=alt.Scale(scheme="orangered"), title="Age"),
        tooltip=["status", "age", "orders"]),
    use_container_width=True)
explain("the backlog", """
Orders that are not finished yet, grouped by how long they've been waiting. Young orders are normal.
**Old orders stuck in 'placed' or 'paid'** are the alarm bell: a payment problem, an item out of stock,
or a parcel the warehouse forgot. Teams review this list daily so no customer is silently left waiting.
(Orders from the live generator move through statuses randomly, so some of them can look stuck.)
""")
