"""
Hybrid retrieval: dense vector search (Chroma, embedded, no server needed)
merged with sparse keyword search (BM25), then reranked with a cross
encoder. This is the classic three stage RAG retrieval pattern: recall
broadly with two different signals, then spend a heavier model narrowing
down to the few chunks that actually matter.
"""

import logging
import os
from typing import List, Dict
from functools import lru_cache

import chromadb
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer, CrossEncoder

from app.config import (
    CHROMA_DIR,
    EMBEDDING_MODEL,
    RERANKER_MODEL,
    TOP_K_CANDIDATES,
    TOP_K_FINAL,
    MIN_RERANK_SCORE,
    RRF_K,
)
from app.chunking import load_all_chunks, Chunk

log = logging.getLogger(__name__)

COLLECTION_NAME = "failure_docs"


@lru_cache(maxsize=1)
def _embedder() -> SentenceTransformer:
    return SentenceTransformer(EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def _reranker() -> CrossEncoder:
    return CrossEncoder(RERANKER_MODEL)


@lru_cache(maxsize=1)
def _chroma_collection():
    os.makedirs(CHROMA_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_or_create_collection(COLLECTION_NAME)


@lru_cache(maxsize=1)
def _bm25_index():
    """
    Returns (BM25Okapi, list[Chunk]) built once and cached. Kept in
    lockstep with the Chroma index by build_index() below, both are
    built from the same load_all_chunks() call.
    """
    chunks = load_all_chunks()
    tokenized = [c.text.lower().split() for c in chunks]
    return BM25Okapi(tokenized), chunks


def build_index() -> int:
    """
    Rebuilds both the vector index and the BM25 index from the documents
    on disk. Call this once after adding or changing documents in
    data/docs. Returns the number of chunks indexed.
    """
    chunks = load_all_chunks()
    if not chunks:
        return 0

    collection = _chroma_collection()
    # Chroma has no "clear collection" primitive on the base API, so we
    # delete and recreate to guarantee a clean rebuild instead of stale
    # duplicate entries from a previous run.
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.get_or_create_collection(COLLECTION_NAME)
    _chroma_collection.cache_clear()

    embedder = _embedder()
    texts = [c.text for c in chunks]
    embeddings = embedder.encode(texts, show_progress_bar=False).tolist()

    collection.add(
        ids=[c.chunk_id for c in chunks],
        embeddings=embeddings,
        documents=texts,
        metadatas=[{"doc_id": c.doc_id, "doc_title": c.doc_title} for c in chunks],
    )

    # Force BM25 to rebuild against the same chunk set on next access.
    _bm25_index.cache_clear()
    return len(chunks)


def _vector_search(query: str, k: int) -> Dict[str, float]:
    collection = _chroma_collection()
    q_emb = _embedder().encode([query], show_progress_bar=False).tolist()
    results = collection.query(query_embeddings=q_emb, n_results=k)
    scores = {}
    if not results["ids"] or not results["ids"][0]:
        return scores
    # Chroma returns squared L2 distance by default, smaller is better.
    # Convert to a similarity-style score so it merges cleanly with BM25.
    for chunk_id, distance in zip(results["ids"][0], results["distances"][0]):
        scores[chunk_id] = 1.0 / (1.0 + distance)
    return scores


def _keyword_search(query: str, k: int) -> Dict[str, float]:
    bm25, chunks = _bm25_index()
    tokenized_query = query.lower().split()
    scores = bm25.get_scores(tokenized_query)
    ranked = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)[:k]
    return {c.chunk_id: float(s) for c, s in ranked if s > 0}


def _normalize(scores: Dict[str, float]) -> Dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def _normalize_max(scores: Dict[str, float]) -> Dict[str, float]:
    """
    Scales scores against the best one in the set, for display.

    Divides by the maximum rather than applying min max normalisation.
    Min max maps the weakest member to exactly 0.0, which is then
    indistinguishable from a chunk the signal never returned at all.
    """
    if not scores:
        return {}
    hi = max(scores.values())
    if hi <= 0:
        return {k: 0.0 for k in scores}
    return {k: v / hi for k, v in scores.items()}


def hybrid_search_detailed(query: str, top_k_final: int = TOP_K_FINAL):
    """
    Hybrid retrieval with Reciprocal Rank Fusion, cross encoder reranking,
    and one chunk per document in the final context. Also returns a stage
    by stage record of how the ranking was reached.

    Fusion is by rank rather than by score: vector similarity and BM25 sit
    on incomparable scales, and normalising them onto a shared range
    floors the weakest match in each signal to zero, which is then
    indistinguishable from no match at all.

    Returns (final_chunks, stages).
    """
    _, chunks = _bm25_index()
    chunk_by_id = {c.chunk_id: c for c in chunks}

    raw_vec = _vector_search(query, TOP_K_CANDIDATES)
    raw_kw = _keyword_search(query, TOP_K_CANDIDATES)

    # Rank within each signal, 1 based. Only chunks a signal actually
    # returned get a rank there, which is what keeps "ranked last" and
    # "never returned" distinct.
    vec_rank = {
        cid: i + 1
        for i, cid in enumerate(sorted(raw_vec, key=raw_vec.get, reverse=True))
    }
    kw_rank = {
        cid: i + 1
        for i, cid in enumerate(sorted(raw_kw, key=raw_kw.get, reverse=True))
    }

    combined_ids = set(vec_rank) | set(kw_rank)
    fused = {}
    for cid in combined_ids:
        score = 0.0
        if cid in vec_rank:
            score += 1.0 / (RRF_K + vec_rank[cid])
        if cid in kw_rank:
            score += 1.0 / (RRF_K + kw_rank[cid])
        fused[cid] = score

    # Display only, so the UI can show each signal's contribution honestly.
    vec_display = _normalize_max(raw_vec)
    kw_display = _normalize_max(raw_kw)

    log.debug(
        "retrieval: vector=%d keyword=%d both=%d pool=%d",
        len(raw_vec), len(raw_kw), len(set(vec_rank) & set(kw_rank)), len(combined_ids),
    )
    # Plain rank cap. RRF is ordinal, so the only meaningful operation on
    # its output is to take the top n. A score threshold here would gate on
    # how many signals agreed rather than on relevance.
    shortlist_ids = sorted(fused, key=fused.get, reverse=True)[:TOP_K_CANDIDATES]

    stages = {
        "total_chunks": len(chunks),
        "vector_hits": len(raw_vec),
        "keyword_hits": len(raw_kw),
        "both_signals": len(set(vec_rank) & set(kw_rank)),
        "pool_size": len(combined_ids),
        "fusion": "rrf",
        "rrf_k": RRF_K,
        "min_rerank_score": MIN_RERANK_SCORE,
        "top_k_candidates": TOP_K_CANDIDATES,
        "top_k_final": top_k_final,
        "shortlist_size": len(shortlist_ids),
        "dropped_below_rerank": 0,
        "dropped_duplicate_doc": 0,
        "dropped_over_cap": 0,
        "candidates": [],
    }

    if not shortlist_ids:
        return [], stages

    shortlist_chunks = [chunk_by_id[cid] for cid in shortlist_ids]
    pairs = [(query, c.text) for c in shortlist_chunks]
    rerank_scores = _reranker().predict(pairs, show_progress_bar=False).tolist()

    ranked = sorted(
        zip(shortlist_chunks, rerank_scores), key=lambda x: x[1], reverse=True
    )

    # Final selection: relevance gate, then one chunk per document, then the
    # cap. Recording why each candidate lost keeps the UI honest.
    reasons: dict = {}
    seen_docs: set = set()
    final: list = []
    for c, score in ranked:
        if float(score) < MIN_RERANK_SCORE:
            reasons[c.chunk_id] = "below_rerank_threshold"
            stages["dropped_below_rerank"] += 1
            continue
        if c.doc_id in seen_docs:
            reasons[c.chunk_id] = "duplicate_doc"
            stages["dropped_duplicate_doc"] += 1
            continue
        if len(final) >= top_k_final:
            reasons[c.chunk_id] = "over_cap"
            stages["dropped_over_cap"] += 1
            continue
        seen_docs.add(c.doc_id)
        final.append((c, score))

    kept_ids = {c.chunk_id for c, _ in final}
    log.debug(
        "retrieval: reranked %d, kept %d (below_threshold=%d duplicate_doc=%d over_cap=%d)",
        len(ranked), len(final), stages["dropped_below_rerank"],
        stages["dropped_duplicate_doc"], stages["dropped_over_cap"],
    )
    merged_rank = {cid: i + 1 for i, cid in enumerate(shortlist_ids)}
    rerank_rank = {c.chunk_id: i + 1 for i, (c, _) in enumerate(ranked)}

    stages["candidates"] = [
        {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "doc_title": c.doc_title,
            "text": c.text,
            # Scaled against the best hit in that signal. None means the
            # signal never returned this chunk, which is not the same thing
            # as returning it last.
            "vector_score": (
                round(vec_display[c.chunk_id], 4) if c.chunk_id in vec_display else None
            ),
            "keyword_score": (
                round(kw_display[c.chunk_id], 4) if c.chunk_id in kw_display else None
            ),
            "vector_rank": vec_rank.get(c.chunk_id),
            "keyword_rank": kw_rank.get(c.chunk_id),
            "in_vector": c.chunk_id in vec_rank,
            "in_keyword": c.chunk_id in kw_rank,
            "merged_score": round(fused.get(c.chunk_id, 0.0), 6),
            "merged_rank": merged_rank[c.chunk_id],
            "rerank_score": round(float(s), 4),
            "rerank_rank": rerank_rank[c.chunk_id],
            # positive means the cross encoder promoted this chunk
            "rank_delta": merged_rank[c.chunk_id] - rerank_rank[c.chunk_id],
            "kept": c.chunk_id in kept_ids,
            "drop_reason": reasons.get(c.chunk_id),
        }
        for c, s in ranked
    ]

    final_chunks = [
        {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "doc_title": c.doc_title,
            "text": c.text,
            "vector_score": (
                round(vec_display[c.chunk_id], 4) if c.chunk_id in vec_display else None
            ),
            "keyword_score": (
                round(kw_display[c.chunk_id], 4) if c.chunk_id in kw_display else None
            ),
            "rerank_score": round(float(s), 4),
        }
        for c, s in final
    ]
    return final_chunks, stages


def hybrid_search(query: str, top_k_final: int = TOP_K_FINAL) -> List[dict]:
    """Thin wrapper kept for callers that only want the final chunks."""
    final, _ = hybrid_search_detailed(query, top_k_final)
    return final
