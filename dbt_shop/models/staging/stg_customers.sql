with events as (
    select *, coalesce(after, before) as row_data
    from {{ source('raw', 'cdc_events') }}
    where table_name = 'customers'
),

deduped as (
    select *,
        row_number() over (
            partition by row_data->>'customer_id', source_lsn, op
            order by kafka_offset
        ) as rn
    from events
)

select
    (row_data->>'customer_id')::int           as customer_id,
    row_data->>'first_name'                   as first_name,
    row_data->>'last_name'                    as last_name,
    row_data->>'email'                        as email,
    row_data->>'phone'                        as phone,
    row_data->>'city'                         as city,
    row_data->>'country'                      as country,
    (row_data->>'created_at')::timestamptz    as created_at,
    (row_data->>'updated_at')::timestamptz    as updated_at,
    op                                        as cdc_op,
    op = 'd'                                  as is_deleted,
    is_snapshot,
    -- system time: when Postgres committed the change
    to_timestamp(source_ts_ms / 1000.0)       as changed_at,
    -- business time: when the change happened in the shop
    -- (updated_at from the app; deletes have no new updated_at, so use commit time)
    case when op = 'd' then to_timestamp(source_ts_ms / 1000.0)
         else coalesce((row_data->>'updated_at')::timestamptz,
                       to_timestamp(source_ts_ms / 1000.0)) end as effective_at,
    source_lsn,
    kafka_offset
from deduped
where rn = 1
