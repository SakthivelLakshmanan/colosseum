"""Document search via Vertex AI Agent Search / Discovery Engine."""
from __future__ import annotations

import logging
from typing import Optional

from google.cloud import discoveryengine_v1 as discoveryengine

from core.schemas import DocumentHit
from core.settings import get_settings

logger = logging.getLogger(__name__)

_search_client: Optional[discoveryengine.SearchServiceClient] = None


def get_search_client() -> discoveryengine.SearchServiceClient:
    """Get or initialize Discovery Engine SearchServiceClient."""
    global _search_client
    if _search_client is None:
        _search_client = discoveryengine.SearchServiceClient()
    return _search_client


def _clean_filename(uri: str = "", title: str = "") -> str:
    """Extract clean file name with .pdf extension from URI or title."""
    if uri:
        # e.g. gs://bucket/docs/Brevis_Draft_Agreement.pdf
        fname = uri.split("/")[-1].split("?")[0].strip()
        if fname:
            if not fname.endswith(".pdf") and "." not in fname:
                fname = f"{fname}.pdf"
            return fname

    if title:
        fname = title.split("/")[-1].strip()
        if fname:
            if not fname.endswith(".pdf") and "." not in fname:
                fname = f"{fname}.pdf"
            return fname

    return "document.pdf"


def _get_serving_config(engine_id: Optional[str] = None) -> str:
    """Construct serving config path for Discovery Engine search."""
    settings = get_settings()
    project = settings.project_id
    location = settings.documents.agent_search.location or "global"

    eng_id = engine_id or settings.documents.agent_search.engine_id
    if not eng_id or eng_id == "YOUR_SEARCH_APP_ID":
        # Fallback: attempt to discover engine if placeholder is still set
        try:
            engine_client = discoveryengine.EngineServiceClient()
            parent = f"projects/{project}/locations/{location}/collections/default_collection"
            engines = list(engine_client.list_engines(parent=parent))
            if engines:
                eng_id = engines[0].name.split("/")[-1]
        except Exception as e:
            logger.warning("Could not auto-discover Discovery Engine ID: %s", e)

    if not eng_id or eng_id == "YOUR_SEARCH_APP_ID":
        raise ValueError("Discovery Engine engine_id is not configured in settings.yaml")

    return (
        f"projects/{project}/locations/{location}/collections/default_collection"
        f"/engines/{eng_id}/servingConfigs/default_search"
    )


def search(
    question: str,
    top_k: int = 5,
    engine_id: Optional[str] = None,
) -> list[DocumentHit]:
    """Search company documents using Agent Search / Discovery Engine.

    Requests chunks only (layout parser with chunking). Falls back to extractive
    segments if chunk mode is unavailable.

    Returns:
        list[DocumentHit]: Hits with doc_ref="<file name>#p<page>", title=file name,
                           and snippet trimmed to ~600 characters.
    """
    if not question or not question.strip():
        return []

    client = get_search_client()
    serving_config = _get_serving_config(engine_id)

    # 1. Attempt CHUNKS search mode
    try:
        content_search_spec = discoveryengine.SearchRequest.ContentSearchSpec(
            search_result_mode=discoveryengine.SearchRequest.ContentSearchSpec.SearchResultMode.CHUNKS,
        )

        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=question.strip(),
            page_size=top_k,
            content_search_spec=content_search_spec,
        )

        response = client.search(request=request)
        hits: list[DocumentHit] = []

        for result in response.results:
            if result.chunk:
                chunk = result.chunk
                uri = chunk.document_metadata.uri if chunk.document_metadata else ""
                title_meta = chunk.document_metadata.title if chunk.document_metadata else ""
                fname = _clean_filename(uri=uri, title=title_meta)

                first_page = 1
                if chunk.page_span and chunk.page_span.page_start:
                    first_page = chunk.page_span.page_start

                doc_ref = f"{fname}#p{first_page}"
                snippet = (chunk.content or "").strip()[:600]

                hits.append(
                    DocumentHit(
                        doc_ref=doc_ref,
                        title=fname,
                        snippet=snippet,
                    )
                )

        if hits:
            return hits[:top_k]

    except Exception as exc:
        logger.warning("Chunk mode search failed, falling back to extractive segments: %s", exc)

    # 2. Fallback to extractive segments
    try:
        fallback_spec = discoveryengine.SearchRequest.ContentSearchSpec(
            extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                max_extractive_segment_count=1,
            ),
            snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                return_snippet=True,
            ),
        )

        fallback_req = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=question.strip(),
            page_size=top_k,
            content_search_spec=fallback_spec,
        )

        fallback_resp = client.search(request=fallback_req)
        fallback_hits: list[DocumentHit] = []

        for result in fallback_resp.results:
            doc = result.document
            derived = dict(doc.derived_struct_data) if doc and doc.derived_struct_data else {}
            uri = derived.get("link") or derived.get("uri") or ""
            title_meta = derived.get("title") or ""
            fname = _clean_filename(uri=uri, title=title_meta)

            first_page = 1
            snippet = ""

            segments = derived.get("extractive_segments") or []
            if segments:
                first_seg = segments[0]
                snippet = first_seg.get("content") or ""
                page_val = first_seg.get("page_number") or first_seg.get("pageNumber")
                if page_val:
                    try:
                        first_page = int(page_val)
                    except (ValueError, TypeError):
                        first_page = 1
            elif derived.get("snippets"):
                snippet = derived["snippets"][0].get("snippet") or ""

            snippet = snippet.strip()[:600]
            doc_ref = f"{fname}#p{first_page}"

            fallback_hits.append(
                DocumentHit(
                    doc_ref=doc_ref,
                    title=fname,
                    snippet=snippet,
                )
            )

        return fallback_hits[:top_k]

    except Exception as exc:
        logger.error("Extractive segments fallback search also failed: %s", exc)
        return []


# Convenient alias per ARCHITECTURE.md §6
search_documents = search
