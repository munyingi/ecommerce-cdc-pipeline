#!/usr/bin/env bash
# One command to load the 12-month backfill through the CDC pipeline,
# rebuild the warehouse with dbt, and open the dashboard.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
mkdir -p logs

echo "1/5  Making sure the Docker stack is up..."
docker compose up -d
docker compose ps --format "table {{.Name}}\t{{.Status}}"

echo "2/5  Starting the CDC consumer in the background (log: logs/consumer.log)..."
if pgrep -f "consumer/load_cdc.py" >/dev/null; then
  echo "     consumer already running"
else
  nohup python consumer/load_cdc.py > logs/consumer.log 2>&1 &
  sleep 3
fi

count() {
  docker exec warehouse-db psql -U warehouse -d warehouse -tAc "SELECT count(*) FROM raw.cdc_events" | tr -d '[:space:]'
}
before=$(count)

echo "3/5  Backfilling 12 months of shop history into the source database..."
python generator/backfill.py

echo "4/5  Waiting for Debezium -> Kafka -> consumer to land every event..."
last=-1; stable=0
while [ "$stable" -lt 3 ]; do
  sleep 5
  now=$(count)
  echo "     events in warehouse: $now (was $before before the backfill)"
  if [ "$now" = "$last" ]; then stable=$((stable + 1)); else stable=0; fi
  last=$now
done

echo "5/5  Rebuilding and testing the warehouse with dbt..."
(cd dbt_shop && dbt build)

echo
echo "Done. Opening the dashboard (Ctrl+C to stop it)..."
python -m streamlit run dashboard/app.py
