"""Paths, env vars, and knobs. Import this first; it creates the folders."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Embeddings run locally with FastEmbed. A Gemini name ("models/...") left over in
# .env from the old setup is not a FastEmbed model, so it is ignored.
_LOCAL_EMBEDDING = "BAAI/bge-small-en-v1.5"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "").strip() or _LOCAL_EMBEDDING
if EMBEDDING_MODEL.startswith("models/"):
    EMBEDDING_MODEL = _LOCAL_EMBEDDING
LIVE_URL = os.getenv("LIVE_URL", "").rstrip("/")
FIRM_NAME = os.getenv("FIRM_NAME", "Tanya Freeman Law LLC")
# Shown under the user's chat bubbles. There is no sign-in yet.
USER_NAME = os.getenv("USER_NAME", "").strip()

# --- Case folders (left panel). Files uploaded here go into OKF + RAG. -------
LIBRARY_DIR = ROOT / "knowledge" / "library"
LIBRARY_FOLDERS = (
    "exhibits",
    "pleadings",
    "discovery",
    "financials",
    "correspondence",
)

# --- Knowledge base (left panel toggles). Reference material, not case files. -
FIRM_DIR = ROOT / "knowledge" / "firm"
KB_DIR = ROOT / "knowledge" / "kb"
KB_SOURCES = (
    {"id": "court_rules", "label": "NJ Court Rules", "dir": KB_DIR / "court-rules"},
    {"id": "statutes", "label": "NJ Family Law Statutes", "dir": KB_DIR / "statutes"},
    {"id": "firm", "label": "Firm SOPs", "dir": FIRM_DIR},
)
# Old drop folder from the sales bot. Kept so nothing breaks; no longer read.
RAW_DIR = ROOT / "knowledge" / "raw"

OKF_DIR = ROOT / "knowledge" / "okf"
CHUNK_DIR = OKF_DIR / "chunks"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "bot.db"
QDRANT_DIR = DATA_DIR / "qdrant"
QDRANT_COLLECTION = "lori_v2"  # payload carries kind / folder / parent / source_file
DRAFT_DIR = DATA_DIR / "drafts"
SAVED_DIR = DATA_DIR / "saved"  # stand-in for LEAP until LEAP is connected
CASE_UPLOAD_DIR = DATA_DIR / "case_uploads"
PROMPT_PATH = ROOT / "prompt.md"
MATTER_PATH = ROOT / "matter.json"
SAMPLE_DIR = ROOT / "samples"

HISTORY_LIMIT = 20
FACT_LIMIT = 20
CHUNK_SIZE = 2000
MAX_CHUNKS_PER_FILE = 250  # case-folder files
KB_MAX_CHUNKS_PER_FILE = 6000  # big reference books (statutes, court rules)

# Retrieval
RAG_K_LIBRARY = 10  # passages from the case folders when the library is too big to send whole
RAG_K_KB = 4  # passages from the knowledge base
LIBRARY_FULL_CHARS = 120_000  # at or under this, Lori gets every case-folder file in full
KB_PASSAGE_CHARS = 4000  # trim very long knowledge-base passages

# Uploads / court portal
UPLOAD_BATCH_MB = 35
COURT_UPLOAD_LIMIT_MB = 35

for folder in (
    RAW_DIR,
    FIRM_DIR,
    LIBRARY_DIR,
    OKF_DIR,
    CHUNK_DIR,
    DATA_DIR,
    QDRANT_DIR,
    DRAFT_DIR,
    SAVED_DIR,
    CASE_UPLOAD_DIR,
):
    folder.mkdir(parents=True, exist_ok=True)
for name in LIBRARY_FOLDERS:
    (LIBRARY_DIR / name).mkdir(parents=True, exist_ok=True)
for source in KB_SOURCES:
    source["dir"].mkdir(parents=True, exist_ok=True)


MATTER_DEFAULT = {
    "name": "",
    "docket": "",
    "county": "",
    "court": [],
    "action": "Civil Action",
    "plaintiff": "",
    "defendant": "",
    "firm_block": [],
    "bates_prefix": "",
}


def load_matter() -> dict:
    """The selected case. Edit matter.json; it is re-read on every request."""
    matter = dict(MATTER_DEFAULT)
    try:
        matter.update(json.loads(MATTER_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return matter


def llm():
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GOOGLE_API_KEY or None,
        temperature=0,
    )
