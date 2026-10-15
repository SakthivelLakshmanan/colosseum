"""Adviser-facing tools (get_facts, query_table, search_documents, calculate) for Colosseum.

These functions serve as tools for Google ADK agents and keep an in-memory record
of every invocation so the moderator can verify cited evidence.
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import operator
from typing import Any, Optional

from knowledge.documents import search as search_docs
from knowledge.fact_store import load_facts, rank_facts
from knowledge.queries import QUERY_DEFINITIONS, run_query


def _serialize_tool_output(obj: Any) -> Any:
    """Ensure all tool outputs are JSON-serializable primitives for LLM agent frameworks."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, list):
        return [_serialize_tool_output(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize_tool_output(v) for k, v in obj.items()}
    return obj

# Default six adviser domains
ALL_DOMAINS = ["finance", "business", "customer", "legal", "infosec", "ops"]

# Safe arithmetic operators supported by calculate()
_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# In-memory record of every tool invocation
_tool_call_log: list[dict[str, Any]] = []


def record_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    result_count: int,
    evidence_ids: list[str] | None = None,
) -> None:
    """Record a tool invocation into the in-memory audit log."""
    _tool_call_log.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_name": tool_name,
            "arguments": arguments,
            "result_count": result_count,
            "evidence_ids": evidence_ids or [],
        }
    )


def get_tool_calls() -> list[dict[str, Any]]:
    """Return a copy of the in-memory tool call audit log."""
    return list(_tool_call_log)


def clear_tool_calls() -> None:
    """Clear the in-memory tool call audit log (e.g. at the start of a run)."""
    _tool_call_log.clear()


def get_retrieved_evidence_ids() -> set[str]:
    """Return all evidence IDs (fact_ids and doc_refs) retrieved in the current run log."""
    retrieved = set()
    for entry in _tool_call_log:
        for eid in entry.get("evidence_ids", []):
            retrieved.add(eid)
    return retrieved


