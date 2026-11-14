-- Monthly acquisition cohorts: how many customers from each first-order month
-- come back in later months, and how much they spend.

with orders as (
    select
        customer_id,
        date_trunc('month', placed_at at time zone 'Europe/Helsinki')::date as order_month,
        order_amount
    from {{ ref('fct_orders') }}
    where not is_cancelled
),

first_orders as (
    select customer_id, min(order_month) as cohort_month
    from orders
    group by customer_id
),

cohort_size as (
    select cohort_month, count(*) as cohort_customers
    from first_orders
    group by cohort_month
),

activity as (
    select
        f.cohort_month,
        ((extract(year from o.order_month) - extract(year from f.cohort_month)) * 12
          + extract(month from o.order_month) - extract(month from f.cohort_month))::int
            as months_since_first_order,
        o.customer_id,
        o.order_amount
    from orders o
    join first_orders f using (customer_id)
)

select
    a.cohort_month,
    a.months_since_first_order,
    cs.cohort_customers,
    count(distinct a.customer_id)                                   as active_customers,
    count(distinct a.customer_id)::numeric / cs.cohort_customers    as retention_rate,
    sum(a.order_amount)                                             as revenue,
    sum(a.order_amount) / cs.cohort_customers                       as revenue_per_customer
from activity a
join cohort_size cs using (cohort_month)
group by a.cohort_month, a.months_since_first_order, cs.cohort_customers
