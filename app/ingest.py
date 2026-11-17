"""Knowledge-base files on disk -> OKF documents + chunks -> vector index.

Case folders (Exhibits, Pleadings, Discovery, Financials, Correspondence) are
storage only. They are listed in the UI and dragged into chat when needed.
They are never indexed here. Chat attachments go through app.case_file instead.

Only the knowledge base (NJ Court Rules, NJ Family Law Statutes, Firm SOPs)
is ingested into OKF + RAG.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from pathlib import Path
from typing import Callable

from . import vectors
from .okf import chunk_text, parse_meta, parse_okf, section_label, slug, write_okf
from .settings import (
    CHUNK_DIR,
    CHUNK_SIZE,
    COURT_UPLOAD_LIMIT_MB,
    KB_CHUNK_SIZE,
    KB_MAX_CHUNKS_PER_FILE,
    KB_SOURCES,
    LIBRARY_DIR,
    LIBRARY_FOLDERS,
    MAX_CHUNKS_PER_FILE,
    OKF_DIR,
    load_matter,
)

ALLOWED = {".md", ".txt", ".docx", ".pdf"}
log = logging.getLogger("lori.ingest")

# What the background indexer is doing right now (shown beside the knowledge base).
STATUS = {"running": False, "kind": "", "name": "", "done": 0, "total": 0}
_LIBRARY_LOCK = threading.Lock()
_SYNC_LOCK = threading.Lock()


# --- reading files ---------------------------------------------------------


def _read_pdf(path: Path) -> str:
    """Text per page, with a page heading so passages can be pin-cited."""
    from pypdf import PdfReader

    pages = []
    for number, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"## Page {number}\n\n{text}")
    return "\n\n".join(pages)


def _clean_ebook_markdown(text: str) -> str:
    """Strip e-book conversion noise (:::, {.class ...}, internal links) from big .md books."""
    # Always strip pandoc/HTML-ish attributes — Yellow/Red books use these even without :::.
    text = re.sub(r"\{[.#][^{}]*\}", "", text)
    text = re.sub(r"\[\.?(?:bold|italic|underline)\]", "", text)
    text = re.sub(r"\[([^\[\]]*)\]\(#[^)]*\)", r"\1", text)
    if "\n:::" in text:
        text = re.sub(r"^:::\s*h_s\s*\n(.+?)\n:::\s*$", r"## \1", text, flags=re.M)
        text = re.sub(r"^:::.*$", "", text, flags=re.M)
    # Statute titles often use --- as an em-dash; keep body readable and YAML-safe in headings.
    text = text.replace("---", "—")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_source(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return _clean_ebook_markdown(path.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".docx":
        from docx import Document

        return "\n\n".join(p.text.strip() for p in Document(str(path)).paragraphs if p.text.strip())
    if suffix == ".pdf":
        return _read_pdf(path)
    return ""


def page_count(path: Path) -> int | None:
    """Real page count. Only PDFs have one we can trust."""
    if path.suffix.lower() != ".pdf":
        return None
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:
        return None


# --- naming ----------------------------------------------------------------


def concept_id(kind: str, folder: str, name: str) -> str:
    if kind == "library":
        return f"lib-{folder}-{slug(name)}"
    if kind == "firm":
        return f"firm-{slug(name)}"
    return f"kb-{slug(kind)}-{slug(name)}"


def _doc_path(cid: str) -> Path:
    return OKF_DIR / f"{cid}.md"


def _chunk_paths(cid: str) -> list[Path]:
    # "--" never appears inside a slug, so this cannot match another document.
    return sorted(CHUNK_DIR.glob(f"{cid}--*.md"))


def _signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _natural(name: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _files_in(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED),
        key=lambda p: _natural(p.name),
    )


def doc_meta(cid: str) -> dict:
    path = _doc_path(cid)
    if not path.exists():
        return {}
    try:
        return parse_meta(path)
    except Exception:
        return {}


def sources() -> list[tuple[str, str, Path]]:
    """Every knowledge-base file that should be in the index. Case folders are excluded."""
    found: list[tuple[str, str, Path]] = []
    for path in _files_in(KB_SOURCES[2]["dir"]):
        found.append(("firm", "firm", path))
    for source in KB_SOURCES[:2]:
        for path in _files_in(source["dir"]):
            found.append((source["id"], source["id"], path))
    return found


# --- ingest one file ---------------------------------------------------------


def ingest_file(
    kind: str,
    folder: str,
    path: Path,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Write OKF document + chunks for one file and (re)index its vectors."""
    lock = _LIBRARY_LOCK if kind == "library" else threading.Lock()
    with lock:
        cid = concept_id(kind, folder, path.name)
        warnings: list[str] = []
        title = path.stem
        try:
            text = read_source(path)
            if path.suffix.lower() == ".md" and text.startswith("---"):
                front, body = parse_okf(path)
                title = str(front.get("title") or title)
                text = body or text
        except Exception as exc:
            text = ""
            warnings.append(f"{path.name}: could not be read ({exc}).")
        if not text and not warnings:
            warnings.append(f"{path.name}: no text found (scanned image?). Listed, but Lori cannot read it.")

        chunk_size = CHUNK_SIZE if kind == "library" else KB_CHUNK_SIZE
        limit = MAX_CHUNKS_PER_FILE if kind == "library" else KB_MAX_CHUNKS_PER_FILE
        pieces = chunk_text(text, chunk_size=chunk_size, max_chunks=limit) if text else []
        log.info("Chunked %s into %s passages (%s chars)", path.name, len(pieces), len(text))

        for old in _chunk_paths(cid):
            old.unlink()
        try:
            vectors.delete_parent(cid)
        except Exception:
            log.exception("Could not clear old vectors for %s", cid)

        meta = {
            "type": "Document",
            "kind": kind,
            "folder": folder,
            "title": title,
            "source_file": path.name,
            "signature": _signature(path),
            "bytes": path.stat().st_size,
            "pages": page_count(path),
            "chars": len(text),
            "chunks": len(pieces),
        }
        # Parent OKF keeps a short stub — full book body is on disk in knowledge/kb/.
        stub = text if len(text) <= 50_000 else text[:50_000] + "\n\n…[truncated for OKF; full source remains in the knowledge folder]"
        write_okf(_doc_path(cid), meta, stub)

        records: list[dict] = []
        for index, (section, body) in enumerate(pieces, start=1):
            label = section_label(section) if section != "section" else ""
            key = f"{cid}--{index:04d}.md"
            write_okf(
                CHUNK_DIR / key,
                {
                    "type": "Chunk",
                    "kind": kind,
                    "folder": folder,
                    "title": title,
                    "parent": cid,
                    "section": label,
                    "source_file": path.name,
                    "index": index,
                },
                body,
            )
            records.append(
                {
                    "key": key,
                    "body": body,
                    "kind": kind,
                    "folder": folder,
                    "parent": cid,
                    "title": title,
                    "section": label,
                    "source_file": path.name,
                }
            )

        stored = 0
        if records:
            try:
                stored = vectors.upsert_records(records, progress)
                meta["vectors"] = stored
                write_okf(_doc_path(cid), meta, stub)
            except Exception as exc:
                log.exception("Vector index failed for %s", path.name)
                warnings.append(f"{path.name}: saved, but the search index failed ({exc}).")
        else:
            meta["vectors"] = 0
            write_okf(_doc_path(cid), meta, stub)

        return {"cid": cid, "name": path.name, "chunks": len(pieces), "vectors": stored, "warnings": warnings}


