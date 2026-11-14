select customer_id
from {{ ref('dim_customer') }}
group by customer_id
having count(*) filter (where is_current) <> 1
