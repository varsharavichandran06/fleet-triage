"use client";

import { useState } from "react";
import type { Candidate, RetrievalStages } from "@/lib/api";

// Fixed row geometry so the connector SVG can be drawn from arithmetic
// instead of measuring the DOM after layout.
const ROW_H = 68;
const GAP = 10;
const PITCH = ROW_H + GAP;
const LANE_W = 78;

// Why a reranked candidate did not make it into the final context.
const REASONS: Record<string, { label: string; cls: string }> = {
  below_rerank_threshold: {
    label: "below threshold",
    cls: "bg-rose-100 text-rose-700",
  },
  duplicate_doc: {
    label: "same document",
    cls: "bg-amber-100 text-amber-700",
  },
  over_cap: { label: "over cap", cls: "bg-neutral-200 text-neutral-600" },
};

function toneFor(delta: number) {
  if (delta > 0) return { stroke: "#10b981", text: "text-emerald-600", bg: "bg-emerald-50" };
  if (delta < 0) return { stroke: "#f43f5e", text: "text-rose-600", bg: "bg-rose-50" };
  return { stroke: "#a3a3a3", text: "text-neutral-400", bg: "bg-neutral-100" };
}

function Card({
  c,
  rank,
  score,
  scoreLabel,
  dimmed,
  active,
  reason,
  onHover,
}: {
  c: Candidate;
  rank: number;
  score: number;
  scoreLabel: string;
  dimmed: boolean;
  active: boolean;
  reason?: string | null;
  onHover: (id: string | null) => void;
}) {
  const r = reason ? REASONS[reason] : null;
  return (
    <div
      onMouseEnter={() => onHover(c.chunk_id)}
      onMouseLeave={() => onHover(null)}
      style={{ height: ROW_H }}
      className={[
        "flex items-center gap-2.5 rounded-lg border px-2.5 transition",
        active
          ? "border-neutral-900 bg-white shadow-sm"
          : "border-neutral-200 bg-white",
        dimmed ? "opacity-40" : "",
      ].join(" ")}
    >
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-neutral-900 text-[11px] font-semibold tabular-nums text-white">
        {rank}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[11px] font-medium text-neutral-800">
          {c.doc_title}
        </div>
        <div className="mt-0.5 flex items-center gap-1.5">
          <span className="text-[9px] uppercase tracking-wide text-neutral-400">
            {scoreLabel}
          </span>
          <span className="text-[10px] font-semibold tabular-nums text-neutral-700">
            {score.toFixed(3)}
          </span>
          {r && (
            <span
              className={`rounded px-1 py-px text-[9px] font-medium ${r.cls}`}
            >
              {r.label}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default function RerankFlow({ stages }: { stages: RetrievalStages }) {
  const [hover, setHover] = useState<string | null>(null);
  const cands = stages.candidates;

  if (cands.length === 0) {
    return (
      <p className="text-xs text-neutral-400">No candidates were retrieved.</p>
    );
  }

  const byMerged = [...cands].sort((a, b) => a.merged_rank - b.merged_rank);
  const byRerank = [...cands].sort((a, b) => a.rerank_rank - b.rerank_rank);

  const n = cands.length;
  const svgH = n * PITCH - GAP;
  const yFor = (rank: number) => (rank - 1) * PITCH + ROW_H / 2;

  const moved = cands.filter((c) => c.rank_delta !== 0).length;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-neutral-500">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded bg-emerald-500" /> promoted
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded bg-rose-500" /> demoted
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded bg-neutral-400" /> unchanged
        </span>
        <span className="text-neutral-400">
          the cross encoder moved {moved} of {n} candidates, and kept{" "}
          {cands.filter((c) => c.kept).length} above a score of{" "}
          {stages.min_rerank_score}
        </span>
      </div>

      <div className="flex gap-0">
        {/* Before */}
        <div className="flex-1">
          <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-neutral-400">
            After hybrid merge
          </h4>
          <div style={{ display: "flex", flexDirection: "column", gap: GAP }}>
            {byMerged.map((c) => (
              <Card
                key={c.chunk_id}
                c={c}
                rank={c.merged_rank}
                score={c.merged_score}
                scoreLabel="fused"
                dimmed={false}
                active={hover === c.chunk_id}
                onHover={setHover}
              />
            ))}
          </div>
        </div>

        {/* Connectors */}
        <div style={{ width: LANE_W }} className="shrink-0 pt-[26px]">
          <svg width={LANE_W} height={svgH} className="overflow-visible">
            {cands.map((c) => {
              const y1 = yFor(c.merged_rank);
              const y2 = yFor(c.rerank_rank);
              const tone = toneFor(c.rank_delta);
              const isHover = hover === c.chunk_id;
              return (
                <path
                  key={c.chunk_id}
                  d={`M 0 ${y1} C ${LANE_W * 0.45} ${y1}, ${LANE_W * 0.55} ${y2}, ${LANE_W} ${y2}`}
                  fill="none"
                  stroke={tone.stroke}
                  strokeWidth={isHover ? 2.5 : 1.5}
                  strokeDasharray={c.kept ? undefined : "3 3"}
                  opacity={hover && !isHover ? 0.15 : c.kept ? 0.9 : 0.5}
                />
              );
            })}
          </svg>
        </div>

        {/* After */}
        <div className="relative flex-1">
          <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-neutral-400">
            After cross encoder rerank
          </h4>
          <div style={{ display: "flex", flexDirection: "column", gap: GAP }}>
            {byRerank.map((c) => {
              const tone = toneFor(c.rank_delta);
              return (
                <div key={c.chunk_id} className="relative">
                  <Card
                    c={c}
                    rank={c.rerank_rank}
                    score={c.rerank_score}
                    scoreLabel="rerank"
                    dimmed={!c.kept}
                    active={hover === c.chunk_id}
                    reason={c.drop_reason}
                    onHover={setHover}
                  />
                  {c.rank_delta !== 0 && (
                    <span
                      className={`absolute -right-1 top-1/2 -translate-y-1/2 rounded px-1 py-0.5 text-[9px] font-semibold tabular-nums ${tone.bg} ${tone.text}`}
                    >
                      {c.rank_delta > 0 ? "+" : ""}
                      {c.rank_delta}
                    </span>
                  )}
                </div>
              );
            })}
          </div>

        </div>
      </div>
    </div>
  );
}
