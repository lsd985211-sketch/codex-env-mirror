#!/usr/bin/env python3
"""Governed, stateless adapter for the local BGE-M3 semantic capability.

The adapter owns transport and bounded vector operations only.  It does not
own an index, citations, domain entities, or business acceptance.  Consumers
must retain their lexical/graph routes and report ``vector_used`` and
``vector_reason`` alongside their own evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse
from typing import Any


SCHEMA = "semantic_capability_owner.v1"
OWNER = "semantic_capability_owner"
DEFAULT_ENDPOINT = "http://127.0.0.1:11434/api/embed"
DEFAULT_MODEL = "bge-m3"
MAX_ITEMS = 32
MAX_TEXT_CHARS = 8192
MAX_DIMENSIONS = 4096
DEFAULT_RRF_CONSTANT = 60
DEFAULT_SELECTION_MARGIN = 0.001
DEFAULT_PROBE_TIMEOUT = 8.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _error(reason: str, *, fallback: str = "structured_fts_graph") -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ok": False,
        "owner": OWNER,
        "vector_used": False,
        "vector_reason": reason,
        "fallback": fallback,
    }


def _bounded_texts(values: list[Any]) -> tuple[list[str], str | None]:
    if not isinstance(values, list) or not values:
        return [], "empty_input"
    if len(values) > MAX_ITEMS:
        return [], "input_count_exceeded"
    texts = [_text(value) for value in values]
    if any(not value for value in texts):
        return [], "empty_text"
    if any(len(value) > MAX_TEXT_CHARS for value in texts):
        return [], "input_length_exceeded"
    return texts, None


def _endpoint(endpoint: str = "") -> str:
    value = _text(endpoint) or _text(os.environ.get("CODEX_BGE_M3_ENDPOINT")) or DEFAULT_ENDPOINT
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("endpoint_not_loopback")
    return value


def _request_embeddings(texts: list[str], *, endpoint: str, model: str, timeout: float) -> list[list[float]]:
    payload = json.dumps({"model": model, "input": texts, "truncate": False}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        decoded = json.loads(response.read(MAX_ITEMS * MAX_DIMENSIONS * 16 + 4096).decode("utf-8"))
    vectors = decoded.get("embeddings") if isinstance(decoded, dict) else None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError("invalid_embedding_shape")
    normalized: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list) or not vector or len(vector) > MAX_DIMENSIONS:
            raise ValueError("invalid_embedding_vector")
        values = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("non_finite_embedding")
        normalized.append(values)
    return normalized


def status(*, probe: bool = False, endpoint: str = "", model: str = DEFAULT_MODEL, timeout: float = DEFAULT_PROBE_TIMEOUT) -> dict[str, Any]:
    try:
        selected_endpoint = _endpoint(endpoint)
    except ValueError as exc:
        return _error(str(exc))
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": True,
        "owner": OWNER,
        "capability_id": "model:bge-m3",
        "model": model,
        "endpoint": selected_endpoint,
        "callable": None,
        "healthy": None,
        "vector_used": False,
        "vector_reason": "probe_not_requested",
        "fallback": "structured_fts_graph",
        "persistence": "none",
        "scope": "transport_and_bounded_vectors_only",
        "model_capabilities": {
            "model_family": "BGE-M3",
            "model_dimension": 1024,
            "model_context_tokens": 8192,
            "model_modes": ["dense", "sparse", "multi_vector"],
            "active_transport_modes": ["dense"],
            "inactive_transport_modes": ["sparse", "multi_vector"],
            "inactive_reason": "ollama_api_embed_returns_dense_embeddings_only",
            "batch_input": True,
            "hybrid_fusion": "client_side_rrf",
            "silent_truncation": False,
        },
    }
    if not probe:
        return result
    try:
        _request_embeddings(["codex semantic capability probe"], endpoint=result["endpoint"], model=model, timeout=timeout)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        result.update({"callable": False, "healthy": False, "vector_reason": f"probe_failed:{type(exc).__name__}"})
        return result
    result.update({"callable": True, "healthy": True, "vector_reason": "probe_ok"})
    return result


def embed(texts: list[Any], *, endpoint: str = "", model: str = DEFAULT_MODEL, timeout: float = 8.0) -> dict[str, Any]:
    bounded, reason = _bounded_texts(texts)
    if reason:
        return _error(reason)
    unique: list[str] = []
    unique_index: dict[str, int] = {}
    positions: list[int] = []
    for value in bounded:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        if digest not in unique_index:
            unique_index[digest] = len(unique)
            unique.append(value)
        positions.append(unique_index[digest])
    try:
        selected_endpoint = _endpoint(endpoint)
        unique_vectors = _request_embeddings(unique, endpoint=selected_endpoint, model=model, timeout=timeout)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        reason = str(exc) if str(exc) == "endpoint_not_loopback" else f"transport_failed:{type(exc).__name__}"
        return _error(reason)
    vectors = [unique_vectors[index] for index in positions]
    return {
        "schema": SCHEMA,
        "ok": True,
        "owner": OWNER,
        "model": model,
        "count": len(vectors),
        "unique_count": len(unique_vectors),
        "in_batch_reused": len(vectors) - len(unique_vectors),
        "dimensions": len(vectors[0]),
        "embeddings": vectors,
        "vector_used": True,
        "vector_reason": "owner_embedding",
        "fallback": "structured_fts_graph",
        "endpoint": selected_endpoint,
        "scope": "request_batch",
        "eligible_count": len(bounded),
        "coverage": 1.0,
    }


def rerank(query: Any, candidates: list[Any], *, endpoint: str = "", model: str = DEFAULT_MODEL, timeout: float = 8.0, limit: int = 8) -> dict[str, Any]:
    query_texts, reason = _bounded_texts([query])
    candidate_texts, candidate_reason = _bounded_texts(candidates)
    if reason or candidate_reason:
        return _error(reason or candidate_reason or "invalid_input")
    embedded = embed([query_texts[0], *candidate_texts], endpoint=endpoint, model=model, timeout=timeout)
    if not embedded.get("ok"):
        return embedded
    vectors = embedded["embeddings"]
    query_vector = vectors[0]
    query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
    rows = []
    for index, vector in enumerate(vectors[1:]):
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        score = sum(left * right for left, right in zip(query_vector, vector)) / (query_norm * norm)
        rows.append({"index": index, "score": round(score, 8), "text": candidate_texts[index]})
    rows.sort(key=lambda item: (-item["score"], item["index"]))
    return {"schema": SCHEMA, "ok": True, "owner": OWNER, "vector_used": True, "vector_reason": "bounded_cosine_rerank", "results": rows[: max(1, min(int(limit), len(rows), MAX_ITEMS))], "fallback": "structured_fts_graph"}


def _candidate_rows(candidates: list[Any]) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(candidates, list) or not candidates:
        return [], "empty_input"
    if len(candidates) > MAX_ITEMS:
        return [], "input_count_exceeded"
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(candidates):
        if not isinstance(value, dict):
            return [], "candidate_object_required"
        candidate_id = _text(value.get("id") or value.get("candidate_id"))
        text = _text(value.get("text"))
        if not candidate_id or not text or candidate_id in seen:
            return [], "candidate_identity_invalid"
        if len(text) > MAX_TEXT_CHARS:
            return [], "input_length_exceeded"
        seen.add(candidate_id)
        row = {
            "id": candidate_id,
            "text": text,
            "source_ref": _text(value.get("source_ref")),
            "content_sha256": _text(value.get("content_sha256")) or hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "input_index": index,
        }
        for key in ("lexical_rank", "graph_rank"):
            if value.get(key) is not None:
                try:
                    rank = int(value[key])
                except (TypeError, ValueError):
                    return [], f"{key}_invalid"
                if rank < 1:
                    return [], f"{key}_invalid"
                row[key] = rank
        rows.append(row)
    return rows, None


def hybrid_rerank(
    query: Any,
    candidates: list[Any],
    *,
    endpoint: str = "",
    model: str = DEFAULT_MODEL,
    timeout: float = 8.0,
    limit: int = 8,
    rank_constant: int = DEFAULT_RRF_CONSTANT,
) -> dict[str, Any]:
    """Fuse dense, lexical, and graph result ranks without copying domain state."""

    query_texts, query_reason = _bounded_texts([query])
    rows, candidate_reason = _candidate_rows(candidates)
    if query_reason or candidate_reason:
        return _error(query_reason or candidate_reason or "invalid_input")
    if int(rank_constant) < 1:
        return _error("rank_constant_invalid")
    embedded = embed([query_texts[0], *[row["text"] for row in rows]], endpoint=endpoint, model=model, timeout=timeout)
    if not embedded.get("ok"):
        fallback_rows = sorted(rows, key=lambda row: (row.get("lexical_rank", MAX_ITEMS + 1), row.get("graph_rank", MAX_ITEMS + 1), row["input_index"]))
        return {
            **embedded,
            "results": [{key: row[key] for key in ("id", "source_ref", "content_sha256") if row.get(key)} for row in fallback_rows[: max(1, min(limit, len(rows)))]],
            "fallback_used": True,
        }
    query_vector, *vectors = embedded["embeddings"]
    query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
    dense_scores: list[tuple[float, int]] = []
    for index, vector in enumerate(vectors):
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        dense_scores.append((sum(left * right for left, right in zip(query_vector, vector)) / (query_norm * norm), index))
    dense_scores.sort(key=lambda item: (-item[0], item[1]))
    dense_ranks = {index: rank for rank, (_, index) in enumerate(dense_scores, 1)}
    retrievers = ["dense"]
    if any("lexical_rank" in row for row in rows):
        retrievers.append("lexical")
    if any("graph_rank" in row for row in rows):
        retrievers.append("graph")
    output = []
    for index, row in enumerate(rows):
        ranks = {"dense": dense_ranks[index]}
        if "lexical_rank" in row:
            ranks["lexical"] = row["lexical_rank"]
        if "graph_rank" in row:
            ranks["graph"] = row["graph_rank"]
        rrf_score = sum(1.0 / (int(rank_constant) + rank) for rank in ranks.values())
        output.append({
            "id": row["id"],
            "source_ref": row["source_ref"],
            "content_sha256": row["content_sha256"],
            "dense_score": round(dense_scores[dense_ranks[index] - 1][0], 8),
            "rrf_score": round(rrf_score, 10),
            "ranks": ranks,
            "input_index": row["input_index"],
        })
    output.sort(key=lambda row: (-row["rrf_score"], row["ranks"]["dense"], row["input_index"]))
    for row in output:
        row.pop("input_index", None)
    return {
        "schema": SCHEMA,
        "ok": True,
        "owner": OWNER,
        "vector_used": True,
        "vector_reason": "dense_plus_owner_ranks_rrf" if len(retrievers) > 1 else "dense_rrf",
        "retrievers": retrievers,
        "rank_constant": int(rank_constant),
        "results": output[: max(1, min(int(limit), len(output), MAX_ITEMS))],
        "fallback": "caller_owned_lexical_graph_order",
        "fallback_used": False,
        "usage": {key: embedded[key] for key in ("count", "unique_count", "in_batch_reused", "dimensions", "scope", "eligible_count", "coverage")},
    }


def select(
    query: Any,
    candidates: list[Any],
    *,
    endpoint: str = "",
    model: str = DEFAULT_MODEL,
    timeout: float = 8.0,
    margin: float = DEFAULT_SELECTION_MARGIN,
) -> dict[str, Any]:
    ranked = hybrid_rerank(query, candidates, endpoint=endpoint, model=model, timeout=timeout, limit=2)
    results = ranked.get("results") if isinstance(ranked.get("results"), list) else []
    if not ranked.get("ok") or not results:
        return {**ranked, "selected": None, "ambiguous": True, "selection_reason": "semantic_owner_unavailable"}
    observed_margin = results[0]["rrf_score"] - results[1]["rrf_score"] if len(results) > 1 else 1.0
    ambiguous = len(results) > 1 and observed_margin < max(0.0, float(margin))
    return {
        **ranked,
        "selected": None if ambiguous else results[0]["id"],
        "ambiguous": ambiguous,
        "selection_margin": round(observed_margin, 10),
        "required_margin": max(0.0, float(margin)),
        "selection_reason": "codex_judgment_required" if ambiguous else "semantic_margin_satisfied",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BGE-M3 semantic capability owner")
    parser.add_argument("command", choices=("status", "embed", "rerank", "hybrid-rerank", "select"))
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--texts", default="[]")
    parser.add_argument("--query", default="")
    parser.add_argument("--candidates", default="[]")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--rank-constant", type=int, default=DEFAULT_RRF_CONSTANT)
    parser.add_argument("--margin", type=float, default=DEFAULT_SELECTION_MARGIN)
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            payload = status(probe=args.probe, endpoint=args.endpoint, model=args.model, timeout=args.timeout)
        elif args.command == "embed":
            payload = embed(json.loads(args.texts), endpoint=args.endpoint, model=args.model, timeout=args.timeout)
        elif args.command == "rerank":
            payload = rerank(args.query, json.loads(args.candidates), endpoint=args.endpoint, model=args.model, timeout=args.timeout, limit=args.limit)
        elif args.command == "hybrid-rerank":
            payload = hybrid_rerank(args.query, json.loads(args.candidates), endpoint=args.endpoint, model=args.model, timeout=args.timeout, limit=args.limit, rank_constant=args.rank_constant)
        else:
            payload = select(args.query, json.loads(args.candidates), endpoint=args.endpoint, model=args.model, timeout=args.timeout, margin=args.margin)
    except (json.JSONDecodeError, TypeError):
        payload = _error("invalid_json_input")
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
