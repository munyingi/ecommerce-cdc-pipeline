"""Products: what sells, how concentrated sales are, and how demand reacts to price."""
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))  # find db.py
from db import eur, explain, page_setup, period_filter, query

page_setup("Products",
           "Which products drive the business, and how does demand react to price changes?")
p = period_filter()

perf = query("""
    SELECT product_id, product_name, category, current_price::float AS current_price,
           min_price::float AS min_price, max_price::float AS max_price, price_changes,
           units, revenue::float AS revenue, orders, revenue_rank,
           cumulative_revenue_share::float AS cumulative_revenue_share, abc_class
    FROM marts.mart_product_performance
    ORDER BY revenue_rank
""")
selling = perf[perf.revenue > 0]
n = len(selling)
top20 = selling.head(max(1, round(n * 0.2))).revenue.sum() / selling.revenue.sum() if n else 0
a_count = (perf.abc_class == "A").sum()

c = st.columns(4)
c[0].metric("Products with sales", f"{n} of {len(perf)}")
c[1].metric("Revenue from top 20% of products", f"{top20:.0%}")
c[2].metric("A-class products (80% of revenue)", f"{a_count}")
c[3].metric("Best seller", selling.iloc[0].product_name if n else "n/a",
            eur(selling.iloc[0].revenue) if n else None, delta_color="off")

st.divider()

# ---------------------------------------------------------------- Pareto
st.subheader("Revenue concentration (Pareto / ABC)")
base = alt.Chart(perf).encode(x=alt.X("revenue_rank:O", title="Products ranked by revenue",
                                      axis=alt.Axis(labels=False, ticks=False)))
bars = base.mark_bar().encode(
    y=alt.Y("revenue:Q", title="Revenue (€)"),
    color=alt.Color("abc_class:N", title="Class",
                    scale=alt.Scale(domain=["A", "B", "C"], range=["#1f77b4", "#7fb3d5", "#d6e4f0"])),
    tooltip=["product_name", "category", "abc_class",
             alt.Tooltip("revenue:Q", format=",.0f"),
             alt.Tooltip("cumulative_revenue_share:Q", format=".0%", title="Cumulative share")])
line = base.mark_line(color="#d62728", strokeWidth=2).encode(
    y=alt.Y("cumulative_revenue_share:Q", title="Cumulative share",
            axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])))
st.altair_chart(alt.layer(bars, line).resolve_scale(y="independent"), use_container_width=True)
explain("the Pareto chart", f"""
Products are sorted from best to worst seller. The red line is the **cumulative share of revenue**.
Here the top 20% of products bring **{top20:.0%}** of revenue: the classic "80/20" pattern found in
almost every retailer.

The **ABC classes** turn this into decisions:
- **A** (first 80% of revenue, {a_count} products): never let them go out of stock; negotiate hardest with their suppliers.
- **B** (next 15%): manage normally.
- **C** (last 5%): candidates for discontinuing, bundling, or clearance. They take warehouse space and admin time but earn little.
""")

# ---------------------------------------------------------------- categories
st.subheader("Category trends")
cat = query("""
    SELECT d.month_start, p.category, sum(f.line_amount)::float AS revenue
    FROM marts.fct_order_items f
    JOIN marts.dim_product p ON p.product_sk = f.product_sk
    JOIN marts.dim_date d    ON d.date_key   = f.date_key
    WHERE NOT f.is_cancelled
    GROUP BY 1, 2 ORDER BY 1
""")
left, right = st.columns([3, 2])
with left:
    st.altair_chart(
        alt.Chart(cat).mark_area().encode(
            x=alt.X("month_start:T", timeUnit="yearmonth", title=None),
            y=alt.Y("revenue:Q", stack="normalize", title="Share of revenue",
                    axis=alt.Axis(format="%")),
            color="category:N",
            tooltip=[alt.Tooltip("month_start:T", timeUnit="yearmonth"), "category",
                     alt.Tooltip("revenue:Q", format=",.0f")]),
        use_container_width=True)
with right:
    period_cat = query("""
        SELECT p.category, sum(f.line_amount)::float AS revenue, sum(f.quantity)::int AS units,
               (sum(f.line_amount) / nullif(sum(f.quantity), 0))::float AS avg_unit_price
        FROM marts.fct_order_items f
        JOIN marts.dim_product p ON p.product_sk = f.product_sk
        WHERE NOT f.is_cancelled
          AND (f.order_ts AT TIME ZONE 'Europe/Helsinki')::date BETWEEN %s AND %s
        GROUP BY 1 ORDER BY 2 DESC
    """, (p["start"], p["end"]))
    st.dataframe(period_cat, hide_index=True, use_container_width=True,
                 column_config={"revenue": st.column_config.NumberColumn("Revenue €", format="%.0f"),
                                "avg_unit_price": st.column_config.NumberColumn("Avg unit price €",
                                                                               format="%.2f")})
