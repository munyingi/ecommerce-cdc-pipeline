"""Customers: acquisition, retention, lifetime value, segments and geography."""
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))  # find db.py
from db import eur, explain, page_setup, period_filter, query

page_setup("Customers",
           "Who buys, do they come back, and how much are they worth over time?")
p = period_filter()

# ---------------------------------------------------------------- KPIs (all time)
k = query("""
    SELECT count(*)                                         AS customers,
           avg(is_repeat_customer::int)::float              AS repeat_rate,
           avg(orders)::float                               AS orders_per_customer,
           avg(revenue)::float                              AS revenue_per_customer,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY revenue)::float AS median_revenue
    FROM marts.mart_customer_metrics
""").iloc[0]
gap = query("""
    WITH o AS (
        SELECT customer_id, placed_at,
               lag(placed_at) OVER (PARTITION BY customer_id ORDER BY placed_at) AS prev
        FROM marts.fct_orders WHERE NOT is_cancelled
    )
    SELECT percentile_cont(0.5) WITHIN GROUP (
               ORDER BY extract(epoch FROM placed_at - prev) / 86400)::float AS median_days
    FROM o WHERE prev IS NOT NULL
""").iloc[0].median_days

c = st.columns(5)
c[0].metric("Customers who ordered", f"{int(k.customers):,}")
c[1].metric("Repeat customer rate", f"{k.repeat_rate:.1%}")
c[2].metric("Orders per customer", f"{k.orders_per_customer:.2f}")
c[3].metric("Average lifetime revenue", eur(k.revenue_per_customer))
c[4].metric("Median days between orders", f"{gap:.0f}" if gap else "n/a")
explain("customer KPIs", f"""
- **Repeat customer rate**: share of customers who ordered more than once. Here **{k.repeat_rate:.0%}**
  came back at least once; the other {1 - k.repeat_rate:.0%} bought once and left. Raising this
  is usually the cheapest growth lever, because keeping a customer costs far less than finding a new one.
- **Average lifetime revenue** ({eur(k.revenue_per_customer)}) vs **median** ({eur(k.median_revenue)}):
  the average is pulled up by a small group of big spenders. That gap is why companies segment
  customers instead of treating them all the same.
- **Median days between orders** ({gap:.0f} days): the natural buying rhythm. A customer silent for
  much longer than this is drifting away, which is when win-back emails are sent.
""")

st.divider()

# ---------------------------------------------------------------- new vs returning
st.subheader("Revenue from new vs returning customers")
nvr = query("""
    SELECT month_start,
           sum(new_customer_revenue)::float       AS "New customers",
           sum(returning_customer_revenue)::float AS "Returning customers"
    FROM marts.mart_daily_kpis
    GROUP BY 1 ORDER BY 1
""").melt("month_start", var_name="Customer type", value_name="revenue")
st.altair_chart(
    alt.Chart(nvr).mark_bar().encode(
        x=alt.X("month_start:T", timeUnit="yearmonth", title=None),
        y=alt.Y("revenue:Q", title="Revenue (€)"),
        color=alt.Color("Customer type:N"),
        tooltip=[alt.Tooltip("month_start:T", timeUnit="yearmonth", title="Month"),
                 "Customer type", alt.Tooltip("revenue:Q", format=",.0f")]),
    use_container_width=True)
explain("new vs returning revenue", """
A young shop lives on **new** customers; as it matures, the **returning** share should grow
month after month. If the returning slice stays flat while marketing spend rises, the business
is "renting" customers: paying to acquire people who never come back.
""")

# ---------------------------------------------------------------- cohorts
st.subheader("Cohort retention")
cohorts = query("""
    SELECT cohort_month, months_since_first_order, cohort_customers,
           retention_rate::float AS retention_rate,
           revenue_per_customer::float AS revenue_per_customer
    FROM marts.mart_cohort_retention
    WHERE months_since_first_order <= 12
    ORDER BY cohort_month, months_since_first_order
""")
cohorts["cohort"] = pd.to_datetime(cohorts.cohort_month).dt.strftime("%Y-%m")
heat = cohorts[cohorts.months_since_first_order > 0]
base = alt.Chart(heat).encode(
    x=alt.X("months_since_first_order:O", title="Months since first order"),
    y=alt.Y("cohort:O", title="First-order month (cohort)"))
