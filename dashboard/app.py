"""
Executive overview: the numbers a leadership team opens first every morning.
Run with:  python -m streamlit run dashboard/app.py
"""
import sys
from pathlib import Path

import altair as alt
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent))  # find db.py
from db import delta, eur, explain, page_setup, pct_change, period_filter, query

page_setup("Executive overview",
           "Is the business growing, and where is the growth coming from? "
           "Built on a CDC pipeline: Postgres → Debezium → Kafka → warehouse → dbt.")
p = period_filter()

KPI_SQL = """
    SELECT
        coalesce(sum(revenue), 0)::float                    AS revenue,
        coalesce(sum(orders), 0)::int                       AS orders,
        coalesce(sum(orders_placed), 0)::int                AS orders_placed,
        coalesce(sum(cancelled_orders), 0)::int             AS cancelled,
        coalesce(sum(units), 0)::int                        AS units,
        coalesce(sum(new_customers), 0)::int                AS new_customers,
        coalesce(sum(returning_customer_revenue), 0)::float AS returning_revenue
    FROM marts.mart_daily_kpis
    WHERE date_day BETWEEN %s AND %s
"""
ACTIVE_SQL = """
    SELECT count(DISTINCT customer_id) AS active
    FROM marts.fct_orders
    WHERE NOT is_cancelled
      AND (placed_at AT TIME ZONE 'Europe/Helsinki')::date BETWEEN %s AND %s
"""


def kpis(start, end):
    k = query(KPI_SQL, (start, end)).iloc[0].to_dict()
    for col in ("orders", "orders_placed", "cancelled", "units", "new_customers"):
        k[col] = int(k[col])
    k["active"] = int(query(ACTIVE_SQL, (start, end)).iloc[0].active)
    k["aov"] = k["revenue"] / k["orders"] if k["orders"] else 0
    k["units_per_order"] = k["units"] / k["orders"] if k["orders"] else 0
    k["cancel_rate"] = k["cancelled"] / k["orders_placed"] if k["orders_placed"] else 0
    k["returning_share"] = k["returning_revenue"] / k["revenue"] if k["revenue"] else 0
    k["orders_per_customer"] = k["orders"] / k["active"] if k["active"] else 0
    return k


cur = kpis(p["start"], p["end"])
prev = kpis(p["prev_start"], p["prev_end"]) if p["label"] != "All time" else {}
g = prev.get

# ---------------------------------------------------------------- KPI tiles
st.subheader("Headline KPIs")
r1 = st.columns(4)
r1[0].metric("Net revenue", eur(cur["revenue"]), delta(cur["revenue"], g("revenue")))
r1[1].metric("Orders", f"{cur['orders']:,}", delta(cur["orders"], g("orders")))
r1[2].metric("Average order value", eur(cur["aov"]), delta(cur["aov"], g("aov")))
r1[3].metric("Active customers", f"{cur['active']:,}", delta(cur["active"], g("active")))
r2 = st.columns(4)
r2[0].metric("New customers", f"{cur['new_customers']:,}",
             delta(cur["new_customers"], g("new_customers")))
r2[1].metric("Revenue from returning customers", f"{cur['returning_share']:.1%}",
             delta(cur["returning_share"], g("returning_share"), points=True))
r2[2].metric("Units per order", f"{cur['units_per_order']:.2f}",
             delta(cur["units_per_order"], g("units_per_order")))
r2[3].metric("Cancellation rate", f"{cur['cancel_rate']:.1%}",
             delta(cur["cancel_rate"], g("cancel_rate"), points=True), delta_color="inverse")

explain("the headline KPIs", """
- **Net revenue**: value of all orders that were not cancelled (often called GMV net of cancellations).
  It is the top-line number: is the shop selling more than before?
- **Orders** and **average order value (AOV)**: revenue = orders × AOV. If revenue moves,
  these two tell you *why*: more shoppers checking out, or bigger baskets.
- **Active customers**: distinct customers with at least one non-cancelled order in the period.
- **New customers**: customers whose *first ever* order falls in the period. Acquisition health.
- **Revenue from returning customers**: share of revenue from people who had ordered before.
  Mature e-commerce businesses live on repeat buyers, because acquiring customers is expensive.
- **Units per order**: basket depth. Cross-selling and bundles push this up.
- **Cancellation rate**: cancelled ÷ all placed orders. Red arrow = getting worse.

Arrows compare against the **previous period of the same length** (e.g. last 90 days vs the 90 before).
""")

# ---------------------------------------------------------------- automatic insights
daily = query("""
    SELECT date_day, revenue::float AS revenue, orders, new_customers
    FROM marts.mart_daily_kpis
    WHERE date_day BETWEEN %s AND %s
    ORDER BY date_day
""", (p["start"], p["end"]))
monthly = query("""
    SELECT month_start, sum(revenue)::float AS revenue, sum(orders)::int AS orders,
           count(*) AS days_in_data
    FROM marts.mart_daily_kpis
    GROUP BY month_start ORDER BY month_start
""")
weekday = query("""
    SELECT day_of_week, day_name, avg(revenue)::float AS avg_revenue
    FROM marts.mart_daily_kpis
    WHERE date_day BETWEEN %s AND %s
    GROUP BY 1, 2 ORDER BY 1
""", (p["start"], p["end"]))

