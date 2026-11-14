-- One row per product price version: how fast it sold while that price was active.
-- Comparing units/day across versions shows how demand reacts to price.

with bounds as (
    select max(order_ts) as last_order_ts from {{ ref('fct_order_items') }}
),

versions as (
    select
        p.product_sk, p.product_id, p.version_number, p.product_name, p.price,
        p.valid_from,
        least(p.valid_to, b.last_order_ts) as valid_until
    from {{ ref('dim_product') }} p
    cross join bounds b
),

sales as (
    select product_sk, sum(quantity) as units, sum(line_amount) as revenue
    from {{ ref('fct_order_items') }}
    where not is_cancelled
    group by product_sk
)

select
    v.product_id,
    v.version_number,
    v.product_name,
    v.price,
    v.valid_from,
    v.valid_until,
    extract(epoch from (v.valid_until - v.valid_from)) / 86400.0 as days_active,
    coalesce(s.units, 0)                                         as units,
    coalesce(s.revenue, 0)                                       as revenue,
    coalesce(s.units, 0)
      / nullif(extract(epoch from (v.valid_until - v.valid_from)) / 86400.0, 0)
                                                                 as units_per_day
from versions v
left join sales s using (product_sk)
where v.valid_until > v.valid_from
