-- The daily KPI mart must add up to exactly the same revenue as the fact table.
select k.total, f.total
from (select sum(revenue) as total from {{ ref('mart_daily_kpis') }}) k
cross join (select sum(line_amount) as total from {{ ref('fct_order_items') }}
            where not is_cancelled) f
where k.total <> f.total