st.altair_chart(
    base.mark_rect().encode(
        color=alt.Color("retention_rate:Q", scale=alt.Scale(scheme="greens"),
                        title="Retention", legend=alt.Legend(format=".0%")),
        tooltip=["cohort", "months_since_first_order", "cohort_customers",
                 alt.Tooltip("retention_rate:Q", format=".1%")])
    + base.mark_text(fontSize=10).encode(
        text=alt.Text("retention_rate:Q", format=".0%"),
        color=alt.condition("datum.retention_rate > 0.15", alt.value("white"), alt.value("black"))),
    use_container_width=True)
m1 = heat[heat.months_since_first_order == 1].retention_rate.mean()
m6 = heat[heat.months_since_first_order == 6].retention_rate.mean()
explain("the cohort table", f"""
Each row is a **cohort**: all customers whose first order was in that month. Each column shows what
share of them ordered again *N months later*. Read a row left to right to see one group fading out;
read a column top to bottom to see whether newer customers are stickier than older ones.

In this data, on average **{m1:.0%}** of customers order again in month 1 and **{m6:.0%}** in month 6.
That steep early drop is normal in e-commerce: the first weeks after the first purchase decide whether
someone becomes a regular, which is why onboarding emails and second-order discounts exist.
""")

st.subheader("Cumulative revenue per customer (lifetime value curve)")
ltv = cohorts.copy()
ltv["cumulative_revenue_per_customer"] = ltv.groupby("cohort").revenue_per_customer.cumsum()
recent = sorted(ltv.cohort.unique())[-12:]
st.altair_chart(
    alt.Chart(ltv[ltv.cohort.isin(recent)]).mark_line(point=True).encode(
        x=alt.X("months_since_first_order:Q", title="Months since first order"),
        y=alt.Y("cumulative_revenue_per_customer:Q", title="Revenue per customer (€)"),
        color=alt.Color("cohort:N", title="Cohort"),
        tooltip=["cohort", "months_since_first_order",
                 alt.Tooltip("cumulative_revenue_per_customer:Q", format=",.2f")]),
    use_container_width=True)
explain("the lifetime value curve", """
How much an average customer from each cohort has spent in total, N months after joining.
This is **customer lifetime value (LTV)** as it builds up. Companies compare it with **customer
acquisition cost (CAC)**: if acquiring a customer costs €40 and the curve passes €40 after 3 months,
marketing pays for itself in 3 months. (Marketing spend isn't in this dataset, so CAC is not shown.)
""")

st.divider()

# ---------------------------------------------------------------- RFM
st.subheader("Customer segments (RFM)")
seg = query("""
    SELECT rfm_segment AS segment, count(*) AS customers,
           sum(revenue)::float AS revenue,
           avg(days_since_last_order)::float AS avg_days_since_last_order,
           avg(orders)::float AS avg_orders
    FROM marts.mart_customer_metrics
    GROUP BY 1 ORDER BY revenue DESC
""")
seg["share_of_customers"] = seg.customers / seg.customers.sum()
seg["share_of_revenue"] = seg.revenue / seg.revenue.sum()
actions = {
    "Champions": "Reward them: early access, loyalty perks, ask for reviews and referrals.",
    "Loyal": "Upsell and cross-sell; invite to the loyalty programme.",
    "Promising": "Second-to-third order nudges, personalised recommendations.",
    "New": "Onboarding series; make the second order easy.",
    "Needs attention": "Time-limited offer before they slip away.",
    "At risk": "Win-back campaign now: they used to buy often but have gone quiet.",
    "Hibernating": "Low-cost reactivation only; don't overspend on them.",
}
seg["suggested action"] = seg.segment.map(actions)
left, right = st.columns([2, 3])
with left:
    st.altair_chart(
        alt.Chart(seg).mark_bar().encode(
            x=alt.X("share_of_revenue:Q", title="Share of revenue", axis=alt.Axis(format="%")),
            y=alt.Y("segment:N", sort="-x", title=None),
            tooltip=["segment", "customers",
                     alt.Tooltip("share_of_customers:Q", format=".1%"),
                     alt.Tooltip("share_of_revenue:Q", format=".1%")]),
        use_container_width=True)