def remove_doc(cid: str) -> None:
    for path in _chunk_paths(cid):
        path.unlink()
    doc = _doc_path(cid)
    if doc.exists():
        doc.unlink()
    try:
        vectors.delete_parent(cid)
    except Exception:
        log.exception("Could not clear vectors for %s", cid)


def remove_library_file(folder: str, name: str) -> bool:
    path = LIBRARY_DIR / folder / Path(name).name
    if not path.is_file():
        return False
    with _LIBRARY_LOCK:
        path.unlink()
    remove_doc(concept_id("library", folder, path.name))
    return True


def kb_source(source_id: str) -> dict | None:
    return next((s for s in KB_SOURCES if s["id"] == source_id), None)


def remove_kb_file(source_id: str, name: str) -> bool:
    """Remove a knowledge-base file (court rules / statutes / firm SOPs)."""
    source = kb_source(source_id)
    if not source:
        return False
    path = source["dir"] / Path(name).name
    if not path.is_file():
        return False
    path.unlink()
    remove_doc(concept_id(source_id, source_id, path.name))
    return True


# --- keep the index in step with the folders --------------------------------


def sync_all(force: bool = False) -> dict:
    """Index anything new or changed, drop anything whose file is gone."""
    with _SYNC_LOCK:
        wanted = sources()
        valid = {concept_id(kind, folder, path.name) for kind, folder, path in wanted}

        # Leftovers: documents and chunks whose source file no longer exists.
        for doc in OKF_DIR.glob("*.md"):
            if doc.stem not in valid:
                remove_doc(doc.stem)
        for chunk in CHUNK_DIR.glob("*.md"):
            if chunk.stem.split("--")[0] not in valid or "--" not in chunk.stem:
                chunk.unlink()

        try:
            if force:
                vectors.reset()
            empty_index = vectors.count() == 0
        except Exception:
            log.exception("Vector store unavailable")
            empty_index = False

        done, warnings = 0, []
        for kind, folder, path in wanted:
            meta = doc_meta(concept_id(kind, folder, path.name))
            fresh = (
                meta.get("signature") == _signature(path)
                and "vectors" in meta
                and not (empty_index and meta.get("chunks"))
            )
            if fresh and not force:
                continue
            STATUS.update(running=True, kind=kind, name=path.name, done=0, total=0)

            def report(finished: int, total: int) -> None:
                STATUS.update(done=finished, total=total)

            try:
                result = ingest_file(kind, folder, path, report)
                warnings.extend(result["warnings"])
                done += 1
            except Exception as exc:
                log.exception("Ingest failed for %s", path)
                warnings.append(f"{path.name}: {exc}")
        STATUS.update(running=False, kind="", name="", done=0, total=0)
        return {"files": len(wanted), "indexed_now": done, "warnings": warnings}


