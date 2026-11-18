"""RAG: decide what Lori gets to read this turn, and record exactly what that was.

* Case folders: storage only. The manifest lists what is on disk. File text reaches
  Lori only when the user attaches or drags a file into chat (handled as case_file).
* Knowledge base: the best matching passages from the sources switched on.

The record of what was used ("activity") is what the chat shows the user.
"""

from __future__ import annotations

import json
import logging

from . import vectors
from .ingest import bates_ranges, court_volumes, kb_stats, library_stats
from .settings import (
    KB_PASSAGE_CHARS,
    KB_SOURCES,
    PROMPT_PATH,
    RAG_K_KB,
    load_matter,
)
from .store import get_kv

log = logging.getLogger("lori.rag")

KB_LABELS = {source["id"]: source["label"] for source in KB_SOURCES}


def kb_enabled() -> dict[str, bool]:
    try:
        saved = json.loads(get_kv("kb_enabled") or "{}")
    except ValueError:
        saved = {}
    return {source["id"]: bool(saved.get(source["id"], True)) for source in KB_SOURCES}


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def _search(question: str, k: int, kinds: list[str]) -> list[dict]:
    if not kinds:
        return []
    try:
        return vectors.search(question, k=k, kinds=kinds)
    except Exception as exc:
        log.exception("Qdrant knowledge-base search failed")
        raise RuntimeError(
            f"Knowledge-base search failed ({exc}). Check Qdrant Cloud and try again."
        ) from exc


def manifest_text(stats: dict, bates: dict, volumes: dict) -> str:
    """Every file in the case folders: name, size, pages, Bates range. No file text."""
    if not stats["total_files"]:
        return "The case folders are empty. Nothing has been uploaded yet."
    by_name = {row["name"]: row for row in bates["rows"]}
    lines = []
    for folder in stats["folders"]:
        if not folder["count"]:
            lines.append(f"{folder['label']}: empty")
            continue
        size = sum(f["bytes"] for f in folder["files"])
        lines.append(f"{folder['label']}: {folder['count']} file(s), {_mb(size)}")
        for number, item in enumerate(folder["files"], start=1):
            parts = [f"  {number}. {item['name']}", _mb(item["bytes"])]
            parts.append(f"{item['pages']} page(s)" if item["pages"] else "page count not available")
            row = by_name.get(item["name"]) if folder["id"] == "exhibits" else None
            if row and row["bates_start"]:
                parts.append(f"Bates {row['bates_start']} to {row['bates_end']}")
                parts.append(f"starts at page {row['start_page']}")
            lines.append(" | ".join(parts))
    if bates["pages"]:
        lines.append(
            f"Exhibit Bates run: {bates['first']} to {bates['last']} ({bates['pages']} pages). "
            "These ranges are assigned from page counts; the PDFs themselves are not stamped."
        )
    if bates["missing"] and bates["prefix"]:
        lines.append("No Bates range (no page count): " + ", ".join(bates["missing"]))
    if len(volumes["volumes"]) > 1 or volumes["oversize"]:
        lines.append(
            f"Court portal limit is {volumes['limit_mb']} MB per upload. "
            f"The exhibits need {len(volumes['volumes'])} volumes: "
            + "; ".join(
                f"Vol {i}: {v['files'][0]} to {v['files'][-1]} ({_mb(v['bytes'])})"
                for i, v in enumerate(volumes["volumes"], start=1)
            )
        )
        if volumes["oversize"]:
            lines.append("Over the limit on their own: " + ", ".join(volumes["oversize"]))
    return "\n".join(lines)


def build_context(question: str) -> dict:
    """Knowledge-base passages for this turn, plus a case-folder inventory (no auto text)."""
    stats = library_stats()
    files = [item for folder in stats["folders"] for item in folder["files"]]
    exhibits = next(f["files"] for f in stats["folders"] if f["id"] == "exhibits")
    bates = bates_ranges(exhibits)
    volumes = court_volumes(exhibits)

    # Knowledge base: Court Rules + Statutes when switched on; Firm SOPs when both books are ready.
    enabled = kb_enabled()
    kb = kb_stats(enabled)
    books_ready = all(
        s["files"] > 0 and not s["waiting"] and not s["indexing"]
        for s in kb
        if s["id"] in {"court_rules", "statutes"}
    )
    kinds = []
    for s in kb:
        if not s["chunks"]:
            continue
        if s["id"] == "firm":
            if books_ready:
                kinds.append("firm")
        elif s["enabled"]:
            kinds.append(s["id"])
    kb_hits = _search(question, RAG_K_KB, kinds)
    kb_blocks, kb_used = [], {}
    for hit in kb_hits:
        kind = hit.get("kind", "")
        title = hit.get("title") or hit.get("source_file", "")
        section = f" | {hit['section']}" if hit.get("section") else ""
        kb_blocks.append(
            f"[{KB_LABELS.get(kind, kind)} / {title}{section}]\n{str(hit.get('body', ''))[:KB_PASSAGE_CHARS]}"
        )
        kb_used.setdefault(kind, [])
        if title not in kb_used[kind]:
            kb_used[kind].append(title)

    activity = {
        "mode": "empty" if not files else "inventory",
        "files_total": len(files),
        "files_used": 0,
        "passages": 0,
        "unreadable": [],
        "folders": [],
        "kb": [{"id": kind, "label": KB_LABELS.get(kind, kind), "titles": titles} for kind, titles in kb_used.items()],
        "bates": {"first": bates["first"], "last": bates["last"], "pages": bates["pages"]},
        "pages": sum(item["pages"] or 0 for item in files),
    }
    return {
        "manifest": manifest_text(stats, bates, volumes),
        "library": (
            "No case-folder text was auto-loaded. "
            "File text arrives only when the user attaches or drags a file into chat this turn."
        ),
        "knowledge": "\n\n".join(kb_blocks),
        "activity": activity,
    }


def matter_text() -> str:
    matter = load_matter()
    if not matter.get("name"):
        return "No matter record is set."
    lines = [
        f"Matter: {matter['name']}",
        f"Docket: {matter.get('docket') or 'not set'}",
        f"County: {matter.get('county') or 'not set'}",
        f"Plaintiff: {matter.get('plaintiff') or 'not set'}",
        f"Defendant: {matter.get('defendant') or 'not set'}",
        f"Bates prefix: {matter.get('bates_prefix') or 'not set'}",
    ]
    if matter.get("firm_block"):
        lines.append("Firm block: " + " / ".join(matter["firm_block"]))
    return "\n".join(lines)


def load_prompt(context: dict, facts: list[str], case_file: str = "") -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    fill = {
        "{matter}": matter_text(),
        "{manifest}": context.get("manifest") or "The case folders are empty.",
        "{library}": context.get("library") or "No case-folder text was provided this turn.",
        "{knowledge}": context.get("knowledge") or "No knowledge-base passages matched this turn.",
        "{case_file}": (case_file or "").strip() or "No case file attached this turn.",
        "{facts}": "\n".join(f"- {f}" for f in facts) or "No stored matter notes yet.",
    }
    for key, value in fill.items():
        template = template.replace(key, value)
    return template
