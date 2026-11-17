"""Qdrant vector store.

Two modes, chosen by env:

* **Qdrant Cloud** (set QDRANT_URL + QDRANT_API_KEY): embeddings run on Qdrant
  Cloud Inference. Render never loads a local model — that is what was blowing
  the free-tier memory limit.
* **Local** (no URL): Qdrant on disk under data/qdrant + FastEmbed locally.

Indexing is incremental: one file's chunks are added or removed at a time.
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
    PointStruct,
    VectorParams,
)
from qdrant_client.models import Document

from .okf import parse_okf
from .settings import (
    EMBEDDING_CACHE_DIR,
    EMBEDDING_MODEL,
    EMBEDDING_VECTOR_SIZE,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_DIR,
    QDRANT_URL,
    USE_QDRANT_CLOUD,
)

log = logging.getLogger("lori.vectors")

# Local on-disk Qdrant locks its folder; serialise every call in both modes.
LOCK = threading.RLock()
BATCH = 32


@lru_cache(maxsize=1)
def _client() -> QdrantClient:
    if USE_QDRANT_CLOUD:
        log.info(
            "Qdrant Cloud at %s (inference on, model %s, dim %s)",
            QDRANT_URL,
            EMBEDDING_MODEL,
            EMBEDDING_VECTOR_SIZE,
        )
        return QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY or None,
            cloud_inference=True,
        )
    log.info("Local Qdrant at %s", QDRANT_DIR)
    return QdrantClient(path=str(QDRANT_DIR))


@lru_cache(maxsize=1)
def _local_embedder():
    """Only used when Qdrant Cloud is not configured."""
    from fastembed import TextEmbedding

    EMBEDDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Loading local embedding model %s (cache %s)", EMBEDDING_MODEL, EMBEDDING_CACHE_DIR)
    return TextEmbedding(model_name=EMBEDDING_MODEL, cache_dir=str(EMBEDDING_CACHE_DIR))


def _as_list(vector) -> list[float]:
    return vector.tolist() if hasattr(vector, "tolist") else list(vector)


def _document(text: str) -> Document:
    return Document(text=text, model=EMBEDDING_MODEL)


def _embed_passages_local(texts: list[str]) -> list[list[float]]:
    return [_as_list(v) for v in _local_embedder().embed(texts, batch_size=BATCH)]


def _embed_query_local(text: str) -> list[float]:
    return _as_list(next(iter(_local_embedder().query_embed(text))))


def _point_id(key: str) -> str:
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def _has_collection() -> bool:
    return QDRANT_COLLECTION in {c.name for c in _client().get_collections().collections}


def _ensure_collection(vector_size: int | None = None) -> None:
    size = vector_size or EMBEDDING_VECTOR_SIZE
    client = _client()
    if _has_collection():
        info = client.get_collection(QDRANT_COLLECTION)
        current = info.config.params.vectors.size  # type: ignore[union-attr]
        if current == size:
            return
        log.warning("Recreating Qdrant collection (dim %s -> %s)", current, size)
        client.delete_collection(QDRANT_COLLECTION)
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=size, distance=Distance.COSINE),
    )


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
        _client().delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="parent", match=MatchValue(value=parent))])
            ),
        )


def upsert_chunks(paths: list[Path], progress: Callable[[int, int], None] | None = None) -> int:
    """Embed OKF chunk files and add them to Qdrant. Returns how many were stored."""
    docs = [doc for doc in (_doc_from_chunk(p) for p in paths) if doc]
    total = len(docs)
    done = 0
    for start in range(0, total, BATCH):
        batch = docs[start : start + BATCH]
        if USE_QDRANT_CLOUD:
            # Cloud Inference: send text + model name; Qdrant embeds on their side.
            points = [
                PointStruct(id=_point_id(doc["key"]), vector=_document(doc["body"]), payload=doc)
                for doc in batch
            ]
            with LOCK:
                _ensure_collection(EMBEDDING_VECTOR_SIZE)
                _client().upsert(collection_name=QDRANT_COLLECTION, points=points)
        else:
            vectors = _embed_passages_local([d["body"] for d in batch])
            points = [
                PointStruct(id=_point_id(doc["key"]), vector=vector, payload=doc)
                for doc, vector in zip(batch, vectors)
            ]
            with LOCK:
                _ensure_collection(len(vectors[0]))
                _client().upsert(collection_name=QDRANT_COLLECTION, points=points)
        done += len(batch)
        if progress:
            progress(done, total)
    return done


def search(question: str, k: int, kinds: list[str] | None = None) -> list[dict]:
    """Top-k chunks, optionally limited to some kinds (library, firm, statutes, court_rules)."""
    if kinds is not None and not kinds:
        return []
    query = _document(question) if USE_QDRANT_CLOUD else _embed_query_local(question)
    query_filter = None
    if kinds:
        query_filter = Filter(must=[FieldCondition(key="kind", match=MatchAny(any=list(kinds)))])
    with LOCK:
        if not _has_collection():
            return []
        hits = _client().query_points(
            collection_name=QDRANT_COLLECTION,
            query=query,
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