def start_background_sync() -> None:
    """Called on server start. Big reference books index without blocking the page."""

    def run() -> None:
        try:
            summary = sync_all()
            log.info("Index sync finished: %s", summary)
        except Exception:
            log.exception("Index sync crashed")
            STATUS.update(running=False)

    threading.Thread(target=run, name="lori-index-sync", daemon=True).start()


def start_kb_ingest(jobs: list[tuple[str, str, Path]]) -> None:
    """Index knowledge-base files in the background so the upload request returns fast."""

    def run() -> None:
        with _SYNC_LOCK:
            for kind, folder, path in jobs:
                STATUS.update(running=True, kind=kind, name=path.name, done=0, total=0)

                def report(finished: int, total: int, _kind=kind) -> None:
                    STATUS.update(kind=_kind, done=finished, total=total)

                try:
                    result = ingest_file(kind, folder, path, report)
                    for warning in result["warnings"]:
                        log.warning("%s", warning)
                    log.info(
                        "Indexed %s: %s chunks, %s vectors",
                        path.name,
                        result["chunks"],
                        result["vectors"],
                    )
                except Exception:
                    log.exception("Background ingest failed for %s", path)
            STATUS.update(running=False, kind="", name="", done=0, total=0)

    threading.Thread(target=run, name="lori-kb-ingest", daemon=True).start()


