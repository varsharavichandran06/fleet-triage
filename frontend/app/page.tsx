"use client";

import { useState } from "react";
import {
  runTriageStream,
  getAuditTrail,
  type TriageResult,
  type AuditEvent,
  type StreamStep,
} from "@/lib/api";
import Funnel from "@/components/Funnel";
import RerankFlow from "@/components/RerankFlow";
import GraphView from "@/components/GraphView";
import ContextPanel from "@/components/ContextPanel";
import VerdictPanel from "@/components/VerdictPanel";

function ScoreBar({
  label,
  value,
  rank,
  tone = "bg-neutral-800",
}: {
  label: string;
  // null means this signal never returned the chunk. Rendering that as a
  // zero length bar would read as "matched, badly", which is a different
  // claim entirely.
  value: number | null;
  rank?: number | null;
  tone?: string;
}) {
  if (value === null) {
    return (
      <div className="flex items-center gap-2 text-[10px] text-neutral-400">
        <span className="w-12 shrink-0">{label}</span>
        <div className="flex-1 border-t border-dashed border-neutral-300" />
        <span className="w-16 shrink-0 text-right italic">not returned</span>
      </div>
    );
  }
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="flex items-center gap-2 text-[10px] text-neutral-500">
      <span className="w-12 shrink-0">{label}</span>
      <div className="h-1.5 flex-1 rounded-full bg-neutral-200">
        <div
          className={`h-1.5 rounded-full ${tone}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-16 shrink-0 text-right tabular-nums">
        {value.toFixed(2)}
        {rank ? <span className="text-neutral-400"> #{rank}</span> : null}
      </span>
    </div>
  );
}

const STEP_LABELS: { key: StreamStep; label: string }[] = [
  { key: "retrieve", label: "Retrieval" },
  { key: "graph", label: "Graph" },
  { key: "decide", label: "Verdict" },
  { key: "synthesize", label: "Synthesis" },
];

function StepProgress({
  done,
  running,
}: {
  done: Set<StreamStep>;
  running: boolean;
}) {
  const next = STEP_LABELS.find((s) => !done.has(s.key));
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2">
      {STEP_LABELS.map((s) => {
        const isDone = done.has(s.key);
        const isActive = running && next?.key === s.key;
        return (
          <span
            key={s.key}
            className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition ${
              isDone
                ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                : isActive
                  ? "border-neutral-900 bg-white text-neutral-900"
                  : "border-neutral-200 bg-white text-neutral-400"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                isDone
                  ? "bg-emerald-500"
                  : isActive
                    ? "animate-pulse bg-neutral-900"
                    : "bg-neutral-300"
              }`}
            />
            {s.label}
          </span>
        );
      })}
    </div>
  );
}

function Pending({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-dashed border-neutral-300 bg-neutral-50 px-3 py-6 text-xs text-neutral-400">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-neutral-400" />
      {label}
    </div>
  );
}

