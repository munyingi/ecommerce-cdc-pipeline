-- Grain: one row per order, with a timestamp for each lifecycle stage
-- (an "accumulating snapshot" fact).

with events as (
    select * from {{ ref('stg_orders') }}
    where not is_deleted
),

current_state as (
    select distinct on (order_id)
        order_id, customer_id, status, order_ts
    from events
    order by order_id, changed_at desc, kafka_offset desc
),

milestones as (
    select
        order_id,
        min(changed_at) filter (where status = 'paid')      as paid_at,
        min(changed_at) filter (where status = 'shipped')   as shipped_at,
        min(changed_at) filter (where status = 'delivered') as delivered_at,
        min(changed_at) filter (where status = 'cancelled') as cancelled_at
    from events
    group by order_id
),

amounts as (
    select order_id,
           sum(line_amount) as order_amount,
           count(*)         as item_count
    from {{ ref('fct_order_items') }}
    group by order_id
)

select
    s.order_id,
    s.customer_id,
    to_char(s.order_ts at time zone 'Europe/Helsinki', 'YYYYMMDD')::int as date_key,
    s.status                    as current_status,
    s.order_ts                  as placed_at,
    m.paid_at,
    m.shipped_at,
    m.delivered_at,
    m.cancelled_at,
    extract(epoch from (m.delivered_at - s.order_ts)) / 60.0 as minutes_to_deliver,
    coalesce(a.order_amount, 0) as order_amount,
    coalesce(a.item_count, 0)   as item_count
from current_state s
left join milestones m on m.order_id = s.order_id
left join amounts a    on a.order_id = s.order_id