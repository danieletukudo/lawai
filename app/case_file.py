"""Chat-uploaded case PDF/DOC for this turn only — not OKF / not company RAG.

Large files are chunked in memory. Only the most relevant chunks for the user's
question are sent to the LLM.
"""

from __future__ import annotations

import re
from pathlib import Path

from .okf import chunk_text
from .settings import CHUNK_SIZE

# How many case chunks may enter the prompt for one turn.
MAX_CASE_CHUNKS_TO_LLM = 8
# Hard stop while reading raw text into memory.
MAX_EXTRACT_CHARS = 2_000_000


def _read_raw(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".docx":
        from docx import Document

        return "\n\n".join(p.text.strip() for p in Document(str(path)).paragraphs if p.text.strip())
    if suffix == ".pdf":
        from pypdf import PdfReader

        pages = []
        for page in PdfReader(str(path)).pages:
            chunk = (page.extract_text() or "").strip()
            if chunk:
                pages.append(chunk)
        return "\n\n".join(pages)
    return ""


def extract_text(path: str | Path) -> str:
    """Full text extract (may be large). Prefer prepare_case_context() for chat."""
    text = _read_raw(Path(path)).strip()
    if len(text) > MAX_EXTRACT_CHARS:
        text = text[:MAX_EXTRACT_CHARS]
    return text


def _score_chunk(query: str, heading: str, body: str) -> int:
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
    if not words:
        return 1
    hay = f"{heading} {body}".lower()
    return sum(1 for w in words if w in hay)


def select_case_chunks(
    full_text: str,
    question: str = "",
    *,
    max_chunks: int = MAX_CASE_CHUNKS_TO_LLM,
) -> dict:
    """Chunk case text in memory and keep the best pieces for this question."""
    text = (full_text or "").strip()
    if not text:
        return {
            "context": "",
            "total_chunks": 0,
            "used_chunks": 0,
            "chunked": False,
            "detail": "Case file had no extractable text.",
        }

    pieces = chunk_text(text, chunk_size=CHUNK_SIZE)
    total = len(pieces)
    if total == 0:
        return {
            "context": "",
            "total_chunks": 0,
            "used_chunks": 0,
            "chunked": False,
            "detail": "Case file produced no chunks.",
        }

    # Small file: send everything.
    if total <= max_chunks:
        blocks = []
        for i, (heading, body) in enumerate(pieces, start=1):
            label = heading or f"Part {i}"
            blocks.append(f"[Case chunk {i}/{total}: {label}]\n{body}")
        return {
            "context": "\n\n".join(blocks),
            "total_chunks": total,
            "used_chunks": total,
            "chunked": total > 1,
            "detail": f"Sent all {total} case chunk(s) to Lori.",
        }

    # Large file: score by question, keep top N (stable order by score then index).
    ranked = []
    for index, (heading, body) in enumerate(pieces):
        score = _score_chunk(question, heading, body)
        ranked.append((score, index, heading, body))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    chosen = ranked[:max_chunks]
    # Restore document order for readability.
    chosen.sort(key=lambda row: row[1])

    blocks = []
    for score, index, heading, body in chosen:
        label = heading or f"Part {index + 1}"
        blocks.append(f"[Case chunk {index + 1}/{total}: {label}]\n{body}")

    return {
        "context": "\n\n".join(blocks),
        "total_chunks": total,
        "used_chunks": len(chosen),
        "chunked": True,
        "detail": (
            f"Case file split into {total} chunks; "
            f"sent the {len(chosen)} most relevant to your question (not saved to OKF/RAG)."
        ),
    }


def prepare_case_context(path: str | Path, question: str = "") -> dict:
    """Read case upload → chunk → select relevant pieces for the LLM (ephemeral)."""
    full = extract_text(path)
    result = select_case_chunks(full, question)
    result["chars_extracted"] = len(full)
    return result