# --- what the UI and Lori are told about the folders -------------------------


def library_stats() -> dict:
    """Inventory of files stored in the case folders. Not indexed."""
    folders = []
    total_files = 0
    total_bytes = 0
    for folder in LIBRARY_FOLDERS:
        files = []
        for path in _files_in(LIBRARY_DIR / folder):
            size = path.stat().st_size
            pages = page_count(path)
            files.append(
                {
                    "name": path.name,
                    "folder": folder,
                    "bytes": size,
                    "pages": pages,
                    "chars": size,  # placeholder; text is read only when attached to chat
                    "chunks": 0,
                    "indexed": False,
                }
            )
            total_bytes += size
        total_files += len(files)
        folders.append({"id": folder, "label": folder.title(), "count": len(files), "files": files})
    return {"folders": folders, "total_files": total_files, "total_bytes": total_bytes}


def bates_ranges(exhibits: list[dict]) -> dict:
    """Assign Bates ranges to exhibits in folder order from their real page counts.

    This only works out the numbers. Nothing is stamped onto the PDFs.
    """
    prefix = str(load_matter().get("bates_prefix") or "").strip()
    rows, missing, cursor = [], [], 0
    for item in exhibits:
        pages = item.get("pages")
        if not prefix or not pages:
            missing.append(item["name"])
            rows.append({**item, "bates_start": None, "bates_end": None, "start_page": None})
            continue
        rows.append(
            {
                **item,
                "bates_start": f"{prefix} {cursor + 1:05d}",
                "bates_end": f"{prefix} {cursor + pages:05d}",
                "start_page": cursor + 1,
            }
        )
        cursor += pages
    return {
        "prefix": prefix,
        "rows": rows,
        "pages": cursor,
        "first": f"{prefix} {1:05d}" if cursor else "",
        "last": f"{prefix} {cursor:05d}" if cursor else "",
        "missing": missing,
    }


def court_volumes(exhibits: list[dict]) -> dict:
    """Group exhibits, in order, into upload volumes under the court portal limit."""
    limit = COURT_UPLOAD_LIMIT_MB * 1024 * 1024
    volumes: list[dict] = []
    oversize = []
    for item in exhibits:
        size = item["bytes"]
        if size > limit:
            oversize.append(item["name"])
        if not volumes or volumes[-1]["bytes"] + size > limit:
            volumes.append({"files": [], "bytes": 0})
        volumes[-1]["files"].append(item["name"])
        volumes[-1]["bytes"] += size
    return {"limit_mb": COURT_UPLOAD_LIMIT_MB, "volumes": volumes, "oversize": oversize}


def kb_stats(enabled: dict[str, bool]) -> list[dict]:
    out = []
    for source in KB_SOURCES:
        files = _files_in(source["dir"])
        chunks = vectors_done = waiting = 0
        file_rows = []
        for path in files:
            meta = doc_meta(concept_id(source["id"], source["id"], path.name))
            chunks += int(meta.get("chunks") or 0)
            vectors_done += int(meta.get("vectors") or 0)
            if "vectors" not in meta or meta.get("signature") != _signature(path):
                waiting += 1
            file_rows.append(
                {
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "chunks": int(meta.get("chunks") or 0),
                    "indexed": "vectors" in meta,
                }
            )
        indexing = STATUS["running"] and STATUS["kind"] == source["id"]
        percent = int(100 * STATUS["done"] / STATUS["total"]) if indexing and STATUS["total"] else 0
        out.append(
            {
                "id": source["id"],
                "label": source["label"],
                "files": len(files),
                "file_rows": file_rows,
                "chunks": chunks,
                "vectors": vectors_done,
                "enabled": bool(enabled.get(source["id"], True)),
                "indexing": indexing,
                "waiting": waiting,  # files not in the index yet
                "percent": percent,
            }
        )
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(sync_all(force="--reindex" in sys.argv))
