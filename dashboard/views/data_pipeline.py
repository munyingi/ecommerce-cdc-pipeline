"""Data pipeline: is the data fresh, complete and trustworthy?"""
import sys
from pathlib import Path

import altair as alt
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))  # find db.py
from db import explain, page_setup, query, snapshot_time

page_setup("Data pipeline",
           "Can the numbers on the other pages be trusted? Freshness, volume and data quality.")

st.graphviz_chart("""
digraph {
  rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef3fb", fontname="Helvetica"];
  app [label="Shop app\\n(generator)"]; src [label="Postgres\\nsource DB"];
  dbz [label="Debezium\\n(Kafka Connect)"]; k [label="Kafka\\n4 topics"];
  ld [label="Python loader\\n(idempotent)"]; raw [label="Warehouse\\nraw.cdc_events"];
  dbt [label="dbt\\nstaging → SCD2 dims\\n→ facts → marts"]; ui [label="This dashboard"];
  app -> src -> dbz -> k -> ld -> raw -> dbt -> ui;
  src -> dbz [label=" WAL"];
}
""")

h = query("""
    SELECT
      count(*)                                                        AS events,
      count(*) FILTER (WHERE loaded_at > now() - interval '1 hour')   AS events_last_hour,
      extract(epoch FROM now() - max(loaded_at))::int                 AS seconds_since_last_load,
      (avg(extract(epoch FROM loaded_at - to_timestamp(source_ts_ms / 1000.0)))
         FILTER (WHERE loaded_at > now() - interval '15 minutes' AND NOT is_snapshot))::float
                                                                      AS avg_latency_s
    FROM raw.cdc_events
""").iloc[0]
lag_s = int(h.seconds_since_last_load)
latency = None if h.avg_latency_s is None or h.avg_latency_s != h.avg_latency_s else max(0.0, h.avg_latency_s)
status = ("🟢 Fresh" if lag_s < 120
          else "🟡 Delayed" if lag_s < 3600 else "🔴 Stale")
c = st.columns(4)
snap = snapshot_time()
if snap is None:
    c[0].metric("Freshness", status, f"last event {lag_s:,} s ago", delta_color="off")
else:
    c[0].metric("Snapshot", f"{snap:%d %b %Y}", "online demo data", delta_color="off")
c[1].metric("End-to-end CDC latency",
            f"{latency:.1f} s" if latency is not None else "idle")
c[2].metric("Events in the last hour", f"{int(h.events_last_hour):,}")
c[3].metric("Total change events", f"{int(h.events):,}")
explain("freshness and latency", """
- **Freshness**: how long since the last change event landed in the warehouse. If the generator is running
  and this turns yellow or red, something upstream is broken (Debezium stopped, Kafka down, loader crashed).
  dbt checks the same thing with `dbt source freshness`.
- **End-to-end latency**: average time from the moment Postgres **committed** a change to the moment it was
  **stored in the warehouse** (last 15 minutes). A few seconds means near-real-time CDC. "idle" means
  nothing changed recently.
- Note the marts on other pages update when `dbt build` runs; the raw layer updates continuously.
""")

st.divider()

st.subheader("Change events by table and operation")
ops = query("""
    SELECT table_name,
           count(*) FILTER (WHERE op = 'r') AS snapshot_reads,
           count(*) FILTER (WHERE op = 'c') AS inserts,
           count(*) FILTER (WHERE op = 'u') AS updates,
           count(*) FILTER (WHERE op = 'd') AS deletes,
           count(*) AS total,
           max(loaded_at) AT TIME ZONE 'Europe/Helsinki' AS last_loaded
    FROM raw.cdc_events GROUP BY 1 ORDER BY 1
""")
st.dataframe(ops, hide_index=True, use_container_width=True)
explain("operation types", """
Debezium labels every event: **r** = row read during the initial snapshot, **c** = insert,
**u** = update, **d** = delete. The many **updates on orders** are status changes (placed → paid → shipped →
delivered). Updates on customers and products are moves and price changes: the raw material for the
SCD Type 2 history. A batch tool that copies tables once a night would see only the final state and miss
all the steps in between.
""")

st.subheader("Load volume over time")
vol = query("""
    SELECT date_trunc('minute', loaded_at AT TIME ZONE 'Europe/Helsinki') AS minute,
           table_name, count(*) AS events
    FROM raw.cdc_events
    WHERE loaded_at > (SELECT max(loaded_at) FROM raw.cdc_events) - interval '3 hours'
    GROUP BY 1, 2 ORDER BY 1
""")
st.altair_chart(
    alt.Chart(vol).mark_bar().encode(
        x=alt.X("minute:T", title="Load time (last 3 hours of activity)"),
        y=alt.Y("events:Q", title="Events per minute"), color="table_name:N",
        tooltip=["minute:T", "table_name", "events"]),
    use_container_width=True)
explain("load volume", """
Events landed per minute. The **live generator** produces a steady trickle; the one-off **backfill**
appears as a huge spike, because a year of history was written in seconds. Capacity planning in real
pipelines looks exactly like this: normal load vs peak load, and whether the loader keeps up.
""")

st.divider()

st.subheader("Data quality checks")
dq = query("""
    SELECT 'Duplicate Kafka messages in raw' AS check,
           (count(*) - count(DISTINCT (topic, kafka_partition, kafka_offset)))::int AS failing_rows
    FROM raw.cdc_events
    UNION ALL
    SELECT 'Order lines without a valid customer version',
           count(*) FILTER (WHERE customer_sk IS NULL)::int FROM marts.fct_order_items
    UNION ALL
    SELECT 'Order lines without a valid product version',
           count(*) FILTER (WHERE product_sk IS NULL)::int FROM marts.fct_order_items
    UNION ALL
    SELECT 'Orders with no order lines',
           count(*) FILTER (WHERE item_count = 0)::int FROM marts.fct_orders
    UNION ALL
    SELECT 'Customers with more than one current version',
           count(*)::int FROM (SELECT customer_id FROM marts.dim_customer
                               GROUP BY 1 HAVING count(*) FILTER (WHERE is_current) <> 1) x
""")
dq["status"] = dq.failing_rows.map(lambda n: "✅ pass" if n == 0 else "⚠️ check")
st.dataframe(dq, hide_index=True, use_container_width=True)
explain("data quality", """
Live versions of the most important dbt tests. **Zero failing rows** everywhere means the numbers on the
other pages are complete and consistent:
- no duplicate events (the loader is idempotent),
- every sale is linked to the customer and product version that was valid at the time (SCD2 windows have no gaps),
- exactly one current version per customer.

A small number of "orders with no order lines" can appear for orders placed in the last moments before
the marts were rebuilt, because their lines hadn't landed yet. Rebuilding with `dbt build` clears them.
""")
