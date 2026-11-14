-- Grain: one row per order line.
-- Each line points to the customer and product VERSIONS valid at order time.

with items as (
    -- latest state of each order line
    select distinct on (order_item_id) *
    from {{ ref('stg_order_items') }}
    order by order_item_id, changed_at desc, kafka_offset desc
),

orders as (
    -- latest state of each order
    select distinct on (order_id) *
    from {{ ref('stg_orders') }}
    order by order_id, changed_at desc, kafka_offset desc
)

select
    i.order_item_id,
    i.order_id,
    c.customer_sk,
    p.product_sk,
    o.customer_id,
    i.product_id,
    to_char(o.order_ts at time zone 'Europe/Helsinki', 'YYYYMMDD')::int as date_key,
    o.order_ts,
    o.status                         as order_status,
    o.status = 'cancelled'           as is_cancelled,
    i.quantity,
    i.unit_price,
    i.quantity * i.unit_price        as line_amount
from items i
join orders o
  on o.order_id = i.order_id
-- the SCD2 point-in-time joins
left join {{ ref('dim_customer') }} c
  on c.customer_id = o.customer_id
 and o.order_ts >= c.valid_from
 and o.order_ts <  c.valid_to
left join {{ ref('dim_product') }} p
  on p.product_id = i.product_id
 and o.order_ts >= p.valid_from
 and o.order_ts <  p.valid_to
where not i.is_deleted