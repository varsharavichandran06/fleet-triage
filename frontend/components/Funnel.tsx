"use client";

import type { RetrievalStages } from "@/lib/api";

/**
 * Top of funnel view of the retrieval pipeline. Each bar is scaled against
 * the widest stage so the drop off from corpus to final context is visible
 * at a glance, and each caption says what did the cutting.
 */
export default function Funnel({ stages }: { stages: RetrievalStages }) {
  const kept = stages.candidates.filter((c) => c.kept).length;

  const finalCaption = (() => {
    const bits: string[] = [];
    if (stages.dropped_below_rerank > 0)
      bits.push(`${stages.dropped_below_rerank} below rerank ${stages.min_rerank_score}`);
    if (stages.dropped_duplicate_doc > 0)
      bits.push(`${stages.dropped_duplicate_doc} same document`);
    if (stages.dropped_over_cap > 0)
      bits.push(`${stages.dropped_over_cap} over the cap of ${stages.top_k_final}`);
    return bits.length
      ? `cut ${bits.join(", ")}`
      : "everything reranked above the threshold fit";
  })();

  const steps = [
    {
      label: "Corpus",
      count: stages.total_chunks,
      caption: "every chunk in the index",
      bar: "bg-neutral-300",
      dot: "bg-neutral-400",
    },
    {
      label: "Recalled",
      count: stages.pool_size,
      caption: `vector ${stages.vector_hits} + keyword ${stages.keyword_hits}, deduplicated`,
      bar: "bg-sky-400",
      dot: "bg-sky-500",
    },
    {
      label: "Shortlist",
      count: stages.shortlist_size,
      caption: `${stages.fusion.toUpperCase()} k=${stages.rrf_k} fuses the two signals by rank (${stages.both_signals} of ${stages.pool_size} found by both), then the top ${stages.top_k_candidates} go to the cross encoder`,
      bar: "bg-violet-400",
      dot: "bg-violet-500",
    },
    {
      label: "Final context",
      count: kept,
      caption: finalCaption,
      bar: kept === 0 ? "bg-rose-400" : "bg-emerald-500",
      dot: kept === 0 ? "bg-rose-500" : "bg-emerald-600",
    },
  ];

  const max = Math.max(...steps.map((s) => s.count), 1);

  return (
    <div className="space-y-3">
      {steps.map((s, i) => {
        // Floor the width so a stage of 2 out of 349 is still readable.
        const pct = s.count === 0 ? 0 : Math.max(8, (s.count / max) * 100);
        const prev = i > 0 ? steps[i - 1].count : null;
        const dropped = prev !== null ? prev - s.count : 0;
        return (
          <div key={s.label} className="flex items-center gap-3">
            <div className="flex w-32 shrink-0 items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${s.dot}`} />
              <span className="text-xs font-medium text-neutral-700">
                {s.label}
              </span>
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex h-7 items-center">
                {s.count === 0 ? (
                  <span className="rounded-md bg-rose-100 px-2 py-1 text-[11px] font-semibold text-rose-700">
                    0 — nothing cleared the relevance threshold
                  </span>
                ) : (
                  <div
                    className={`flex h-7 items-center rounded-md ${s.bar} px-2.5 transition-all`}
                    style={{ width: `${pct}%` }}
                  >
                    <span className="text-xs font-semibold tabular-nums text-white">
                      {s.count}
                    </span>
                  </div>
                )}
                {dropped > 0 && (
                  <span className="ml-2 shrink-0 text-[10px] tabular-nums text-neutral-400">
                    &minus;{dropped}
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-[10px] text-neutral-400">{s.caption}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
