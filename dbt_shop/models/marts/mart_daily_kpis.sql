-- One row per day: the core commercial KPIs.
-- Revenue = value of non-cancelled orders (GMV net of cancellations).

with daily as (
    select
        date_key,
        count(*)                                                    as orders_placed,
        count(*) filter (where not is_cancelled)                    as orders,
        count(*) filter (where is_cancelled)                        as cancelled_orders,
        coalesce(sum(order_amount) filter (where not is_cancelled), 0) as revenue,
        coalesce(sum(units) filter (where not is_cancelled), 0)        as units,
        count(distinct customer_id) filter (where not is_cancelled)    as active_customers,
        count(*) filter (where is_first_order and not is_cancelled)    as new_customers,
        coalesce(sum(order_amount) filter (where is_first_order and not is_cancelled), 0)
                                                                    as new_customer_revenue,
        coalesce(sum(order_amount) filter (where not is_first_order and not is_cancelled), 0)
                                                                    as returning_customer_revenue
    from {{ ref('fct_orders') }}
    group by date_key
)

select
    d.date_key,
    d.date_day,
    d.month_start,
    d.week_start,
    d.day_of_week,
    d.day_name,
    d.is_weekend,
    coalesce(k.orders_placed, 0)              as orders_placed,
    coalesce(k.orders, 0)                     as orders,
    coalesce(k.cancelled_orders, 0)           as cancelled_orders,
    coalesce(k.revenue, 0)                    as revenue,
    coalesce(k.units, 0)                      as units,
    coalesce(k.active_customers, 0)           as active_customers,
    coalesce(k.new_customers, 0)              as new_customers,
    coalesce(k.new_customer_revenue, 0)       as new_customer_revenue,
    coalesce(k.returning_customer_revenue, 0) as returning_customer_revenue
from {{ ref('dim_date') }} d
left join daily k on k.date_key = d.date_key
where d.date_day between
      (select min((placed_at at time zone 'Europe/Helsinki')::date) from {{ ref('fct_orders') }})
  and (select max((placed_at at time zone 'Europe/Helsinki')::date) from {{ ref('fct_orders') }})
