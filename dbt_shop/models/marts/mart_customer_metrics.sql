-- One row per customer who has ordered: lifetime value, recency and RFM segment.

with orders as (
    select * from {{ ref('fct_orders') }}
    where not is_cancelled
),

agg as (
    select
        customer_id,
        min(placed_at)       as first_order_at,
        max(placed_at)       as last_order_at,
        count(*)             as orders,
        sum(order_amount)    as revenue,
        avg(order_amount)    as avg_order_value
    from orders
    group by customer_id
),

as_of as (
    select max(placed_at) as as_of from orders
),

scored as (
    select
        a.*,
        date_trunc('month', a.first_order_at at time zone 'Europe/Helsinki')::date as cohort_month,
        extract(epoch from (as_of.as_of - a.last_order_at)) / 86400.0              as days_since_last_order,
        -- R: recency quintile (5 = bought most recently)
        ntile(5) over (order by a.last_order_at)                                  as r_score,
        -- F: frequency bands (fixed, because most customers have 1 order)
        case when a.orders >= 5 then 5 when a.orders = 4 then 4
             when a.orders = 3 then 3 when a.orders = 2 then 2 else 1 end         as f_score,
        -- M: monetary quintile
        ntile(5) over (order by a.revenue)                                        as m_score
    from agg a
    cross join as_of
)

select
    s.*,
    c.city as current_city,
    s.orders > 1 as is_repeat_customer,
    case
        when s.r_score >= 4 and s.f_score >= 3 then 'Champions'
        when s.r_score >= 3 and s.f_score >= 3 then 'Loyal'
        when s.r_score >= 4 and s.f_score = 1  then 'New'
        when s.r_score >= 3 and s.f_score = 2  then 'Promising'
        when s.r_score <= 2 and s.f_score >= 3 then 'At risk'
        when s.r_score <= 2 and s.f_score <= 2 then 'Hibernating'
        else 'Needs attention'
    end as rfm_segment
from scored s
left join {{ ref('dim_customer') }} c
  on c.customer_id = s.customer_id and c.is_current
