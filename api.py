"""HTTP door for Lori. Code lives here so app/ stays the engine.

    python api.py            then open http://127.0.0.1:8000/
    http://127.0.0.1:8000/?sample=1   shows the design mockup's sample content

Every endpoint that changes something returns the whole workspace, so the page
always redraws from one source of truth.
"""

from __future__ import annotations



import json
import logging
import os
import uuid
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.bot import reply
from app.case_file import prepare_case_files
from app.drafts import draft_file, file_name, get_draft, save_draft
from app.graph import reset_engine
from app.ingest import (
    ALLOWED,
    kb_source,
    remove_kb_file,
    remove_library_file,
    start_background_sync,
    start_kb_ingest,
    sync_all,
)
from app.okf import parse_okf
from app.rag import kb_enabled
from app.settings import (
    CASE_UPLOAD_DIR,
    CHUNK_DIR,
    EMBEDDING_MODEL,
    EMBEDDING_VECTOR_SIZE,
    QDRANT_URL,
    FIRM_DIR,
    FIRM_NAME,
    KB_SOURCES,
    LIBRARY_DIR,
    LIBRARY_FOLDERS,
    LIVE_URL,
    OKF_DIR,
    PROMPT_PATH,
    QDRANT_COLLECTION,
    UPLOAD_BATCH_MB,
)
from app.store import clear_thread, set_kv
from app.workspace import sample_workspace, workspace

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Index anything new in the folders without holding up the page. A large
    # statute book takes a few minutes the first time; uploads index as they arrive.
    start_background_sync()
    reset_engine()
    yield


app = FastAPI(title="Lori — legal preparation assistant", version="2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _folder_key(folder: str) -> str:
    key = (folder or "").strip().lower()
    if key not in LIBRARY_FOLDERS:
        raise HTTPException(status_code=400, detail=f"folder must be one of: {', '.join(LIBRARY_FOLDERS)}")
    return key


def _allowed_name(filename: str | None) -> str:
    name = Path(filename or "").name
    if not name or Path(name).suffix.lower() not in ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"{name or 'That file'} is not supported. Use {', '.join(sorted(ALLOWED))}.",
        )
    return name


# --- page ---------------------------------------------------------------------


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"ok": True, "agent": "Lori", "firm": FIRM_NAME}


@app.get("/workspace")
def get_workspace(sample: int = 0):
    """Everything the page draws. ?sample=1 returns the design mockup's content."""
    return sample_workspace() if sample else workspace()


# --- chat ---------------------------------------------------------------------


@app.post("/chat")
async def chat(
    message: str = Form(""),
    case_files: list[UploadFile] | None = File(None),
    case_file: UploadFile | None = File(None),
):
    """One turn with Lori.

    case_files (or legacy case_file) are attached in chat for this turn only.
    They are chunked in memory; never added to OKF / RAG.
    """
    uploads: list[UploadFile] = []
    for item in case_files or []:
        if item is not None and (item.filename or "").strip():
            uploads.append(item)
    if case_file is not None and (case_file.filename or "").strip():
        uploads.append(case_file)

    case = None
    if uploads:
        saved: list[tuple[str, Path]] = []
        try:
            for item in uploads:
                name = _allowed_name(item.filename)
                # Unique path per request so concurrent chats with the same filename cannot collide.
                dest = CASE_UPLOAD_DIR / f"{uuid.uuid4().hex}-{name}"
                dest.write_bytes(await item.read())
                saved.append((name, dest))
            case = await run_in_threadpool(prepare_case_files, saved, message or "")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not read an attached file: {exc}") from exc
        finally:
            for _, dest in saved:
                dest.unlink(missing_ok=True)
        if not case:
            names = ", ".join(name for name, _ in saved) or "attachment"
            raise HTTPException(
                status_code=400,
                detail=f"No text could be read from {names}. If a file is a scan, it needs OCR first.",
            )
    if not (message or "").strip() and not case:
        raise HTTPException(status_code=400, detail="Type a message or attach a case file.")
    try:
        await run_in_threadpool(reply, message, case)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger("lori.api").exception("Chat turn failed")
        raise HTTPException(status_code=502, detail=f"Lori could not finish this turn: {exc}") from exc
    return workspace()


@app.post("/thread/reset")
def reset_thread():
    """New conversation. Files, drafts and matter notes are kept."""
    clear_thread()
    set_kv("open_draft", "")
    return workspace()


