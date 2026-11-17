"""Open Knowledge Format: labeled markdown on disk, plus chunking."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .settings import CHUNK_SIZE, MAX_CHUNKS_PER_FILE


def write_okf(path: Path, meta: dict, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep section labels YAML-safe (ebook titles often contain --- which breaks naive parsers).
    safe = dict(meta)
    if "section" in safe:
        safe["section"] = section_label(str(safe.get("section") or ""))
    front = yaml.safe_dump(safe, sort_keys=False, allow_unicode=True, width=10_000).strip()
    path.write_text(f"---\n{front}\n---\n\n{body.strip()}\n", encoding="utf-8")


def _split_front_matter(text: str) -> tuple[dict, str]:
    """Split OKF only on a closing --- line. Values may contain --- (common in statute titles)."""
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    front_lines: list[str] = []
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            front = yaml.safe_load("".join(front_lines)) or {}
            return front, "".join(lines[index + 1 :]).strip()
        front_lines.append(lines[index])
    return {}, text.strip()


def parse_okf(path: Path) -> tuple[dict, str]:
    return _split_front_matter(path.read_text(encoding="utf-8"))


def parse_meta(path: Path) -> dict:
    """Front matter only. Cheap even when the body is a whole statute book."""
    lines: list[str] = []
    with path.open(encoding="utf-8") as handle:
        if handle.readline().strip() != "---":
            return {}
        for line in handle:
            if line.strip() == "---":
                return yaml.safe_load("".join(lines)) or {}
            lines.append(line)
    return {}


def slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return out or "note"


def section_label(text: str, limit: int = 160) -> str:
    """Short, clean heading for chunk metadata (no ebook markup, no ---)."""
    label = text or ""
    label = re.sub(r"\{[.#][^{}]*\}", "", label)
    label = re.sub(r"\[([^\[\]]*)\]\([^)]*\)", r"\1", label)
    label = re.sub(r"\[\.?(?:bold|italic|underline)\]", "", label)
    label = label.replace("---", "—").replace("–", "—")
    label = re.sub(r"[\[\]\\]+", " ", label)
    label = " ".join(label.split()).strip(" -—")
    if len(label) > limit:
        label = label[: limit - 1].rstrip() + "…"
    return label


def _split_sections(body: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in body.splitlines():
        if line.startswith("#"):
            sections.append((section_label(line.lstrip("#").strip()), []))
        else:
            sections[-1][1].append(line)
    out = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if text:
            out.append((heading, text))
    return out


def _pack_pieces(heading: str, text: str, size: int) -> list[tuple[str, str]]:
    if len(text) <= size:
        return [(heading, text)]
    chunks: list[tuple[str, str]] = []
    current = ""
    for para in re.split(r"\n\s*\n", text):
        piece = para.strip()
        if not piece:
            continue
        if current and len(current) + len(piece) + 2 > size:
            chunks.append((heading, current))
            current = piece
        else:
            current = f"{current}\n\n{piece}".strip() if current else piece
    if current:
        chunks.append((heading, current))
    return chunks


def chunk_text(body: str, chunk_size: int | None = None, max_chunks: int | None = None) -> list[tuple[str, str]]:
    """Split text into retrieval chunks, packing small headings together."""
    size = chunk_size or CHUNK_SIZE
    limit = max_chunks or MAX_CHUNKS_PER_FILE
    sections = _split_sections(body)
    if not sections:
        return []

    packed: list[tuple[str, str]] = []
    buf_heading = sections[0][0]
    buf_parts: list[str] = []
    buf_len = 0

    def flush():
        nonlocal buf_heading, buf_parts, buf_len
        if not buf_parts:
            return
        text = "\n\n".join(buf_parts).strip()
        packed.extend(_pack_pieces(buf_heading, text, size))
        buf_parts = []
        buf_len = 0

    for heading, text in sections:
        piece_len = len(text)
        # Start a new pack when the buffer would grow well past size.
        if buf_parts and buf_len + piece_len > size:
            flush()
            buf_heading = heading or buf_heading
        if not buf_parts:
            buf_heading = heading or "section"
        label = f"## {heading}" if heading else ""
        buf_parts.append(f"{label}\n{text}".strip() if label else text)
        buf_len += piece_len
    flush()

    # If still too many chunks (huge statute books), re-pack with a larger window.
    guard = 0
    while len(packed) > limit and guard < 8:
        guard += 1
        size = int(size * 1.75)
        merged: list[tuple[str, str]] = []
        buf_h, buf_t = packed[0][0], packed[0][1]
        for heading, text in packed[1:]:
            if len(buf_t) + len(text) + 2 <= size:
                buf_t = f"{buf_t}\n\n{text}"
            else:
                merged.append((buf_h, buf_t))
                buf_h, buf_t = heading, text
        merged.append((buf_h, buf_t))
        packed = merged

    if len(packed) > limit:
        packed = packed[:limit]
    return packed
