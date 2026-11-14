select a.customer_id, a.version_number, b.version_number as overlapping_version
from {{ ref('dim_customer') }} a
join {{ ref('dim_customer') }} b
  on a.customer_id = b.customer_id
 and a.customer_sk <> b.customer_sk
 and a.valid_from < b.valid_to
 and b.valid_from < a.valid_to