explain("category mix", """
The left chart shows each category's **share** of monthly revenue, so you can see the mix shift
even when total sales grow. Seasonal products show up clearly (e.g. sports gear around summer,
electronics before Christmas). A category gaining share is where to expand the range; one losing share
needs a pricing or assortment review. The table shows the selected period with average selling price,
which separates "sells many cheap items" from "sells few expensive ones".
""")

st.divider()

# ---------------------------------------------------------------- price elasticity
st.subheader("Does demand react to price?")
changed = perf[perf.price_changes > 0]
if changed.empty:
    st.write("No price changes recorded yet.")
else:
    labels = {f"{r.product_name} (#{r.product_id}, {r.price_changes} changes)": r.product_id
              for r in changed.sort_values("revenue", ascending=False).itertuples()}
    choice = st.selectbox("Product", list(labels))
    versions = query("""
        SELECT version_number, price::float AS price,
               valid_from AT TIME ZONE 'Europe/Helsinki' AS valid_from,
               days_active::float AS days_active, units, units_per_day::float AS units_per_day
        FROM marts.mart_product_price_versions
        WHERE product_id = %s ORDER BY version_number
    """, (labels[choice],))
    versions["label"] = versions.apply(lambda r: f"v{r.version_number}: €{r.price:.2f}", axis=1)
    left, right = st.columns(2)
    with left:
        st.altair_chart(
            alt.Chart(versions).mark_line(interpolate="step-after", point=True).encode(
                x=alt.X("valid_from:T", title=None), y=alt.Y("price:Q", title="Price (€)"),
                tooltip=["label", "valid_from:T"]),
            use_container_width=True)
    with right:
        st.altair_chart(
            alt.Chart(versions).mark_bar().encode(
                x=alt.X("label:N", sort=None, title="Price version"),
                y=alt.Y("units_per_day:Q", title="Units sold per day"),
                tooltip=["label", alt.Tooltip("units_per_day:Q", format=".2f"),
                         alt.Tooltip("days_active:Q", format=".0f"), "units"]),
            use_container_width=True)

    overall = query("""
        WITH v AS (
            SELECT product_id, price, units_per_day,
                   lag(price)         OVER (PARTITION BY product_id ORDER BY version_number) AS prev_price,
                   lag(units_per_day) OVER (PARTITION BY product_id ORDER BY version_number) AS prev_upd
            FROM marts.mart_product_price_versions
            WHERE days_active >= 3
        )
        SELECT
          avg(units_per_day / nullif(prev_upd, 0) - 1) FILTER (WHERE price < prev_price)::float AS cut_effect,
          avg(units_per_day / nullif(prev_upd, 0) - 1) FILTER (WHERE price > prev_price)::float AS raise_effect,
          count(*) FILTER (WHERE prev_price IS NOT NULL) AS changes
        FROM v
    """).iloc[0]
    explain("price and demand", f"""
Every price change creates a new version of the product (SCD Type 2), so the warehouse knows exactly
which price was live when each unit sold. Comparing **units sold per day** under each price shows
**price elasticity**: how strongly demand reacts to price.

Across all {int(overall.changes or 0)} price changes (versions active for at least 3 days),
demand changed on average by **{(overall.cut_effect or 0):+.0%}** after a price cut and
**{(overall.raise_effect or 0):+.0%}** after a price increase.

Caveat: other things change at the same time (seasonality, Black Friday traffic, overall growth),
so real pricing teams confirm these effects with controlled A/B price tests.
""")

st.divider()

# ---------------------------------------------------------------- baskets
st.subheader("Frequently bought together")
pairs = query("""
    SELECT pa.product_name AS product_a, pb.product_name AS product_b, count(*) AS orders_together
    FROM marts.fct_order_items a
    JOIN marts.fct_order_items b
      ON a.order_id = b.order_id AND a.product_id < b.product_id
    JOIN marts.dim_product pa ON pa.product_sk = a.product_sk
    JOIN marts.dim_product pb ON pb.product_sk = b.product_sk
    WHERE NOT a.is_cancelled
    GROUP BY 1, 2
    ORDER BY 3 DESC
    LIMIT 10
""")
st.dataframe(pairs, hide_index=True, use_container_width=True)
explain("basket analysis", """
Pairs of products that most often appear in the same order. This powers **"customers also bought"**
recommendations, bundle offers and store layout. Raising **units per order** (see the overview page)
by even 0.1 is a large revenue gain for a big retailer, and good recommendations are the main lever.
""")

st.subheader("All products")
st.dataframe(
    perf[["revenue_rank", "product_name", "category", "abc_class", "units", "revenue",
          "current_price", "min_price", "max_price", "price_changes"]],
    hide_index=True, use_container_width=True,
    column_config={"revenue": st.column_config.NumberColumn("Revenue €", format="%.0f"),
                   "current_price": st.column_config.NumberColumn("Price €", format="%.2f"),
                   "min_price": st.column_config.NumberColumn("Lowest €", format="%.2f"),
                   "max_price": st.column_config.NumberColumn("Highest €", format="%.2f")})
