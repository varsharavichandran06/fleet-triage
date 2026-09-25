"""
Thin LLM wrapper. Uses the OpenAI client pointed at LLM_BASE_URL, so it
works against OpenAI itself or any OpenAI-compatible endpoint (a local
vLLM server, for example). Swap this file for the anthropic SDK if you
would rather use Claude, nothing else in the project needs to change.
"""

import json
import logging
import re
import sys
from typing import List, Dict

from openai import OpenAI

from app.config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, DRY_RUN

log = logging.getLogger(__name__)

_client = None


class LLMError(RuntimeError):
    """
    Raised when the LLM endpoint cannot be reached or refuses the call.

    Fatal rather than falling through to the fallback extraction, so an
    unreachable or misconfigured client cannot be mistaken for a
    successful run that quietly stored fallback output.
    """


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY or "unused")
    return _client


TRIPLE_PROMPT = """Extract factual (subject, relation, object) triples from \
the text below. Focus on causes, fixes, and part relationships. Return \
ONLY a JSON array of objects with keys "subject", "relation", "object". \
Keep each field short, a few words at most. If there is nothing worth \
extracting, return an empty array.

Title: {title}
Text: {body}
"""


def extract_triples(title: str, body: str) -> List[Dict[str, str]]:
    if DRY_RUN:
        return _fallback_triples(title, body)
    try:
        resp = _get_client().chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": TRIPLE_PROMPT.format(title=title, body=body)}],
            temperature=0,
        )
    except Exception as e:
        # A bad key, an unreachable endpoint, or an incompatible client is
        # an outage, not a per document quirk. Fail the indexing run
        # loudly instead of silently storing fallback garbage.
        raise LLMError(
            f"LLM call failed while extracting triples from {title!r}: {e}"
        ) from e

    text = (resp.choices[0].message.content or "").strip()
    log.debug("llm triples: title=%r response_chars=%d", title, len(text))
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        # The endpoint answered, the model just ignored the output format.
        # That is one bad document, not an outage, so skip it and carry on.
        print(
            f"WARNING: no JSON array in triple response for {title!r}, skipping document",
            file=sys.stderr,
        )
        return []
    try:
        triples = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        print(
            f"WARNING: malformed JSON in triple response for {title!r} ({e}), skipping document",
            file=sys.stderr,
        )
        return []
    return [t for t in triples if all(k in t for k in ("subject", "relation", "object"))]


def _fallback_triples(title: str, body: str) -> List[Dict[str, str]]:
    """
    Keyword based extraction used only when DRY_RUN is set, to keep the
    graph non empty for local testing without an API key. Never used to
    cover a failed LLM call; those raise LLMError.
    """
    triples = []
    lower = body.lower()
    if "root cause" in lower and "fix" in lower:
        cause_part = lower.split("root cause:")[-1].split("fix:")[0]
        fix_part = lower.split("fix:")[-1]
        subject = title.lower()
        triples.append({"subject": subject, "relation": "has_root_cause", "object": cause_part.strip()[:60]})
        triples.append({"subject": subject, "relation": "has_fix", "object": fix_part.strip()[:60]})
    return triples


TRIAGE_PROMPT = """You are a hardware validation triage assistant. A new \
test failure needs to be classified against known issues.

New failure report:
{query}

Retrieved similar reports and known issues (ranked by relevance):
{chunks}

Related facts from the knowledge graph:
{graph_facts}

Respond with a short triage recommendation: is this a known issue (name \
which one if so), a new issue worth escalating, or a likely test rig \
artifact. Give your reasoning in 3 to 5 sentences, referencing the \
specific evidence above.
"""


def build_triage_prompt(
    query: str, chunks: List[dict], graph_facts: List[dict]
) -> str:
    """
    Assembles the exact string the model is asked to answer.

    Split out so callers receive the real prompt rather than a
    reconstruction, which would drift as either one changed.
    """
    chunks_text = "\n".join(f"- ({c['doc_title']}) {c['text']}" for c in chunks) or "none"
    facts_text = (
        "\n".join(f"- {f['subject']} {f['relation']} {f['object']}" for f in graph_facts)
        or "none"
    )
    return TRIAGE_PROMPT.format(
        query=query, chunks=chunks_text, graph_facts=facts_text
    )


def synthesize_triage_detailed(
    query: str, chunks: List[dict], graph_facts: List[dict]
) -> Dict[str, str]:
    """
    Returns {"answer", "prompt"}. The prompt is included so callers can
    see exactly what context the model was given.
    """
    prompt = build_triage_prompt(query, chunks, graph_facts)

    if DRY_RUN:
        return {"answer": _fallback_triage(query, chunks, graph_facts), "prompt": prompt}

    try:
        resp = _get_client().chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
    except Exception as e:
        raise LLMError(f"LLM call failed during triage synthesis: {e}") from e

    return {"answer": resp.choices[0].message.content.strip(), "prompt": prompt}


def synthesize_triage(query: str, chunks: List[dict], graph_facts: List[dict]) -> str:
    """Thin wrapper kept for callers that only want the answer text."""
    return synthesize_triage_detailed(query, chunks, graph_facts)["answer"]


def _fallback_triage(query: str, chunks: List[dict], graph_facts: List[dict]) -> str:
    if not chunks:
        return "No similar reports found in the knowledge base. Recommend manual review."
    top = chunks[0]
    facts_line = f" Related known facts: {graph_facts[0]}" if graph_facts else ""
    return (
        f"[DRY_RUN, no live LLM call] Closest match is '{top['doc_title']}' "
        f"with rerank score {top['rerank_score']}.{facts_line} "
        f"Recommend comparing symptoms against this report before escalating."
    )