# --- case folders (storage only; drag into chat to send to Lori) --------------


@app.post("/library/upload")
async def upload_library_files(folder: str = Form("exhibits"), files: list[UploadFile] = File(...)):
    """Save one batch into a case folder. Not indexed — drag into chat to send to Lori."""
    key = _folder_key(folder)
    names = [_allowed_name(item.filename) for item in files]
    payloads = [await item.read() for item in files]
    if sum(len(p) for p in payloads) > UPLOAD_BATCH_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"A batch can be at most {UPLOAD_BATCH_MB} MB.")

    def store() -> None:
        for name, data in zip(names, payloads):
            (LIBRARY_DIR / key / name).write_bytes(data)

    await run_in_threadpool(store)
    return {**workspace(), "uploaded": names, "upload_warnings": []}


@app.get("/library/file")
def open_library_file(folder: str, name: str):
    path = LIBRARY_DIR / _folder_key(folder) / Path(name).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such file.")
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@app.delete("/library/file")
def delete_library_file(folder: str, name: str):
    if not remove_library_file(_folder_key(folder), name):
        raise HTTPException(status_code=404, detail="No such file.")
    return workspace()


# --- knowledge base -------------------------------------------------------------


def _kb_key(source: str) -> str:
    key = (source or "").strip()
    if key not in {s["id"] for s in KB_SOURCES}:
        raise HTTPException(status_code=400, detail=f"source must be one of: {', '.join(s['id'] for s in KB_SOURCES)}")
    return key


@app.post("/kb/toggle")
def toggle_kb(source: str = Form(...), enabled: bool = Form(...)):
    key = _kb_key(source)
    state = kb_enabled()
    state[key] = enabled
    set_kv("kb_enabled", json.dumps(state))
    return workspace()


@app.post("/kb/upload")
async def upload_kb_files(source: str = Form(...), files: list[UploadFile] = File(...)):
    """Upload into Court Rules, Family Law Statutes, or Firm SOPs and index in the background."""
    key = _kb_key(source)
    src = kb_source(key)
    assert src is not None
    if src.get("uploadable") is False or src.get("mode") == "okf_bundle":
        raise HTTPException(
            status_code=400,
            detail="Firm drafting examples are an OKF bundle on disk. Replace files in knowledge/okf-bundles/matrimonial-drafting/ and reindex.",
        )
    names = [_allowed_name(item.filename) for item in files]
    payloads = [await item.read() for item in files]
    if sum(len(p) for p in payloads) > UPLOAD_BATCH_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"A batch can be at most {UPLOAD_BATCH_MB} MB.")

    dest_dir = src["dir"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for name, data in zip(names, payloads):
        dest = dest_dir / name
        dest.write_bytes(data)
        jobs.append((key, key, dest))
    # Save first, index in the background (big statute/court books take minutes on Cloud Inference).
    start_kb_ingest(jobs)
    return {
        **workspace(),
        "uploaded": names,
        "upload_warnings": [],
        "source": key,
    }


def _kb_file_path(src: dict, name: str) -> Path:
    """Resolve a KB file path. Bundles may use relative paths like examples/pension/before.md."""
    root = src["dir"].resolve()
    raw = (name or "").strip().lstrip("/")
    if src.get("mode") == "okf_bundle":
        path = (root / raw).resolve()
    else:
        path = (root / Path(raw).name).resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=400, detail="Invalid knowledge-base path.")
    return path


