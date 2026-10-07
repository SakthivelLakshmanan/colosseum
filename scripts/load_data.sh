#!/usr/bin/env bash
# Loads the Colosseum starter data into BigQuery and the documents into Cloud Storage.
# Run from the unzipped colosseum_starter_pack folder in Cloud Shell:
#   bash scripts/load_data.sh YOUR_BUCKET_NAME
set -euo pipefail
BUCKET="${1:?Give your bucket name, e.g. bash scripts/load_data.sh colosseum-docs-sl}"
DATASET=colosseum
EVAL_DATASET=colosseum_eval
LOCATION=asia-south1

echo "== Checking datasets"
bq show "$DATASET" >/dev/null 2>&1 || bq --location=$LOCATION mk --dataset "$DATASET"
bq show "$EVAL_DATASET" >/dev/null 2>&1 || bq --location=$LOCATION mk --dataset "$EVAL_DATASET"

echo "== Loading the fact store (explicit schema)"
bq load --replace --source_format=CSV --skip_leading_rows=1 --allow_quoted_newlines \
  "$DATASET.facts" data/facts.csv schemas/facts_schema.json

echo "== Loading company data tables"
for t in transactions_monthly mandates_monthly gateway_health_monthly incidents support_tickets_monthly sales_pipeline fees_quotes; do
  bq load --replace --source_format=CSV --skip_leading_rows=1 --autodetect "$DATASET.$t" "data/$t.csv"
  echo "   loaded $t"
done

echo "== Loading eval tables (agents must never read these)"
bq load --replace --source_format=CSV --skip_leading_rows=1 --autodetect "$EVAL_DATASET.planted_facts_answer_key" evals/planted_facts_answer_key.csv
bq load --replace --source_format=CSV --skip_leading_rows=1 --autodetect "$EVAL_DATASET.routing_tests" evals/routing_tests.csv
bq load --replace --source_format=CSV --skip_leading_rows=1 --autodetect "$EVAL_DATASET.moderator_tests" evals/moderator_tests.csv

echo "== Uploading documents"
gcloud storage cp docs/*.pdf "gs://$BUCKET/docs/"

echo "== Checks"
bq query --use_legacy_sql=false --format=pretty \
  "SELECT domain, COUNT(*) AS facts FROM $DATASET.facts GROUP BY domain ORDER BY domain"
bq query --use_legacy_sql=false --format=pretty \
  "SELECT gateway, ROUND(100*SUM(success_count)/SUM(txn_count),1) AS success_pct FROM $DATASET.transactions_monthly GROUP BY gateway"
bq query --use_legacy_sql=false --format=pretty \
  "SELECT COUNT(*) AS merchants, ROUND(SUM(expected_monthly_volume_inr_crore),1) AS crore_per_month FROM $DATASET.sales_pipeline"
gcloud storage ls "gs://$BUCKET/docs/"
echo "== Done. Expected: 6 domains (125 facts in total), Arvanta 97.8 / Brevis 97.6, 30 merchants / 45.0 crore, 9 PDFs."
