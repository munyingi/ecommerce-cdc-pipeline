CREATE SCHEMA raw;

CREATE TABLE raw.cdc_events (
    event_id         BIGSERIAL PRIMARY KEY,
    topic            TEXT        NOT NULL,
    kafka_partition  INT         NOT NULL,
    kafka_offset     BIGINT      NOT NULL,
    table_name       TEXT        NOT NULL,
    op               CHAR(1)     NOT NULL,   -- c, u, d, r
    record_key       JSONB,
    before           JSONB,
    after            JSONB,
    source_lsn       BIGINT,
    source_tx_id     BIGINT,
    source_ts_ms     BIGINT,
    is_snapshot      BOOLEAN     NOT NULL,
    payload          JSONB       NOT NULL,   -- full original event
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (topic, kafka_partition, kafka_offset)
);

CREATE INDEX ON raw.cdc_events (table_name, source_ts_ms);