with right:
    st.dataframe(
        seg[["segment", "customers", "share_of_customers", "share_of_revenue",
             "avg_days_since_last_order", "suggested action"]],
        hide_index=True, use_container_width=True,
        column_config={
            "share_of_customers": st.column_config.NumberColumn("% customers", format="percent"),
            "share_of_revenue": st.column_config.NumberColumn("% revenue", format="percent"),
            "avg_days_since_last_order": st.column_config.NumberColumn("Days since last order",
                                                                       format="%.0f"),
        })
champ = seg.set_index("segment")
explain("RFM segmentation", f"""
**RFM** scores every customer on **R**ecency (how recently they bought), **F**requency (how often)
and **M**onetary value (how much). Combining the scores gives segments that marketing can act on,
each with a different message and budget (see the "suggested action" column).

Notice the imbalance: **Champions** are
{champ.loc['Champions', 'share_of_customers'] if 'Champions' in champ.index else 0:.0%} of customers
but bring {champ.loc['Champions', 'share_of_revenue'] if 'Champions' in champ.index else 0:.0%} of revenue.
Losing one Champion hurts as much as losing many one-time buyers.
""")

st.divider()

# ---------------------------------------------------------------- geography + SCD2
st.subheader("Where revenue comes from")
city = query("""
    WITH lines AS (
        SELECT f.order_amount, f.city_at_order AS city_then, cur.city AS city_now
        FROM marts.fct_orders f
        JOIN marts.dim_customer cur
          ON cur.customer_id = f.customer_id AND cur.is_current
        WHERE NOT f.is_cancelled
          AND (f.placed_at AT TIME ZONE 'Europe/Helsinki')::date BETWEEN %s AND %s
    )
    SELECT city,
           sum(at_order)::float AS "City at order time (SCD2)",
           sum(current)::float  AS "Current city (overwritten)"
    FROM (SELECT city_then AS city, order_amount AS at_order, 0 AS current FROM lines
          UNION ALL
          SELECT city_now, 0, order_amount FROM lines) t
    GROUP BY city ORDER BY 2 DESC
""", (p["start"], p["end"]))
long = city.melt("city", var_name="Method", value_name="revenue")
st.altair_chart(
    alt.Chart(long).mark_bar().encode(
        x=alt.X("city:N", sort="-y", title=None),
        xOffset="Method:N",
        y=alt.Y("revenue:Q", title="Revenue (€)"),
        color="Method:N",
        tooltip=["city", "Method", alt.Tooltip("revenue:Q", format=",.0f")]),
    use_container_width=True)
moved = query("""
    SELECT coalesce(sum(f.order_amount) FILTER (WHERE f.city_at_order <> cur.city), 0)::float
           / nullif(sum(f.order_amount), 0)::float AS share
    FROM marts.fct_orders f
    JOIN marts.dim_customer cur ON cur.customer_id = f.customer_id AND cur.is_current
    WHERE NOT f.is_cancelled
      AND (f.placed_at AT TIME ZONE 'Europe/Helsinki')::date BETWEEN %s AND %s
""", (p["start"], p["end"])).iloc[0].share or 0
explain("why the two bars differ (SCD Type 2)", f"""
When a customer moves, a simple database **overwrites** their city. Every past order then looks as
if it came from the new city, and regional reports silently change history.

This warehouse keeps **every version** of each customer with a validity window (SCD Type 2), and each
order is linked to the version valid **when the order was placed**. In this period
**{moved:.1%}** of revenue would be credited to the wrong city without it. For regional
budgets, delivery planning or tax reporting, that error matters.
""")

st.subheader("Look up a customer's history")
cid = st.number_input("Customer ID", min_value=1, value=20, step=1)
st.dataframe(query("""
    SELECT version_number, first_name, last_name, city, is_deleted,
           valid_from AT TIME ZONE 'Europe/Helsinki' AS valid_from,
           CASE WHEN is_current THEN NULL
                ELSE valid_to AT TIME ZONE 'Europe/Helsinki' END AS valid_to,
           is_current
    FROM marts.dim_customer WHERE customer_id = %s ORDER BY version_number
""", (int(cid),)), hide_index=True, use_container_width=True)
