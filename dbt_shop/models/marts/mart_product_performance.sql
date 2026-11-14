-- One row per product: sales, current price, price history and ABC class
-- (A = products making up the first 80% of revenue).

with sales as (
    select
        product_id,
        sum(quantity)            as units,
        sum(line_amount)         as revenue,
        count(distinct order_id) as orders
    from {{ ref('fct_order_items') }}
    where not is_cancelled
    group by product_id
),

catalogue as (
    select
        product_id,
        max(product_name) filter (where is_current) as product_name,
        max(category)     filter (where is_current) as category,
        max(price)        filter (where is_current) as current_price,
        min(price)                                  as min_price,
        max(price)                                  as max_price,
        count(*) - 1                                as price_changes,
        min(valid_from)                             as first_listed_at
    from {{ ref('dim_product') }}
    group by product_id
),

ranked as (
    select
        c.*,
        coalesce(s.units, 0)   as units,
        coalesce(s.revenue, 0) as revenue,
        coalesce(s.orders, 0)  as orders,
        row_number() over (order by coalesce(s.revenue, 0) desc, c.product_id) as revenue_rank,
        sum(coalesce(s.revenue, 0)) over (order by coalesce(s.revenue, 0) desc, c.product_id)
          / nullif(sum(coalesce(s.revenue, 0)) over (), 0)                     as cumulative_revenue_share
    from catalogue c
    left join sales s using (product_id)
)

select
    *,
    case when cumulative_revenue_share <= 0.80 then 'A'
         when cumulative_revenue_share <= 0.95 then 'B'
         else 'C' end as abc_class
from ranked
