-- SCD Type 2 customer dimension built from CDC change events.
-- One row per version of a customer, with a validity window in BUSINESS time
-- (effective_at = when the change happened in the shop, not when it was loaded).

with changes as (
    select
        customer_id, first_name, last_name, email, phone, city, country,
        created_at, is_deleted, effective_at, kafka_offset,
        -- fingerprint of the attributes we track history for
        md5(concat_ws('|', first_name, last_name, email, phone,
                      city, country, is_deleted::text)) as attr_hash
    from {{ ref('stg_customers') }}
),

flagged as (
    select *,
        lag(attr_hash) over (
            partition by customer_id order by effective_at, kafka_offset
        ) as prev_hash
    from changes
),

-- keep only events that actually changed a tracked attribute
versions as (
    select * from flagged
    where prev_hash is null or attr_hash <> prev_hash
),

windowed as (
    select *,
        row_number() over (
            partition by customer_id order by effective_at, kafka_offset
        ) as version_number,
        lead(effective_at) over (
            partition by customer_id order by effective_at, kafka_offset
        ) as next_effective_at
    from versions
)

select
    md5(customer_id::text || '|' || version_number::text) as customer_sk,
    customer_id,
    version_number,
    first_name,
    last_name,
    email,
    phone,
    city,
    country,
    is_deleted,
    -- the first version starts when the customer was created
    case when version_number = 1 then least(created_at, effective_at)
         else effective_at end                             as valid_from,
    coalesce(next_effective_at, '9999-12-31'::timestamptz) as valid_to,
    next_effective_at is null                              as is_current
from windowed
