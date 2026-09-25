const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Chunk = {
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  text: string;
  // null means the signal never returned this chunk at all, which is not
  // the same as returning it ranked last.
  vector_score: number | null;
  keyword_score: number | null;
  rerank_score: number;
};

// One shortlist candidate, carrying its score from every signal and its
// rank both before and after the cross encoder, so the UI can show what
// the reranker actually changed.
export type Candidate = {
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  text: string;
  vector_score: number | null;
  keyword_score: number | null;
  vector_rank: number | null;
  keyword_rank: number | null;
  in_vector: boolean;
  in_keyword: boolean;
  // Reciprocal Rank Fusion score, so a small number where bigger is better.
  // Not comparable across queries, only within one.
  merged_score: number;
  merged_rank: number;
  rerank_score: number;
  rerank_rank: number;
  rank_delta: number;
  kept: boolean;
  // Why this candidate did not reach the final context, null if it did.
  drop_reason: "below_rerank_threshold" | "duplicate_doc" | "over_cap" | null;
};

export type RetrievalStages = {
  total_chunks: number;
  vector_hits: number;
  keyword_hits: number;
  both_signals: number;
  pool_size: number;
  fusion: string;
  rrf_k: number;
  // The only relevance gate. Cross encoder scores are absolute and
  // comparable across queries, unlike the fused RRF score, which is ordinal
  // and carries no threshold meaning.
  min_rerank_score: number;
  top_k_candidates: number;
  top_k_final: number;
  shortlist_size: number;
  dropped_below_rerank: number;
  dropped_duplicate_doc: number;
  dropped_over_cap: number;
  candidates: Candidate[];
};

export type GraphFact = {
  subject: string;
  relation: string;
  object: string;
  source_doc: string;
  // 1 = stated directly about an entity in the query, 2 = reached by
  // walking one step further out.
  hop_distance: number;
};

// The typed decision from the System One model. Carries the request that
// produced it so the UI can show the mechanism, not just the conclusion.
export type Verdict = {
  verdict: string;
  confidence: number;
  probabilities: Record<string, number>;
  drain_node: number | null;
  severity: number | null;
  backend: string;
  latency_ms: number | null;
  error: string | null;
  request_state: {
    failure_report?: string;
    retrieved_evidence?: { source: string; text: string }[];
    knowledge_graph_facts?: string[];
  };
  request_questions: Record<string, unknown>;
  raw_answers: Record<string, unknown>;
};

export type TriageResult = {
  run_id: string;
  query: string;
  chunks: Chunk[];
  graph_entities: string[];
  graph_facts: GraphFact[];
  answer: string;
  retrieval_stages: RetrievalStages;
  // Optional: a run resumed from a checkpoint written before the decide
  // step existed will not have one.
  verdict?: Verdict;
  // The exact text handed to the model. Optional because a run resumed
  // from a checkpoint written by an older build will not have it.
  prompt?: string;
};

export type AuditEvent = {
  timestamp: number;
  run_id: string;
  step: string;
  data: Record<string, unknown>;
};

export async function runTriage(query: string, runId?: string): Promise<TriageResult> {
  const res = await fetch(`${API_BASE}/triage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, run_id: runId }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Triage request failed (${res.status}): ${body}`);
  }
  return res.json();
}

export async function getAuditTrail(runId: string): Promise<AuditEvent[]> {
  const res = await fetch(`${API_BASE}/runs/${runId}/audit`);
  if (!res.ok) {
    throw new Error(`Failed to fetch audit trail (${res.status})`);
  }
  return res.json();
}

export type StreamStep = "retrieve" | "graph" | "decide" | "synthesize";

type StreamHandlers = {
  onStep: (step: StreamStep, data: Partial<TriageResult>) => void;
  onDone: (result: TriageResult) => void;
};

/**
 * Runs a triage over Server-Sent Events, reporting each pipeline step as it
 * completes so the caller can render partial results rather than waiting for
 * the whole run.
 *
 * Parses the SSE framing by hand because EventSource cannot issue a POST.
 */
export async function runTriageStream(
  query: string,
  { onStep, onDone }: StreamHandlers,
  runId?: string
): Promise<void> {
  const res = await fetch(`${API_BASE}/triage/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, run_id: runId }),
  });
  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => "");
    throw new Error(`Triage request failed (${res.status}): ${body}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line; the last piece may be partial.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let event = "";
      let payload = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) payload += line.slice(5).trim();
      }
      if (!payload) continue;

      const parsed = JSON.parse(payload);
      if (event === "step") {
        const { step, ...data } = parsed;
        onStep(step as StreamStep, data as Partial<TriageResult>);
      } else if (event === "done") {
        onDone(parsed as TriageResult);
      } else if (event === "error") {
        throw new Error(parsed.error ?? "Triage failed");
      }
    }
  }
}
