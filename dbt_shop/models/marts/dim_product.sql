-- SCD Type 2 product dimension: full price and catalogue history (business time).

with changes as (
    select
        product_id, product_name, category, price,
        created_at, is_deleted, effective_at, kafka_offset,
        md5(concat_ws('|', product_name, category, price::text,
                      is_deleted::text)) as attr_hash
    from {{ ref('stg_products') }}
),

flagged as (
    select *,
        lag(attr_hash) over (
            partition by product_id order by effective_at, kafka_offset
        ) as prev_hash
    from changes
),

versions as (
    select * from flagged
    where prev_hash is null or attr_hash <> prev_hash
),

windowed as (
    select *,
        row_number() over (
            partition by product_id order by effective_at, kafka_offset
        ) as version_number,
        lead(effective_at) over (
            partition by product_id order by effective_at, kafka_offset
        ) as next_effective_at
    from versions
)

select
    md5(product_id::text || '|' || version_number::text) as product_sk,
    product_id,
    version_number,
    product_name,
    category,
    price,
    is_deleted,
    case when version_number = 1 then least(created_at, effective_at)
         else effective_at end                             as valid_from,
    coalesce(next_effective_at, '9999-12-31'::timestamptz) as valid_to,
    next_effective_at is null                              as is_current
from windowed
