select a.product_id, a.version_number, b.version_number as overlapping_version
from {{ ref('dim_product') }} a
join {{ ref('dim_product') }} b
  on a.product_id = b.product_id
 and a.product_sk <> b.product_sk
 and a.valid_from < b.valid_to
 and b.valid_from < a.valid_to
