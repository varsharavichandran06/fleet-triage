"""
The triage verdict as a typed decision rather than prose.

Returns the verdict as a typed value with calibrated probabilities rather
than as prose, so it can be routed on, thresholded, and measured against
ground truth.

Two backends behind one interface:

  jev  - TypeSafe's System One model, reached through OpenRouter. Purpose
         built for exactly this shape of problem: unstructured state in,
         typed probabilistic decision out, in a few hundred milliseconds.
  llm  - The ordinary chat model with a constrained JSON contract, used
         when no OpenRouter key is configured. Same output shape, slower,
         and the validity of the output is checked rather than guaranteed.

The split is the point. Deciding *what kind* of failure this is is a fast
classification over evidence already gathered; explaining *why* is the
slow language task. They do not need the same model.
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from app.logging_setup import payload

log = logging.getLogger(__name__)

from app.config import (
    JEV_TIMEOUT_S,
    JEV_MAX_RETRIES,
    OPENROUTER_API_KEY,
    JEV_BASE_URL,
    JEV_MODEL,
    VERDICTS,
    LLM_MODEL,
    DRY_RUN,
)


@dataclass
class Verdict:
    verdict: str
    confidence: float
    probabilities: dict = field(default_factory=dict)
    # Probability that the node should be pulled from the scheduler. The
    # expensive mistake in fleet triage is draining a healthy node for a
    # fault that lives in the workload, so this is worth its own signal.
    drain_node: Optional[float] = None
    severity: Optional[float] = None
    backend: str = "none"
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    # Exactly what was sent and what came back. Carried through to the UI
    # because a decision model is only auditable if you can see the state
    # it judged and the distribution it returned, not just the verdict.
    request_state: dict = field(default_factory=dict)
    request_questions: dict = field(default_factory=dict)
    raw_answers: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _state(query: str, chunks: List[dict], graph_facts: List[dict]) -> dict:
    """
    The evidence the decision is made against. Passing the retrieved
    chunks and graph facts rather than the bare report is the whole
    reason the retrieval pipeline exists: without them the classifier is
    working from priors alone and gets ambiguous cases wrong.
    """
    return {
        "failure_report": query,
        "retrieved_evidence": [
            {"source": c.get("doc_title", ""), "text": c.get("text", "")}
            for c in chunks
        ],
        "knowledge_graph_facts": [
            f"{f.get('subject')} {f.get('relation')} {f.get('object')}"
            for f in graph_facts
        ],
    }


def _decide_with_jev(state: dict) -> Verdict:
    import time

    from typesafe_sdk import (
        Choice, Noul, NoulCriteria, RetryPolicy, Score, TypeSafeClient,
    )

    # Bounded. The model normally answers in 240-370ms, but a provider tail
    # event was measured at 10.8s against an unbounded client, which the
    # caller absorbed in full. A timeout plus retry converts that into either
    # a fast recovery or a fast, explicit failure.
    client = TypeSafeClient(
        api_key=OPENROUTER_API_KEY,
        base_url=JEV_BASE_URL,
        timeout=JEV_TIMEOUT_S,
        retry=RetryPolicy(max_retries=JEV_MAX_RETRIES),
    )

    # Plain dict mirror of the questions, built alongside the SDK objects so
    # the UI can render precisely what was asked without reaching into the
    # SDK's internals.
    questions_repr = {
        "verdict": {
            "type": "choice",
            "instructions": "Classify the failure report against the retrieved evidence.",
            "criteria": dict(VERDICTS),
        },
        "drain_node": {
            "type": "noul",
            "instructions": "Should the affected node be drained from the scheduler?",
            "criteria": {
                "true": "A hardware fault is likely, so draining prevents further job loss",
                "false": "The fault is in the workload or the test setup, so draining wastes capacity",
            },
        },
        "severity": {
            "type": "score",
            "instructions": "How urgent is this failure for the fleet?",
            "criteria": ["Low", "Medium", "High", "Critical"],
        },
    }

    questions = {
        "verdict": Choice(
            instructions=(
                "Classify the failure report against the retrieved evidence. "
                "Prefer known_issue only when the evidence actually documents "
                "this failure mode."
            ),
            criteria=dict(VERDICTS),
        ),
        "drain_node": Noul(
            instructions="Should the affected node be drained from the scheduler?",
            criteria=NoulCriteria(
                true="A hardware fault is likely, so draining prevents further job loss",
                false="The fault is in the workload or the test setup, so draining wastes capacity",
            ),
        ),
        "severity": Score(
            instructions="How urgent is this failure for the fleet?",
            criteria=["Low", "Medium", "High", "Critical"],
        ),
    }

    log.debug(
        "jev request: model=%s timeout=%ss retries=%s state_bytes=%d questions=%s",
        JEV_MODEL, JEV_TIMEOUT_S, JEV_MAX_RETRIES,
        len(json.dumps(state)), list(questions_repr),
    )
    payload(log, "jev state", state)

    t0 = time.time()
    response = client.system_one(state, questions, model=JEV_MODEL)
    latency = (time.time() - t0) * 1000
    if latency > JEV_TIMEOUT_S * 400:
        log.warning("jev slow response: %.0fms (model=%s)", latency, JEV_MODEL)

    v = response.answers["verdict"]

    raw = {}
    for name, ans in response.answers.items():
        entry = {"type": getattr(ans, "type", None)}
        for attr in ("choice", "noul", "score", "confidence", "probabilities"):
            val = getattr(ans, attr, None)
            if val is not None:
                entry[attr] = dict(val) if isinstance(val, dict) else val
        raw[name] = entry

    return Verdict(
        verdict=v.choice,
        confidence=float(getattr(v, "confidence", 0.0) or 0.0),
        probabilities=dict(getattr(v, "probabilities", {}) or {}),
        drain_node=float(response.answers["drain_node"].noul),
        severity=float(response.answers["severity"].score),
        backend=f"jev:{JEV_MODEL}",
        latency_ms=round(latency, 1),
        request_state=state,
        request_questions=questions_repr,
        raw_answers=raw,
    )


_LLM_PROMPT = """You are triaging a hardware failure report against retrieved evidence.

