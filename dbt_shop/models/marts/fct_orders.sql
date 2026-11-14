-- Grain: one row per order, with a timestamp for each lifecycle stage
-- (an "accumulating snapshot" fact) and the customer's city at order time.

with events as (
    select * from {{ ref('stg_orders') }}
    where not is_deleted
),

current_state as (
    select distinct on (order_id)
        order_id, customer_id, status, order_ts
    from events
    order by order_id, effective_at desc, kafka_offset desc
),

ranked as (
    select *,
        row_number() over (partition by customer_id order by order_ts, order_id)
            as customer_order_number
    from current_state
),

milestones as (
    select
        order_id,
        min(effective_at) filter (where status = 'paid')      as paid_at,
        min(effective_at) filter (where status = 'shipped')   as shipped_at,
        min(effective_at) filter (where status = 'delivered') as delivered_at,
        min(effective_at) filter (where status = 'cancelled') as cancelled_at
    from events
    group by order_id
),

amounts as (
    select order_id,
           sum(line_amount) as order_amount,
           sum(quantity)    as units,
           count(*)         as item_count
    from {{ ref('fct_order_items') }}
    group by order_id
)

select
    r.order_id,
    r.customer_id,
    c.customer_sk,
    c.city                                   as city_at_order,
    to_char(r.order_ts at time zone 'Europe/Helsinki', 'YYYYMMDD')::int as date_key,
    r.status                                 as current_status,
    r.status = 'cancelled'                   as is_cancelled,
    r.customer_order_number,
    r.customer_order_number = 1              as is_first_order,
    r.order_ts                               as placed_at,
    m.paid_at,
    m.shipped_at,
    m.delivered_at,
    m.cancelled_at,
    extract(epoch from (m.paid_at - r.order_ts)) / 3600.0         as hours_to_pay,
    extract(epoch from (m.shipped_at - m.paid_at)) / 3600.0       as hours_to_ship,
    extract(epoch from (m.delivered_at - m.shipped_at)) / 3600.0  as hours_in_transit,
    extract(epoch from (m.delivered_at - r.order_ts)) / 3600.0    as hours_to_deliver,
    coalesce(a.order_amount, 0)              as order_amount,
    coalesce(a.units, 0)                     as units,
    coalesce(a.item_count, 0)                as item_count
from ranked r
left join {{ ref('dim_customer') }} c
  on c.customer_id = r.customer_id
 and r.order_ts >= c.valid_from
 and r.order_ts <  c.valid_to
left join milestones m on m.order_id = r.order_id
left join amounts a    on a.order_id = r.order_id
