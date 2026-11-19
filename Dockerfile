# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm AS builder
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

FROM python:3.11-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    EMBEDDING_MODEL=sentence-transformers/all-minilm-l6-v2 \
    EMBEDDING_VECTOR_SIZE=384
RUN useradd -r -u 1001 -m -d /home/appuser appuser
COPY --from=builder /install /usr/local
COPY api.py prompt.md matter.json requirements.txt ./
COPY app ./app
COPY static ./static
COPY samples ./samples
COPY knowledge ./knowledge
RUN mkdir -p knowledge/raw knowledge/firm knowledge/library knowledge/kb knowledge/okf/chunks \
        knowledge/okf-bundles \
        data/drafts data/saved data/case_uploads \
    && chown -R appuser:appuser /app /home/appuser
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn api:app --host 0.0.0.0 --port ${PORT}"]