function Section({
  step,
  title,
  subtitle,
  children,
}: {
  step: number;
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-start gap-3">
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-neutral-900 text-[11px] font-semibold text-white">
          {step}
        </span>
        <div>
          <h2 className="text-sm font-semibold text-neutral-800">{title}</h2>
          <p className="text-xs text-neutral-400">{subtitle}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

// Each sample is written to land on a different cluster in the corpus, so
// the pipeline can be exercised against a known issue, a near miss that
// looks like one, and a report with no matching known issue at all.
const SAMPLES = [
  {
    label: "Xid 48 double bit ECC",
    text: "A node logged Xid 48 on one GPU partway through a 512 rank pretraining run. The device dropped out of the collective and took the whole job with it. Do we reset it or pull it?",
  },
  {
    label: "Xid 13 — GPU or our code?",
    text: "Job crashed with Xid 13 on one node. On call wants to drain the node but the workload owner says the code is fine. Which is it?",
  },
  {
    label: "NCCL timeout",
    text: "Multi node run keeps hitting NCCL collective timeouts around the same step. We think the InfiniBand fabric is bad but the port counters look clean.",
  },
  {
    label: "Thermal throttling",
    text: "One GPU is throttling and reaching 95C under sustained load while the other devices in the same chassis stay near 70C. Is the GPU failing?",
  },
  {
    label: "Silent slowdown",
    text: "One node runs about 30 percent slower than identical nodes on the same job. Nothing is logged anywhere, no Xid and no errors at all.",
  },
];

export default function Home() {
  const [query, setQuery] = useState(SAMPLES[0].text);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Filled in step by step as the stream arrives, so sections can render
  // before the run has finished.
  const [result, setResult] = useState<Partial<TriageResult> | null>(null);
  const [done, setDone] = useState<Set<StreamStep>>(new Set());
  const [audit, setAudit] = useState<AuditEvent[]>([]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    setAudit([]);
    setDone(new Set());
    try {
      await runTriageStream(query, {
        onStep: (step, data) => {
          setResult((prev) => ({ ...(prev ?? {}), ...data }));
          setDone((prev) => new Set(prev).add(step));
        },
        onDone: async (full) => {
          setResult(full);
          const trail = await getAuditTrail(full.run_id);
          setAudit(trail);
        },
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  const stages = result?.retrieval_stages;
  // Shortlist in pre rerank order, which is what stage 2 is showing.
  const shortlist = stages
    ? [...stages.candidates].sort((a, b) => a.merged_rank - b.merged_rank)
    : [];

  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900">
      <header className="border-b border-neutral-200 bg-white px-6 py-4">
        <h1 className="text-lg font-semibold">fleet-triage</h1>
        <p className="text-sm text-neutral-500">
          Failure triage for GPU training clusters: hybrid retrieval, a
          knowledge graph, and a typed verdict
        </p>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        <form onSubmit={handleSubmit} className="mb-8 flex gap-3">
          <textarea
            className="h-24 flex-1 resize-none rounded-lg border border-neutral-300 bg-white p-3 text-sm shadow-sm focus:border-neutral-500 focus:outline-none"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Paste a new test failure report..."
          />
          <button
            type="submit"
            disabled={loading}
            className="h-24 rounded-lg bg-neutral-900 px-6 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-50"
          >
            {loading ? "Triaging..." : "Run triage"}
          </button>
        </form>

        <div className="-mt-5 mb-8 flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-neutral-400">Try:</span>
          {SAMPLES.map((s) => (
            <button
              key={s.label}
              type="button"
              onClick={() => setQuery(s.text)}
              className={`rounded-full border px-2.5 py-1 text-[11px] transition ${
                query === s.text
                  ? "border-neutral-900 bg-neutral-900 text-white"
                  : "border-neutral-300 bg-white text-neutral-600 hover:border-neutral-500"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>

        {error && (
          <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            {error}
            <div className="mt-1 text-xs text-red-500">
              Is the backend running at the URL in NEXT_PUBLIC_API_URL? Default
              is http://localhost:8000.
            </div>
          </div>
        )}

        {(loading || result) && (
          <StepProgress done={done} running={loading} />
        )}

        {result && (
          <div className="space-y-5">
            {done.has("retrieve") && stages && result.chunks?.length === 0 && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                <p className="text-sm font-medium text-amber-900">
                  No chunk cleared the relevance threshold
                </p>
                <p className="mt-1 text-xs text-amber-700">
                  {stages.shortlist_size} candidates were reranked and all
                  scored below {stages.min_rerank_score}, so the model was
                  given no evidence rather than the four least bad chunks.
                  That usually means the corpus has nothing on this topic.
                </p>
              </div>
            )}
            <Section
              step={1}
              title="Narrowing the corpus"
              subtitle="Two recall signals run in parallel, then the field is cut twice."
            >
              {stages ? (
                <Funnel stages={stages} />
              ) : (
                <Pending label="Searching the corpus..." />
              )}
            </Section>

            <Section
              step={2}
              title="Hybrid scoring"
              subtitle="Dense vector similarity and BM25 keyword overlap are fused by rank, not by score, because the two live on scales that cannot be compared directly."
            >
              {!stages && <Pending label="Scoring candidates..." />}
              <div className="space-y-2">
                {shortlist.map((c) => (
                  <div
                    key={c.chunk_id}
                    className="rounded-lg border border-neutral-200 bg-neutral-50 p-3"
                  >
                    <div className="mb-1.5 flex items-center gap-2">
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-neutral-200 text-[10px] font-semibold tabular-nums text-neutral-700">
                        {c.merged_rank}
                      </span>
                      <span className="truncate text-xs font-medium text-neutral-800">
                        {c.doc_title}
                      </span>
                    </div>
                    <p className="mb-2 line-clamp-2 text-[11px] text-neutral-500">
                      {c.text}
                    </p>
                    <div className="space-y-1">
                      <ScoreBar
                        label="vector"
                        value={c.vector_score}
                        rank={c.vector_rank}
                        tone="bg-sky-500"
                      />
                      <ScoreBar
                        label="keyword"
                        value={c.keyword_score}
                        rank={c.keyword_rank}
                        tone="bg-amber-500"
                      />
                      {/* RRF scores are tiny and only meaningful relative to
                          each other, so show the fused rank rather than a bar
                          scaled against a meaningless maximum. */}
                      <div className="flex items-center gap-2 text-[10px] text-neutral-500">
                        <span className="w-12 shrink-0">fused</span>
                        <div className="flex-1 border-t border-neutral-200" />
                        <span className="w-16 shrink-0 text-right tabular-nums">
                          #{c.merged_rank}
                          <span className="text-neutral-400">
                            {" "}
                            {c.merged_score.toFixed(4)}
                          </span>
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Section>

            <Section
              step={3}
              title="Reranking"
              subtitle="A cross encoder reads each candidate against the query together, rather than comparing pre computed vectors, and reorders them."
            >
              {stages ? (
                <RerankFlow stages={stages} />
              ) : (
                <Pending label="Waiting on the cross encoder..." />
              )}
            </Section>

            <Section
              step={4}
              title="Knowledge graph context"
              subtitle="Entities mentioned in the query are matched against the graph, then their relations are pulled in."
            >
              {!done.has("graph") && <Pending label="Walking the graph..." />}
              <div className="mb-3 flex flex-wrap gap-1.5">
                {(result.graph_entities ?? []).map((e) => (
                  <span
                    key={e}
                    className="rounded-full bg-neutral-900 px-2.5 py-0.5 text-[11px] text-white"
                  >
                    {e}
                  </span>
                ))}
                {done.has("graph") && result.graph_entities?.length === 0 && (
                  <span className="text-xs text-neutral-400">
                    No known entities matched in the query.
                  </span>
                )}
              </div>
              {done.has("graph") && (
                <GraphView
                  facts={result.graph_facts ?? []}
                  entities={result.graph_entities ?? []}
                />
              )}
            </Section>

            {result.verdict && (
              <Section
                step={5}
                title="Typed verdict"
                subtitle="A System One model classifies the failure from the evidence above, returning a typed decision with calibrated probabilities rather than prose."
              >
                <VerdictPanel v={result.verdict} />
              </Section>
            )}

            <Section
              step={6}
              title="Context handed to the model"
              subtitle="Everything above, flattened into a single prompt."
            >
              {done.has("synthesize") ? (
                <ContextPanel
                  prompt={result.prompt}
                  nChunks={result.chunks?.length ?? 0}
                  nFacts={result.graph_facts?.length ?? 0}
                />
              ) : (
                <Pending label="Assembling the prompt..." />
              )}
            </Section>

            <Section
              step={7}
              title="Triage recommendation"
              subtitle="The language model explains the reasoning. The verdict above decided what kind of failure it is; this says why."
            >
              {result.answer ? (
                <p className="whitespace-pre-wrap rounded-lg bg-neutral-900 p-4 text-sm leading-relaxed text-neutral-50">
                  {result.answer}
                </p>
              ) : (
                <Pending label="Writing the recommendation..." />
              )}
            </Section>

            <section className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
              <h2 className="mb-1 text-sm font-semibold text-neutral-700">
                Audit trail
              </h2>
              <p className="mb-3 text-xs text-neutral-400">
                run_id: {result.run_id ?? "pending"}
              </p>
              <ol className="relative space-y-4 border-l border-neutral-200 pl-4">
                {audit.map((e, i) => (
                  <li key={i} className="relative">
                    <span className="absolute -left-[21px] top-1 h-2 w-2 rounded-full bg-neutral-900" />
                    <div className="text-xs font-medium">{e.step}</div>
                    <div className="text-[10px] text-neutral-400">
                      {new Date(e.timestamp * 1000).toLocaleTimeString()}
                    </div>
                    {Object.keys(e.data).length > 0 && (
                      <pre className="mt-1 overflow-x-auto rounded bg-neutral-50 p-1.5 text-[10px] text-neutral-600">
                        {JSON.stringify(e.data, null, 0)}
                      </pre>
                    )}
                  </li>
                ))}
              </ol>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
