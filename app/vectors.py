"""Qdrant Cloud vector store + Cloud Inference embeddings.

Text is sent to Qdrant with a model name. Qdrant embeds it on their side.
There is no local embedding model in this process.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)
from qdrant_client.models import Document

from .okf import parse_okf
from .settings import (
    EMBEDDING_MODEL,
    EMBEDDING_VECTOR_SIZE,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_URL,
)

log = logging.getLogger("lori.vectors")

LOCK = threading.RLock()
BATCH = 32


@lru_cache(maxsize=1)
def _client() -> QdrantClient:
    if not QDRANT_URL:
        raise RuntimeError(
            "Set QDRANT_URL and QDRANT_API_KEY. Embeddings run on Qdrant Cloud Inference "
            "(no local embedding model)."
        )
    if not QDRANT_API_KEY:
        raise RuntimeError("Set QDRANT_API_KEY for your Qdrant Cloud cluster.")
    log.info(
        "Qdrant Cloud at %s (model %s, dim %s)",
        QDRANT_URL,
        EMBEDDING_MODEL,
        EMBEDDING_VECTOR_SIZE,
    )
    return QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY,
        cloud_inference=True,
    )


def _document(text: str) -> Document:
    return Document(text=text, model=EMBEDDING_MODEL)


def _point_id(key: str) -> str:
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def _has_collection() -> bool:
    return QDRANT_COLLECTION in {c.name for c in _client().get_collections().collections}


def _ensure_payload_indexes() -> None:
    """Qdrant Cloud requires a keyword index before filtering/deleting on a payload field."""
    client = _client()
    for field in ("parent", "kind", "folder"):
        try:
            client.create_payload_index(
                collection_name=QDRANT_COLLECTION,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception as exc:
            # Already exists, or a race with another worker — both are fine.
            if "already exists" not in str(exc).lower():
                log.debug("Payload index %s: %s", field, exc)


def _ensure_collection() -> None:
    client = _client()
    if _has_collection():
        info = client.get_collection(QDRANT_COLLECTION)
        current = info.config.params.vectors.size  # type: ignore[union-attr]
        if current != EMBEDDING_VECTOR_SIZE:
            log.warning(
                "Recreating Qdrant collection (dim %s -> %s)",
                current,
                EMBEDDING_VECTOR_SIZE,
            )
            client.delete_collection(QDRANT_COLLECTION)
            client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=VectorParams(size=EMBEDDING_VECTOR_SIZE, distance=Distance.COSINE),
            )
    else:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_VECTOR_SIZE, distance=Distance.COSINE),
        )
    _ensure_payload_indexes()


def _doc_from_chunk(path: Path) -> dict | None:
    meta, body = parse_okf(path)
    if not body:
        return None
    return {
        "key": path.name,
        "body": body,
        "kind": str(meta.get("kind") or ""),
        "folder": str(meta.get("folder") or ""),
        "parent": str(meta.get("parent") or ""),
        "title": str(meta.get("title") or ""),
        "section": str(meta.get("section") or ""),
        "source_file": str(meta.get("source_file") or ""),
    }


def count() -> int:
    with LOCK:
        if not _has_collection():
            return 0
        return int(_client().count(collection_name=QDRANT_COLLECTION, exact=True).count)


def reset() -> None:
    """Drop every vector. OKF files on disk are untouched."""
    with LOCK:
        if _has_collection():
            _client().delete_collection(QDRANT_COLLECTION)


def delete_parent(parent: str) -> None:
    """Remove every vector that belongs to one document."""
    with LOCK:
        if not _has_collection():
            return
        _ensure_payload_indexes()
        _client().delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="parent", match=MatchValue(value=parent))])
            ),
        )


def upsert_chunks(paths: list[Path], progress: Callable[[int, int], None] | None = None) -> int:
    """Send chunk text to Qdrant Cloud Inference and store the resulting vectors."""
    docs = [doc for doc in (_doc_from_chunk(p) for p in paths) if doc]
    total = len(docs)
    done = 0
    for start in range(0, total, BATCH):
        batch = docs[start : start + BATCH]
        points = [
            PointStruct(id=_point_id(doc["key"]), vector=_document(doc["body"]), payload=doc)
            for doc in batch
        ]
        with LOCK:
            _ensure_collection()
            _client().upsert(collection_name=QDRANT_COLLECTION, points=points)
        done += len(batch)
        if progress:
            progress(done, total)
    return done


def search(question: str, k: int, kinds: list[str] | None = None) -> list[dict]:
    """Top-k chunks, optionally limited to some kinds (library, firm, statutes, court_rules)."""
    if kinds is not None and not kinds:
        return []
    query_filter = None
    if kinds:
        query_filter = Filter(must=[FieldCondition(key="kind", match=MatchAny(any=list(kinds)))])
    with LOCK:
        if not _has_collection():
            return []
        hits = _client().query_points(
            collection_name=QDRANT_COLLECTION,
            query=_document(question),
            query_filter=query_filter,
            limit=k,
            with_payload=True,
        ).points
    out = []
    for hit in hits:
        payload = dict(hit.payload or {})
        payload["score"] = float(hit.score or 0)
        out.append(payload)
    return out
