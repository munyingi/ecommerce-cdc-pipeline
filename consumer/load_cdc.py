"""
CDC loader: reads Debezium events from Kafka and lands them,
unchanged, in the warehouse table raw.cdc_events.

Delivery: at-least-once from Kafka + UNIQUE(topic, partition, offset)
in the warehouse = each event stored exactly once.
"""
import json
import os
import signal
import time

import psycopg
from confluent_kafka import Consumer
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

load_dotenv()

BATCH_SIZE = 500        # write at most this many events per transaction
FLUSH_SECONDS = 1.0     # or flush at least this often

INSERT_SQL = """
INSERT INTO raw.cdc_events
    (topic, kafka_partition, kafka_offset, table_name, op, record_key,
     before, after, source_lsn, source_tx_id, source_ts_ms, is_snapshot, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (topic, kafka_partition, kafka_offset) DO NOTHING
"""

running = True


def stop(*_):
    global running
    running = False


def jsonb_or_none(value):
    return Jsonb(value) if value is not None else None


def to_row(msg):
    """Turn one Kafka message into one raw.cdc_events row."""
    value = json.loads(msg.value())
    source = value.get("source", {})
    key = json.loads(msg.key()) if msg.key() else None
    op = value.get("op")
    return (
        msg.topic(),
        msg.partition(),
        msg.offset(),
        source.get("table"),
        op,
        jsonb_or_none(key),
        jsonb_or_none(value.get("before")),
        jsonb_or_none(value.get("after")),
        source.get("lsn"),
        source.get("txId"),
        source.get("ts_ms"),
        op == "r",
        Jsonb(value),
    )


def flush(conn, consumer, batch):
    """Write the batch, THEN tell Kafka we're done.
    If we crash between the two, Kafka resends and UNIQUE drops duplicates."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(INSERT_SQL, batch)
        inserted = cur.rowcount
    consumer.commit(asynchronous=False)
    return inserted


def main():
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    consumer = Consumer({
        "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP", "localhost:29092"),
        "group.id": "warehouse-loader",
        "auto.offset.reset": "earliest",   # a new group starts from the beginning
        "enable.auto.commit": False,       # we commit only after writing
    })
    # A pattern starting with ^ is a regex: all shop tables,
    # including any new table Debezium starts capturing later
    consumer.subscribe([r"^shop\.public\..*"])

    conn = psycopg.connect(
        host=os.getenv("WH_HOST", "localhost"),
        port=os.getenv("WH_PORT", "5434"),
        dbname=os.getenv("WH_DB", "warehouse"),
        user=os.getenv("WH_USER", "warehouse"),
        password=os.getenv("WH_PASSWORD", "warehouse_pw"),
        autocommit=True,
    )

    batch, read_total, inserted_total = [], 0, 0
    last_flush = time.monotonic()
    print("Consuming... (Ctrl+C to stop)")

    try:
        while running:
            msg = consumer.poll(0.5)
            if msg is not None:
                if msg.error():
                    print(f"Kafka error: {msg.error()}")
                elif msg.value() is not None:   # skip tombstones (empty value)
                    batch.append(to_row(msg))

            due = time.monotonic() - last_flush >= FLUSH_SECONDS
            if batch and (len(batch) >= BATCH_SIZE or due):
                inserted = flush(conn, consumer, batch)
                read_total += len(batch)
                inserted_total += inserted
                print(f"read {len(batch):>4}, inserted {inserted:>4} "
                      f"(totals: read {read_total}, inserted {inserted_total})")
                batch = []
                last_flush = time.monotonic()
    finally:
        if batch:
            flush(conn, consumer, batch)
        consumer.close()
        conn.close()
        print("Stopped cleanly.")


if __name__ == "__main__":
    main()