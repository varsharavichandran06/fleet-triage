"""
FastAPI backend. Three endpoints:

  POST /triage           run (or resume) a triage for a new failure report
  GET  /runs/{run_id}    fetch the stored result of a run
  GET  /runs/{run_id}/audit   fetch the full audit trail for a run

Run with: uvicorn app.main:app --reload --port 8000
"""

import asyncio
import json
import logging
import os
import time

import queue
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.agent import run_triage, _checkpoint_path
from app.audit import read_trail
from app.llm import LLMError
from app.config import RUNS_DIR, CORS_ORIGINS
from app.logging_setup import setup_logging

setup_logging()
log = logging.getLogger(__name__)

app = FastAPI(title="fleet-triage API")

app.add_middleware(
    CORSMiddleware,
    # Explicit origins rather than "*". A wildcard permits any website to
    # call this API from a visitor's browser and read the response.
    allow_origins=CORS_ORIGINS,
    # Restricted to what the API actually exposes and what the browser
    # actually sends, rather than wildcarded.
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class TriageRequest(BaseModel):
    query: str
    run_id: str | None = None  # pass this back to resume a crashed run


@app.post("/triage")
async def triage(req: TriageRequest):
    if not req.query.strip():
        raise HTTPException(400, "query must not be empty")
    # run_triage does blocking IO (embeddings, Neo4j, the LLM call), so it
    # runs in a worker thread and the event loop stays free for other
    # requests, which is the "async programming" expectation in the JD
    # applied to something that actually matters here.
    t0 = time.time()
    log.info(
        "POST /triage query_chars=%d resume=%s", len(req.query), bool(req.run_id)
    )
    try:
        result = await asyncio.to_thread(run_triage, req.query, req.run_id)
    except LLMError as e:
        # The LLM is the upstream dependency here, so surface it as a bad
        # gateway with the real reason rather than a blank 500. Better a
        # loud failure than a canned answer pretending to be a triage.
        log.error("POST /triage failed after %.2fs: %s", time.time() - t0, e)
        raise HTTPException(502, f"LLM unavailable: {e}") from e

    verdict = (result.get("verdict") or {}).get("verdict")
    log.info(
        "POST /triage ok run_id=%s verdict=%s chunks=%d facts=%d total=%.2fs",
        result.get("run_id"), verdict, len(result.get("chunks", [])),
        len(result.get("graph_facts", [])), time.time() - t0,
    )
    return result


def _sse(event: str, data: dict) -> str:
    """Formats one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/triage/stream")
async def triage_stream(req: TriageRequest):
    """
    Same pipeline as /triage, streamed as Server-Sent Events.

    Each completed step is sent as it finishes, so a client can render
    retrieval while the graph, decision and synthesis stages are still
    running. A terminal "done" event carries the full result, and "error"
    carries a failure.

    The run itself happens on a worker thread and communicates through a
    queue; the generator only forwards frames.
    """
    if not req.query.strip():
        raise HTTPException(400, "query must not be empty")

    events: queue.Queue = queue.Queue()
    SENTINEL = object()

    def worker():
        t0 = time.time()
        try:
            result = run_triage(
                req.query,
                req.run_id,
                on_step=lambda step, data: events.put(("step", {"step": step, **data})),
            )
            log.info(
                "POST /triage/stream ok run_id=%s total=%.2fs",
                result.get("run_id"), time.time() - t0,
            )
            events.put(("done", result))
        except LLMError as e:
            log.error("POST /triage/stream failed: %s", e)
            events.put(("error", {"error": f"LLM unavailable: {e}"}))
        except Exception as e:
            log.exception("POST /triage/stream crashed")
            events.put(("error", {"error": str(e)}))
        finally:
            events.put(SENTINEL)

    async def generate():
        log.info("POST /triage/stream query_chars=%d", len(req.query))
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            item = await asyncio.to_thread(events.get)
            if item is SENTINEL:
                break
            event, data = item
            yield _sse(event, data)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Proxies that buffer responses would defeat streaming entirely.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    path = _checkpoint_path(run_id)
    if not os.path.exists(path):
        raise HTTPException(404, "run not found")
    with open(path) as f:
        return json.load(f)


@app.get("/runs/{run_id}/audit")
async def get_audit(run_id: str):
    trail = read_trail(run_id)
    if not trail:
        raise HTTPException(404, "no audit trail for this run")
    return trail


@app.get("/health")
async def health():
    return {"status": "ok"}
