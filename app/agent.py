"""
The triage agent loop.

Four steps run in order: hybrid retrieval, knowledge graph context, a
typed verdict, and prose synthesis.

Each step writes its result to a checkpoint file before the next begins.
If the process dies partway through, calling run_triage() again with the
same run_id resumes from the last completed step rather than repeating
work or losing the run.

An optional on_step callback is invoked as each step completes, with the
step name and the data that step produced, so a caller can stream partial
results instead of waiting for the whole run.
"""

import json
import logging
import os
import re
import time
import uuid
from typing import Callable, Optional

from app.config import RUNS_DIR, MIN_ENTITY_LEN
from app.retrieval import hybrid_search_detailed
from app.graph import graph_context, all_entities
from app.llm import synthesize_triage_detailed
from app.decide import classify
from app.audit import log_event
from app.logging_setup import payload

log = logging.getLogger(__name__)


def _checkpoint_path(run_id: str) -> str:
    return os.path.join(RUNS_DIR, run_id, "checkpoint.json")


def _load_checkpoint(run_id: str) -> dict:
    path = _checkpoint_path(run_id)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"run_id": run_id, "completed_steps": [], "data": {}}


def _save_checkpoint(run_id: str, checkpoint: dict) -> None:
    os.makedirs(os.path.join(RUNS_DIR, run_id), exist_ok=True)
    path = _checkpoint_path(run_id)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(checkpoint, f, indent=2)
    os.replace(tmp_path, path)  # atomic on POSIX, avoids a half written file


def _emit(on_step, step: str, data: dict) -> None:
    """
    Reports a completed step to the caller, if one asked to be told.

    Callback failures are logged and swallowed: a consumer that has
    disconnected must not take the run down with it, since the run is
    checkpointing and will complete regardless.
    """
    if on_step is None:
        return
    try:
        on_step(step, data)
    except Exception as e:
        log.warning("on_step callback failed for step %s: %s", step, e)


def _guess_entities(query: str, known_entities: list) -> list:
    """
    Matches query text against known graph entity names.

    Anchors on word boundaries and drops any match wholly contained in a
    longer one, so a query mentioning "xid 13" reports that rather than
    also reporting a bare "13". Names shorter than MIN_ENTITY_LEN are
    skipped, as they match too loosely in a substring test.
    """
    query_lower = query.lower()
    hits = []
    for e in known_entities:
        name = e.strip().lower()
        if len(name) < MIN_ENTITY_LEN:
            continue
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", query_lower):
            hits.append(e)

    # Keep the most specific match: "xid 13" wins over "13".
    hits.sort(key=lambda x: len(x), reverse=True)
    kept: list = []
    for h in hits:
        if not any(h.lower() in k.lower() for k in kept):
            kept.append(h)
    return kept