def get_facts(
    keywords: str,
    domains: list[str] | None = None,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Search and retrieve verified atomic facts from the company fact store.

    Facts are filtered by domain and ranked by keyword relevance against the fact statement,
    topic, and metric. Superseded facts are automatically excluded.

    Args:
        keywords: Space-separated search terms or topic query describing the needed facts.
        domains: Optional list of domains to filter by (e.g. ['finance', 'ops']).
                 If None or empty, all six company domains (finance, business, customer,
                 legal, infosec, ops) are queried.
        top_k: Maximum number of relevant facts to return (default: 10).

    Returns:
        List of matching Fact dictionaries, each containing fact_id, statement, entity,
        metric, value_num/value_text, unit, as_of date, source_ref, and reliability.
    """
    target_domains = domains if domains else ALL_DOMAINS
    # Never return superseded facts
    active_facts = load_facts(domains=target_domains, include_superseded=False)
    ranked = rank_facts(facts=active_facts, query_text=keywords, top_k=top_k)

    results = [f.model_dump(mode="json") for f in ranked]
    fact_ids = [r["fact_id"] for r in results]

    record_tool_call(
        tool_name="get_facts",
        arguments={"keywords": keywords, "domains": domains, "top_k": top_k},
        result_count=len(results),
        evidence_ids=fact_ids,
    )

    return results


def query_table(
    name: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute an approved parameterized query against company database tables.

    Free-form SQL is not permitted. Only predefined, audited queries are executed.

    Approved query names:
    - 'success_rate_by_bank': Payment success rate by gateway and issuer bank.
      Optional params: start_month (YYYY-MM-DD), end_month (YYYY-MM-DD).
    - 'success_rate_overall': Overall payment success rate by gateway.
      Optional params: start_month (YYYY-MM-DD), end_month (YYYY-MM-DD).
    - 'autopay_success': Recurring mandate success rates by gateway and mandate type.
    - 'uptime_latency': Average system uptime % and p95 latency in ms by gateway.
    - 'incidents_12m': Incidents by gateway, type, and severity in the past 12 months.
      Optional param: as_of (YYYY-MM-DD).
    - 'ticket_trend': Support tickets and 30-day breaches for a category and gateway.
      Required params: category (str), gateway (str).
    - 'pipeline_summary': Merchant pipeline count and expected monthly volume (INR crore).
    - 'fee_quotes': Official fee schedule and contract quotes for a gateway.
      Required param: gateway (str).

    Args:
        name: Name of the approved query to run.
        params: Optional dictionary of query parameters.

    Returns:
        List of row dictionaries resulting from the query. If an unknown query name is passed,
        returns an error dictionary listing valid query names instead of raising an exception.
    """
    try:
        results = run_query(name=name, params=params)
    except KeyError:
        valid_queries = list(QUERY_DEFINITIONS.keys())
        error_result = [
            {
                "error": f"Unknown query '{name}'. Valid queries are: {valid_queries}",
                "valid_queries": valid_queries,
            }
        ]
        record_tool_call(
            tool_name="query_table",
            arguments={"name": name, "params": params},
            result_count=0,
        )
        return error_result
    except ValueError as e:
        error_result = [{"error": str(e)}]
        record_tool_call(
            tool_name="query_table",
            arguments={"name": name, "params": params},
            result_count=0,
        )
        return error_result

    serialized_results = _serialize_tool_output(results)
    record_tool_call(
        tool_name="query_table",
        arguments={"name": name, "params": params},
        result_count=len(serialized_results),
    )
    return serialized_results


def search_documents(
    question: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Search company contracts, board minutes, policies, and review documents.

    Uses Vertex AI Agent Search over ingested PDFs to return the most relevant text chunks.
    Each result provides an exact doc_ref (filename and page number) citable as evidence.

    Args:
        question: Natural language question or search phrase describing the needed information.
        top_k: Maximum number of document passages to return (default: 5).

    Returns:
        List of DocumentHit dictionaries, each containing:
        - doc_ref: Citable reference string (e.g. 'Brevis_Draft_Agreement.pdf#p1').
        - title: File name of the document.
        - snippet: Text passage extracted from the document (up to ~600 characters).
    """
    hits = search_docs(question=question, top_k=top_k)
    results = [h.model_dump(mode="json") for h in hits]
    doc_refs = [r["doc_ref"] for r in results]

    record_tool_call(
        tool_name="search_documents",
        arguments={"question": question, "top_k": top_k},
        result_count=len(results),
        evidence_ids=doc_refs,
    )

    return results


def _evaluate_ast_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate_ast_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"Unsupported constant type in expression: {type(node.value).__name__}")
        return float(node.value)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        operand = _evaluate_ast_node(node.operand)
        return float(_SAFE_OPERATORS[op_type](operand))

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported binary operator: {op_type.__name__}")
        left = _evaluate_ast_node(node.left)
        right = _evaluate_ast_node(node.right)
        if isinstance(node.op, ast.Div) and right == 0.0:
            raise ValueError("Division by zero in calculate()")
        return float(_SAFE_OPERATORS[op_type](left, right))

    raise ValueError(f"Unsupported syntax in calculate expression: {type(node).__name__}")


def _eval_single_expr(expression_str: Any) -> float:
    if expression_str is None or (isinstance(expression_str, str) and not expression_str.strip()):
        raise ValueError("Expression cannot be empty")
    clean_expr = str(expression_str).strip()
    try:
        parsed_node = ast.parse(clean_expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid arithmetic expression: {e}") from e

    return _evaluate_ast_node(parsed_node)


def calculate(
    expression: str | dict[str, str] | None = None,
    expressions: dict[str, str] | None = None,
    **kwargs: Any,
) -> float | dict[str, float]:
    """Safely calculate exact arithmetic expressions without arbitrary code execution.

    Supports numbers (integers and floats), basic arithmetic operators (+, -, *, /),
    unary minus (-x), and parentheses. Never use this for string or logic operations.
    Accepts a single expression string or a batch dictionary of named expressions
    ({"name": "expression", ...}) to evaluate multiple calculations in a single call.

    Args:
        expression: Mathematical expression string, e.g. '(1500 * 0.40) * (0.20 - 0.176) / 100',
            or a dictionary of named expressions (e.g. {'phase1_benefit': '1500 * 0.40'}).
        expressions: Optional dictionary of named expressions for batch calculation.
        **kwargs: Additional named expressions passed as keyword arguments.

    Returns:
        Computed numeric result as a float if a single expression was provided,
        or a dictionary mapping expression names to float results if batch expressions were provided.

    Raises:
        ValueError: If the expression contains invalid characters, unsupported operations,
                    variables, functions, syntax errors, or division by zero.
    """
    if isinstance(expression, dict) and not expression and not expressions and not kwargs:
        raise ValueError("Expression cannot be empty")
    if isinstance(expressions, dict) and not expressions and not expression and not kwargs:
        raise ValueError("Expression cannot be empty")

    # Collect batch expressions if provided
    batch: dict[str, str] = {}
    if isinstance(expressions, dict):
        batch.update(expressions)
    if isinstance(expression, dict):
        batch.update(expression)
    elif isinstance(expression, str) and expression.strip().startswith("{") and expression.strip().endswith("}"):
        try:
            parsed = json.loads(expression.strip())
            if isinstance(parsed, dict):
                batch.update(parsed)
        except Exception:
            pass
    if kwargs:
        batch.update(kwargs)

    if batch:
        results: dict[str, float] = {}
        for name, expr in batch.items():
            results[name] = _eval_single_expr(expr)
        record_tool_call(
            tool_name="calculate",
            arguments={"expressions": batch},
            result_count=len(results),
        )
        return results

    if expression is None:
        raise ValueError("Expression cannot be empty")
    if not isinstance(expression, str):
        raise ValueError(f"Invalid expression type: {type(expression).__name__}")

    result = _eval_single_expr(expression)

    record_tool_call(
        tool_name="calculate",
        arguments={"expression": expression},
        result_count=1,
    )

    return result