insights = []
change = pct_change(cur["revenue"], g("revenue"))
if change is not None:
    driver = ("more orders" if abs(pct_change(cur["orders"], g("orders")) or 0)
              >= abs(pct_change(cur["aov"], g("aov")) or 0) else "bigger baskets")
    insights.append(f"Revenue is **{'up' if change >= 0 else 'down'} {abs(change):.1%}** "
                    f"vs the previous {p['days']} days, driven mainly by **{driver}**.")
history = daily.iloc[:-1]   # the latest day is still in progress (and holds the live generator's burst)
if not history.empty:
    best = history.loc[history.revenue.idxmax()]
    typical = history.revenue.median()
    insights.append(f"Best full day: **{best.date_day:%a %d %b %Y}** with {eur(best.revenue)}, "
                    f"**{best.revenue / typical:.1f}×** a typical day "
                    f"({eur(typical)}). Spikes like this are usually campaigns such as Black Friday.")
if len(daily) > 1:
    today = daily.iloc[-1]
    insights.append(f"Today so far ({today.date_day:%d %b}): {eur(today.revenue)} from "
                    f"{int(today.orders)} orders. This includes the **live generator**, which places "
                    f"orders much faster than the simulated history, so treat today as a demo burst.")
if not weekday.empty:
    top = weekday.loc[weekday.avg_revenue.idxmax()]
    low = weekday.loc[weekday.avg_revenue.idxmin()]
    insights.append(f"**{top.day_name}s** are the strongest day on average, "
                    f"**{low.day_name}s** the weakest "
                    f"({top.avg_revenue / low.avg_revenue - 1:.0%} difference).")
full_months = monthly[monthly.days_in_data >= 28]
if len(full_months) >= 2:
    a, b = full_months.iloc[-2], full_months.iloc[-1]
    insights.append(f"Last full month ({b.month_start:%B}) revenue was {eur(b.revenue)}, "
                    f"{pct_change(b.revenue, a.revenue):+.1%} vs {a.month_start:%B}.")
st.info("**What's happening**\n\n" + "\n\n".join(f"- {i}" for i in insights))

st.divider()

# ---------------------------------------------------------------- revenue trend
left, right = st.columns([3, 2])
with left:
    st.subheader("Daily revenue")
    daily["7-day average"] = daily.revenue.rolling(7, min_periods=1).mean()
    base = alt.Chart(daily).encode(x=alt.X("date_day:T", title=None))
    chart = (base.mark_bar(opacity=0.35).encode(
                 y=alt.Y("revenue:Q", title="Revenue (€)"),
                 tooltip=[alt.Tooltip("date_day:T", title="Day"),
                          alt.Tooltip("revenue:Q", format=",.0f", title="Revenue €"),
                          alt.Tooltip("orders:Q", title="Orders")])
             + base.mark_line(strokeWidth=2.5).encode(y="7-day average:Q"))
    st.altair_chart(chart, use_container_width=True)
    explain("daily revenue", """
Bars are single days, the line is the **7-day moving average**. Daily numbers are noisy
(weekends, paydays, random big orders); the moving average shows the underlying direction.
Look for: steady upward slope (growth), sharp one-off spikes (campaigns, Black Friday),
and dips (holidays, outages, stock problems).
""")

with right:
    st.subheader("Revenue by month")
    monthly["growth"] = monthly.revenue.pct_change()
    bars = alt.Chart(monthly).mark_bar().encode(
        x=alt.X("month_start:T", timeUnit="yearmonth", title=None),
        y=alt.Y("revenue:Q", title="Revenue (€)"),
        tooltip=[alt.Tooltip("month_start:T", timeUnit="yearmonth", title="Month"),
                 alt.Tooltip("revenue:Q", format=",.0f", title="Revenue €"),
                 alt.Tooltip("growth:Q", format="+.1%", title="Month-over-month"),
                 alt.Tooltip("days_in_data:Q", title="Days with data")])
    st.altair_chart(bars, use_container_width=True)
    explain("monthly revenue", """
Month-over-month (MoM) growth is how leadership tracks momentum. Hover a bar to see MoM %.
Watch out for **partial months**: the first and the current month contain fewer days,
so compare full months only. Seasonality matters too: December is usually the peak and
January the hangover, so many companies also compare to the **same month last year**.
""")

# ---------------------------------------------------------------- weekly rhythm
st.subheader("When do customers buy?")
heat = query("""
    SELECT extract(isodow FROM placed_at AT TIME ZONE 'Europe/Helsinki')::int AS dow,
           to_char(placed_at AT TIME ZONE 'Europe/Helsinki', 'Dy')           AS day,
           extract(hour  FROM placed_at AT TIME ZONE 'Europe/Helsinki')::int AS hour,
           count(*) AS orders
    FROM marts.fct_orders
    WHERE (placed_at AT TIME ZONE 'Europe/Helsinki')::date BETWEEN %s AND %s
    GROUP BY 1, 2, 3
""", (p["start"], p["end"]))
st.altair_chart(
    alt.Chart(heat).mark_rect().encode(
        x=alt.X("hour:O", title="Hour of day (Helsinki)"),
        y=alt.Y("day:O", sort=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], title=None),
        color=alt.Color("orders:Q", scale=alt.Scale(scheme="blues"), title="Orders"),
        tooltip=["day", "hour", "orders"]),
    use_container_width=True)
explain("the order heatmap", """
Each cell is the number of orders placed in that weekday and hour. Companies use this to
schedule **email and ad campaigns** (send just before the evening peak), **customer service
staffing**, and **maintenance windows** (deploy when the map is palest, usually at night).
""")
