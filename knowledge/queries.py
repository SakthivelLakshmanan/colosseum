"""Approved read-only named queries against BigQuery company tables."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional

from google.cloud import bigquery

from core.settings import get_settings

_client: Optional[bigquery.Client] = None


def get_client() -> bigquery.Client:
    """Get or initialize BigQuery client."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = bigquery.Client(
            project=settings.project_id,
            location=settings.bigquery.location,
        )
    return _client


def _to_date_str(val: Any) -> str:
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    return str(val)


# Dictionary of approved SQL queries and their parameter builders
QUERY_DEFINITIONS: dict[str, dict[str, Any]] = {
    "success_rate_by_bank": {
        "sql": """
            SELECT
                gateway,
                issuer_bank,
                SUM(txn_count) AS txns,
                ROUND(100 * SUM(success_count) / SUM(txn_count), 2) AS success_pct
            FROM `{project}.{dataset}.transactions_monthly`
            WHERE month BETWEEN @start_month AND @end_month
            GROUP BY gateway, issuer_bank
            ORDER BY gateway, issuer_bank
        """,
        "build_params": lambda p: [
            bigquery.ScalarQueryParameter(
                "start_month", "DATE", _to_date_str(p.get("start_month", "2025-10-01"))
            ),
            bigquery.ScalarQueryParameter(
                "end_month", "DATE", _to_date_str(p.get("end_month", "2026-09-01"))
            ),
        ],
    },
    "success_rate_overall": {
        "sql": """
            SELECT
                gateway,
                SUM(txn_count) AS txns,
                ROUND(100 * SUM(success_count) / SUM(txn_count), 2) AS success_pct
            FROM `{project}.{dataset}.transactions_monthly`
            WHERE month BETWEEN @start_month AND @end_month
            GROUP BY gateway
            ORDER BY gateway
        """,
        "build_params": lambda p: [
            bigquery.ScalarQueryParameter(
                "start_month", "DATE", _to_date_str(p.get("start_month", "2025-10-01"))
            ),
            bigquery.ScalarQueryParameter(
                "end_month", "DATE", _to_date_str(p.get("end_month", "2026-09-01"))
            ),
        ],
    },
    "autopay_success": {
        "sql": """
            SELECT
                gateway,
                mandate_type,
                ROUND(100 * SUM(successes) / SUM(attempts), 2) AS success_pct
            FROM `{project}.{dataset}.mandates_monthly`
            GROUP BY gateway, mandate_type
            ORDER BY gateway, mandate_type
        """,
        "build_params": lambda p: [],
    },
    "uptime_latency": {
        "sql": """
            SELECT
                gateway,
                ROUND(AVG(uptime_pct), 2) AS uptime_pct,
                ROUND(AVG(p95_latency_ms)) AS p95_latency_ms
            FROM `{project}.{dataset}.gateway_health_monthly`
            GROUP BY gateway
            ORDER BY gateway
        """,
        "build_params": lambda p: [],
    },
    "incidents_12m": {
        "sql": """
            SELECT
                gateway,
                type,
                severity,
                COUNT(*) AS incidents,
                SUM(duration_minutes) AS total_minutes
            FROM `{project}.{dataset}.incidents`
            WHERE date > DATE_SUB(@as_of, INTERVAL 12 MONTH)
            GROUP BY gateway, type, severity
            ORDER BY gateway, type, severity
        """,
        "build_params": lambda p: [
            bigquery.ScalarQueryParameter(
                "as_of", "DATE", _to_date_str(p.get("as_of", "2026-09-30"))
            ),
        ],
    },
    "ticket_trend": {
        "sql": """
            SELECT
                month,
                tickets,
                breached_30d
            FROM `{project}.{dataset}.support_tickets_monthly`
            WHERE category = @category AND gateway = @gateway
            ORDER BY month
        """,
        "build_params": lambda p: (
            (
                (_validate_required(p, "category", "gateway"))
                and [
                    bigquery.ScalarQueryParameter("category", "STRING", str(p["category"])),
                    bigquery.ScalarQueryParameter("gateway", "STRING", str(p["gateway"])),
                ]
            )
        ),
    },
    "pipeline_summary": {
        "sql": """
            SELECT
                COUNT(*) AS merchants,
                ROUND(SUM(expected_monthly_volume_inr_crore), 1) AS crore_per_month
            FROM `{project}.{dataset}.sales_pipeline`
        """,
        "build_params": lambda p: [],
    },
    "fee_quotes": {
        "sql": """
            SELECT
                fee_item,
                value,
                unit,
                effective_from,
                source_doc
            FROM `{project}.{dataset}.fees_quotes`
            WHERE gateway = @gateway
            ORDER BY fee_item
        """,
        "build_params": lambda p: (
            (
                (_validate_required(p, "gateway"))
                and [
                    bigquery.ScalarQueryParameter("gateway", "STRING", str(p["gateway"])),
                ]
            )
        ),
    },
}


def _validate_required(params: dict, *keys: str) -> bool:
    """Ensure required parameter keys are present and non-empty."""
    for k in keys:
        if k not in params or params[k] is None or str(params[k]).strip() == "":
            raise ValueError(f"Missing required parameter '{k}' for query")
    return True


def run_query(name: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Execute a named approved query with parameters against BigQuery.

    Raises:
        KeyError: If the query name is not recognized.
        ValueError: If required parameters are missing.
    """
    if name not in QUERY_DEFINITIONS:
        allowed = list(QUERY_DEFINITIONS.keys())
        raise KeyError(f"Unknown query '{name}'. Allowed queries: {allowed}")

    p = params or {}
    query_def = QUERY_DEFINITIONS[name]
    settings = get_settings()
    client = get_client()

    sql = query_def["sql"].format(
        project=settings.project_id,
        dataset=settings.bigquery.dataset,
    )
    query_params = query_def["build_params"](p)

    job_config = bigquery.QueryJobConfig(query_parameters=query_params)
    query_job = client.query(sql, job_config=job_config)
    results = query_job.result()

    serialized: list[dict[str, Any]] = []
    for row in results:
        row_dict = dict(row)
        for k, v in row_dict.items():
            if isinstance(v, (date, datetime)):
                row_dict[k] = v.isoformat()
        serialized.append(row_dict)

    return serialized
