with events as (
    select *, coalesce(after, before) as row_data
    from {{ source('raw', 'cdc_events') }}
    where table_name = 'orders'
),

deduped as (
    select *,
        row_number() over (
            partition by row_data->>'order_id', source_lsn, op
            order by kafka_offset
        ) as rn
    from events
)

select
    (row_data->>'order_id')::int              as order_id,
    (row_data->>'customer_id')::int           as customer_id,
    row_data->>'status'                       as status,
    (row_data->>'order_ts')::timestamptz      as order_ts,
    (row_data->>'updated_at')::timestamptz    as updated_at,
    op                                        as cdc_op,
    op = 'd'                                  as is_deleted,
    is_snapshot,
    to_timestamp(source_ts_ms / 1000.0)       as changed_at,
    source_lsn,
    kafka_offset
from deduped
where rn = 1