#!/usr/bin/env bash
# Copies the hackathon's raw CSVs from the organizers' bucket into our bronze layer.
#
# Source layout (two shapes):
#   data/<table>.csv                                                  snapshot tables
#   data/<table>/year=YYYY/month=MM/day=DD/<table>_YYYYMMDD.csv       daily tables
# Bronze layout (folder structure kept as is, under one ingest date):
#   s3://<lake>/bronze/<table>/ingest_date=<today>/<table>.csv
#   s3://<lake>/bronze/<table>/ingest_date=<today>/year=YYYY/month=MM/day=DD/<table>_YYYYMMDD.csv
#
# The source bucket lives in another AWS account and is read with the read-only
# keys from the data dictionary, so this needs two AWS CLI profiles:
#   SOURCE_PROFILE  read-only datathon keys (configure locally, never commit them)
#   TARGET_PROFILE  your team account
# Files are synced down to STAGING_DIR first (a few GB), then synced up. Re-running
# skips files already downloaded.
#
# Usage: SOURCE_URI=s3://<organizers-bucket>/data LAKE_BUCKET=<lake bucket> ./scripts/ingest_to_bronze.sh
set -euo pipefail

: "${SOURCE_URI:?set SOURCE_URI to the organizers s3://bucket/prefix}"
: "${LAKE_BUCKET:?set LAKE_BUCKET (terraform output lake_bucket)}"
SOURCE_PROFILE="${SOURCE_PROFILE:-datathon}"
TARGET_PROFILE="${TARGET_PROFILE:-default}"
INGEST_DATE="${INGEST_DATE:-$(date +%F)}"
STAGING_DIR="${STAGING_DIR:-${TMPDIR:-/tmp}/bronze_staging}"

TABLES=(customers products branches service_agents marketing_campaigns campaign_sends
        transactions call_center_interactions call_transcripts satisfaction_surveys
        digital_events complaints daily_exchange_rates)

echo "Downloading ${SOURCE_URI} to ${STAGING_DIR}"
aws s3 sync "${SOURCE_URI%/}/" "$STAGING_DIR/" --profile "$SOURCE_PROFILE" --exclude "*" --include "*.csv"

for table in "${TABLES[@]}"; do
  dst="s3://${LAKE_BUCKET}/bronze/${table}/ingest_date=${INGEST_DATE}"
  if [[ -d "$STAGING_DIR/$table" ]]; then
    echo "-> ${table} (daily partitions)"
    aws s3 sync "$STAGING_DIR/$table/" "$dst/" --profile "$TARGET_PROFILE" --exclude "*" --include "*.csv"
  elif [[ -f "$STAGING_DIR/$table.csv" ]]; then
    echo "-> ${table} (single file)"
    aws s3 cp "$STAGING_DIR/$table.csv" "$dst/$table.csv" --profile "$TARGET_PROFILE"
  else
    echo "!! ${table}: not found in ${SOURCE_URI}" >&2
    exit 1
  fi
done
echo "Done. Start the pipeline with: aws glue start-workflow-run --name <glue_workflow output>"
