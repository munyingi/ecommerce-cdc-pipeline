with events as (
    select *, coalesce(after, before) as row_data
    from {{ source('raw', 'cdc_events') }}
    where table_name = 'products'
),

deduped as (
    select *,
        row_number() over (
            partition by row_data->>'product_id', source_lsn, op
            order by kafka_offset
        ) as rn
    from events
)

select
    (row_data->>'product_id')::int            as product_id,
    row_data->>'name'                         as product_name,
    row_data->>'category'                     as category,
    (row_data->>'price')::numeric(10,2)       as price,
    (row_data->>'created_at')::timestamptz    as created_at,
    (row_data->>'updated_at')::timestamptz    as updated_at,
    op                                        as cdc_op,
    op = 'd'                                  as is_deleted,
    is_snapshot,
    to_timestamp(source_ts_ms / 1000.0)       as changed_at,
    case when op = 'd' then to_timestamp(source_ts_ms / 1000.0)
         else coalesce((row_data->>'updated_at')::timestamptz,
                       to_timestamp(source_ts_ms / 1000.0)) end as effective_at,
    source_lsn,
    kafka_offset
from deduped
where rn = 1
