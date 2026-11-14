with events as (
    select *, coalesce(after, before) as row_data
    from {{ source('raw', 'cdc_events') }}
    where table_name = 'order_items'
),

deduped as (
    select *,
        row_number() over (
            partition by row_data->>'order_item_id', source_lsn, op
            order by kafka_offset
        ) as rn
    from events
)

select
    (row_data->>'order_item_id')::int         as order_item_id,
    (row_data->>'order_id')::int              as order_id,
    (row_data->>'product_id')::int            as product_id,
    (row_data->>'quantity')::int              as quantity,
    (row_data->>'unit_price')::numeric(10,2)  as unit_price,
    op                                        as cdc_op,
    op = 'd'                                  as is_deleted,
    is_snapshot,
    to_timestamp(source_ts_ms / 1000.0)       as changed_at,
    source_lsn,
    kafka_offset
from deduped
where rn = 1