def run_triage(
    query: str,
    run_id: Optional[str] = None,
    on_step: Optional[Callable[[str, dict], None]] = None,
) -> dict:
    """
    Runs the 3 step triage pipeline for `query`, resuming from checkpoint
    if run_id points at a partially completed run. Returns the full
    result plus the run_id, so the caller can fetch the audit trail
    later with the same id.
    """
    run_id = run_id or str(uuid.uuid4())
    checkpoint = _load_checkpoint(run_id)
    completed = set(checkpoint["completed_steps"])
    data = checkpoint["data"]
    data["query"] = query

    log_event(run_id, "run_started", {"query": query, "resuming": bool(completed)})

    # Step 1: hybrid retrieval
    if "retrieve" not in completed:
        t0 = time.time()
        log.info("[%s] step 1 retrieve: query_chars=%d", run_id[:8], len(query))
        chunks, stages = hybrid_search_detailed(query)
        log.info(
            "[%s] step 1 retrieve done: corpus=%d pool=%d shortlist=%d final=%d in %.3fs",
            run_id[:8], stages["total_chunks"], stages["pool_size"],
            stages["shortlist_size"], len(chunks), time.time() - t0,
        )
        _emit(on_step, "retrieve", {"chunks": chunks, "retrieval_stages": stages})
        payload(log, "retrieved chunks", [c["chunk_id"] for c in chunks])
        data["chunks"] = chunks
        # Stage by stage record of the ranking, drawn by the UI so the
        # retrieval decision can be inspected rather than taken on faith.
        data["retrieval_stages"] = stages
        completed.add("retrieve")
        checkpoint = {"run_id": run_id, "completed_steps": list(completed), "data": data}
        _save_checkpoint(run_id, checkpoint)
        log_event(
            run_id, "retrieve",
            {"duration_s": round(time.time() - t0, 3), "n_chunks": len(chunks)},
        )
    else:
        log_event(run_id, "retrieve_skipped_resumed", {})

    # Step 2: knowledge graph context
    if "graph" not in completed:
        t0 = time.time()
        try:
            known = all_entities()
            entities = _guess_entities(query, known)
            facts = graph_context(entities)
            log.info(
                "[%s] step 2 graph done: matchable=%d matched=%d facts=%d in %.3fs",
                run_id[:8], len(known), len(entities), len(facts), time.time() - t0,
            )
            payload(log, "graph entities", entities)
        except Exception as e:
            # Neo4j not reachable is a real failure mode worth surviving,
            # not a reason to kill the whole triage run.
            entities, facts = [], []
            log.warning("[%s] step 2 graph failed, continuing without it: %s", run_id[:8], e)
            log_event(run_id, "graph_error", {"error": str(e)})
        data["graph_entities"] = entities
        data["graph_facts"] = facts
        _emit(on_step, "graph", {"graph_entities": entities, "graph_facts": facts})
        completed.add("graph")
        checkpoint = {"run_id": run_id, "completed_steps": list(completed), "data": data}
        _save_checkpoint(run_id, checkpoint)
        log_event(
            run_id, "graph",
            {"duration_s": round(time.time() - t0, 3), "n_facts": len(facts), "entities": entities},
        )
    else:
        log_event(run_id, "graph_skipped_resumed", {})

    # Step 3: typed verdict. Runs before synthesis because classifying the
    # failure is a fast decision over evidence already gathered, and does
    # not need the model that writes the explanation.
    if "decide" not in completed:
        t0 = time.time()
        log.info(
            "[%s] step 3 decide: evidence=%d chunks %d facts",
            run_id[:8], len(data["chunks"]), len(data["graph_facts"]),
        )
        verdict = classify(query, data["chunks"], data["graph_facts"])
        log.info(
            "[%s] step 3 decide done: verdict=%s confidence=%.2f drain=%s via %s in %.3fs",
            run_id[:8], verdict.verdict, verdict.confidence,
            verdict.drain_node, verdict.backend, time.time() - t0,
        )
        data["verdict"] = verdict.as_dict()
        _emit(on_step, "decide", {"verdict": data["verdict"]})
        completed.add("decide")
        checkpoint = {"run_id": run_id, "completed_steps": list(completed), "data": data}
        _save_checkpoint(run_id, checkpoint)
        log_event(
            run_id, "decide",
            {"duration_s": round(time.time() - t0, 3), "verdict": verdict.verdict,
             "confidence": verdict.confidence, "backend": verdict.backend},
        )
    else:
        log_event(run_id, "decide_skipped_resumed", {})

    # Step 4: synthesis
    if "synthesize" not in completed:
        t0 = time.time()
        try:
            log.info("[%s] step 4 synthesize: building prompt", run_id[:8])
            synthesis = synthesize_triage_detailed(
                query, data["chunks"], data["graph_facts"]
            )
            log.info(
                "[%s] step 4 synthesize done: prompt=%d chars answer=%d chars in %.3fs",
                run_id[:8], len(synthesis["prompt"]), len(synthesis["answer"]),
                time.time() - t0,
            )
            payload(log, "prompt", synthesis["prompt"])
        except Exception as e:
            # Record why the run ended before re-raising, so the audit
            # trail shows the failure rather than simply stopping.
            log_event(
                run_id, "synthesize_failed",
                {"duration_s": round(time.time() - t0, 3), "error": str(e)},
            )
            raise
        answer = synthesis["answer"]
        data["answer"] = answer
        # The exact text the model was given, so the UI can show what
        # context actually reached it.
        data["prompt"] = synthesis["prompt"]
        _emit(on_step, "synthesize", {"answer": answer, "prompt": synthesis["prompt"]})
        completed.add("synthesize")
        checkpoint = {"run_id": run_id, "completed_steps": list(completed), "data": data}
        _save_checkpoint(run_id, checkpoint)
        log_event(run_id, "synthesize", {"duration_s": round(time.time() - t0, 3)})
    else:
        log_event(run_id, "synthesize_skipped_resumed", {})

    log_event(run_id, "run_completed", {})
    return {"run_id": run_id, **data}
