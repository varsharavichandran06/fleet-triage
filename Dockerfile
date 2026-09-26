# Backend image: FastAPI, the embedding and reranking models, and a prebuilt
# corpus and vector index.
#
# The corpus and index are built during the image build rather than at boot,
# so a container starts ready to serve and every replica holds an identical
# index. The workbook is the only input; data/ is excluded by .dockerignore.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Model weights are downloaded at build time into a fixed location so the
    # runtime user can read them and no download happens on first request.
    HF_HOME=/opt/models \
    SENTENCE_TRANSFORMERS_HOME=/opt/models

WORKDIR /srv

# CPU-only torch. The default wheel pulls the full CUDA runtime, which adds
# well over a gigabyte that is never used on this hardware.
RUN pip install --no-cache-dir \
    torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the models into the image. Without this the first request after every
# deploy pays the download plus load cost.
RUN python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY gpu_cluster_failures.xlsx .

# Expand the workbook into documents and build the vector and keyword
# indexes. Neither step needs an API key or a database.
RUN python scripts/ingest_xlsx.py && python scripts/build_index.py

# Run unprivileged. Ownership covers the index and model directories, which
# are the only paths written or memory-mapped at runtime.
RUN useradd --system --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /srv /opt/models
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