@app.get("/kb/file")
def open_kb_file(source: str, name: str):
    src = kb_source(_kb_key(source))
    assert src is not None
    path = _kb_file_path(src, name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such file.")
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@app.delete("/kb/file")
def delete_kb_file(source: str, name: str):
    key = _kb_key(source)
    src = kb_source(key)
    if src and (src.get("uploadable") is False or src.get("mode") == "okf_bundle"):
        raise HTTPException(
            status_code=400,
            detail="Firm drafting examples are managed as an OKF bundle on disk and cannot be deleted here.",
        )
    if not remove_kb_file(key, name):
        raise HTTPException(status_code=404, detail="No such file.")
    return workspace()


@app.get("/formatting-rules")
def formatting_rules():
    """The firm rules the PDF builder applies, for the Formatting rules button."""
    path = FIRM_DIR / "writing-and-format.md"
    _, body = parse_okf(path) if path.exists() else ({}, "")
    return {
        "applied": [
            "Times New Roman, 12 point",
            "One-inch margins on Letter paper",
            "Page numbers on every page",
            "Firm block and court caption taken from the matter record",
            "Certifications: double-spaced block paragraphs, no first-line indent, continuous paragraph numbers",
            "Tables only for genuine comparisons, with a ruled header row",
            "Every draft is marked for attorney review",
        ],
        "firm_text": body,
    }


@app.post("/reindex")
async def reindex_knowledge():
    """Rebuild the whole index from the folders. Rarely needed."""
    summary = await run_in_threadpool(sync_all, True)
    return {**summary, "workspace": workspace()}


# --- drafts (the PDF on the right) ----------------------------------------------


@app.get("/drafts/{draft_id}/pdf")
def draft_pdf(draft_id: str, download: int = 0):
    meta, path = get_draft(draft_id), draft_file(draft_id, "draft.pdf")
    if not meta or not path:
        raise HTTPException(status_code=404, detail="No such draft.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=file_name(meta),
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/drafts/{draft_id}/page/{number}.png")
def draft_page(draft_id: str, number: int):
    path = draft_file(draft_id, f"page-{number}.png")
    if not path:
        raise HTTPException(status_code=404, detail="No such page.")
    return FileResponse(path, media_type="image/png")


@app.get("/drafts/{draft_id}/markdown")
def draft_markdown(draft_id: str):
    path = draft_file(draft_id, "draft.md")
    if not path:
        raise HTTPException(status_code=404, detail="No such draft.")
    return {"markdown": path.read_text(encoding="utf-8")}


@app.post("/drafts/{draft_id}/open")
def open_draft(draft_id: str):
    """'Open in Drafting Table': show this draft on the right."""
    if not get_draft(draft_id):
        raise HTTPException(status_code=404, detail="No such draft.")
    set_kv("open_draft", draft_id)
    return workspace()


@app.post("/drafts/{draft_id}/save")
def save_draft_endpoint(draft_id: str):
    """'Save to LEAP': files the PDF and shows it on the right. LEAP itself is not connected yet."""
    meta = save_draft(draft_id)
    if not meta:
        raise HTTPException(status_code=404, detail="No such draft.")
    set_kv("open_draft", draft_id)
    return {**workspace(), "saved_to": meta["saved_to"]}


# --- prompt + raw knowledge (developer tools, used from /docs) --------------------


@app.get("/prompt")
def get_prompt():
    return {"path": str(PROMPT_PATH), "content": PROMPT_PATH.read_text(encoding="utf-8")}


def _sync_prompt_to_live(content: str) -> dict:
    """Copy a local prompt save onto the running Render process. No rebuild."""
    if os.environ.get("RENDER") is not None:
        return {"live_synced": True, "live_detail": "already on live"}
    if not LIVE_URL:
        return {
            "live_synced": False,
            "live_detail": "Set LIVE_URL in .env to your Render URL so Save also updates live.",
        }
    payload = urllib.parse.urlencode({"content": content}).encode()
    request = urllib.request.Request(
        f"{LIVE_URL}/prompt",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            json.loads(response.read().decode())
        return {"live_synced": True, "live_detail": LIVE_URL}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return {"live_synced": False, "live_detail": f"Live HTTP {exc.code}: {detail}"}
    except Exception as exc:
        return {"live_synced": False, "live_detail": f"Live sync failed: {exc}"}


@app.post("/prompt")
def save_prompt(content: str = Form(...)):
    text = content if content.endswith("\n") else content + "\n"
    PROMPT_PATH.write_text(text, encoding="utf-8")
    live = _sync_prompt_to_live(text)
    return {"saved": str(PROMPT_PATH), **live}


@app.get("/knowledge")
def list_knowledge():
    """Where the OKF documents, chunks and vectors live on disk."""
    return {
        "okf_dir": str(OKF_DIR),
        "documents": sorted(p.name for p in OKF_DIR.glob("*.md")),
        "chunk_dir": str(CHUNK_DIR),
        "chunks": sum(1 for _ in CHUNK_DIR.glob("*.md")),
        "vector_store": "qdrant-cloud",
        "qdrant_url": QDRANT_URL,
        "embedding_collection": QDRANT_COLLECTION,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_vector_size": EMBEDDING_VECTOR_SIZE,
        "cloud_inference": True,
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    # Local Qdrant locks its folder, so run exactly one process (no reloader).
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