Failure report:
{report}

Retrieved evidence:
{evidence}

Knowledge graph facts:
{facts}

Choose exactly one verdict from:
{options}

Respond with ONLY a JSON object, no prose:
{{"verdict": "<one of the options>", "confidence": <0..1>, "drain_node": <0..1>, "severity": <0..1>}}
"""


def _decide_with_llm(state: dict) -> Verdict:
    import time

    from app.llm import _get_client

    evidence = (
        "\n".join(f"- ({c['source']}) {c['text']}" for c in state["retrieved_evidence"])
        or "none"
    )
    facts = "\n".join(f"- {f}" for f in state["knowledge_graph_facts"]) or "none"
    options = "\n".join(f"- {k}: {v}" for k, v in VERDICTS.items())

    prompt = _LLM_PROMPT.format(
        report=state["failure_report"], evidence=evidence, facts=facts, options=options
    )

    t0 = time.time()
    resp = _get_client().chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    latency = (time.time() - t0) * 1000
    text = (resp.choices[0].message.content or "").strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return Verdict(
            verdict="new_issue", confidence=0.0, backend=f"llm:{LLM_MODEL}",
            latency_ms=round(latency, 1), error="no JSON object in response",
        )
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        return Verdict(
            verdict="new_issue", confidence=0.0, backend=f"llm:{LLM_MODEL}",
            latency_ms=round(latency, 1), error=f"malformed JSON: {e}",
        )

    verdict = str(data.get("verdict", "")).strip()
    error = None
    if verdict not in VERDICTS:
        # The contract is checked, not guaranteed. This is the difference
        # between a constrained decoder and a typed decision model.
        error = f"model returned an off-contract verdict: {verdict!r}"
        verdict = "new_issue"

    def num(key):
        try:
            return float(data[key])
        except (KeyError, TypeError, ValueError):
            return None

    return Verdict(
        verdict=verdict,
        confidence=num("confidence") or 0.0,
        probabilities={},
        drain_node=num("drain_node"),
        severity=num("severity"),
        backend=f"llm:{LLM_MODEL}",
        latency_ms=round(latency, 1),
        error=error,
        request_state=state,
        request_questions={"prompt": prompt},
        raw_answers=data,
    )


def classify(query: str, chunks: List[dict], graph_facts: List[dict]) -> Verdict:
    """
    Returns a typed verdict for the report, given the evidence retrieved
    for it. Uses Jev when an OpenRouter key is configured, otherwise the
    chat model with a checked JSON contract.
    """
    if DRY_RUN:
        log.info("decide: DRY_RUN, returning a canned verdict")
        return Verdict(verdict="new_issue", confidence=0.0, backend="dry_run")

    state = _state(query, chunks, graph_facts)
    if OPENROUTER_API_KEY:
        try:
            v = _decide_with_jev(state)
            log.info(
                "decide: %s confidence=%.2f drain=%s severity=%s via %s in %.0fms",
                v.verdict, v.confidence, v.drain_node, v.severity,
                v.backend, v.latency_ms or 0,
            )
            payload(log, "jev answers", v.raw_answers)
            return v
        except Exception as e:
            # A decision model being unreachable should degrade to the
            # slower path rather than kill a triage run, unlike the
            # synthesis call which has no sensible substitute.
            log.warning("decide: jev unavailable (%s), falling back to the chat model", e)
            fallback = _decide_with_llm(state)
            fallback.error = f"jev unavailable ({e}), fell back to the chat model"
            return fallback
    log.info("decide: no OpenRouter key set, using the chat model")
    return _decide_with_llm(state)
