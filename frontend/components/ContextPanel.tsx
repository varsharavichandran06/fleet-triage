"use client";

import { useMemo, useState } from "react";

type Kind = "chunk" | "fact" | "header" | "plain";

function classify(line: string): Kind {
  // Evidence lines are emitted as "- (Doc Title) text", graph facts as
  // "- subject relation object". The parenthesis is what separates them.
  if (line.startsWith("- (")) return "chunk";
  if (line.startsWith("- ")) return "fact";
  if (line.trim().endsWith(":") && line.trim().length > 0) return "header";
  return "plain";
}

const STYLES: Record<Kind, string> = {
  chunk: "border-l-2 border-sky-400 bg-sky-50/60 text-neutral-700",
  fact: "border-l-2 border-violet-400 bg-violet-50/60 text-neutral-700",
  header: "font-semibold text-neutral-800",
  plain: "text-neutral-600",
};

function Tile({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white px-3 py-2">
      <div className="text-sm font-semibold tabular-nums text-neutral-900">
        {value}
      </div>
      <div className="text-[10px] text-neutral-400">{label}</div>
    </div>
  );
}

export default function ContextPanel({
  prompt,
  nChunks,
  nFacts,
}: {
  prompt?: string;
  nChunks: number;
  nFacts: number;
}) {
  const [open, setOpen] = useState(true);

  const lines = useMemo(
    () => (prompt ? prompt.split("\n").map((l) => ({ l, k: classify(l) })) : []),
    [prompt]
  );

  if (!prompt) {
    return (
      <p className="text-xs text-neutral-400">
        This run has no recorded prompt. It was resumed from a checkpoint
        written before prompt capture existed, so re-run the query to see it.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile value={nChunks} label="evidence chunks" />
        <Tile value={nFacts} label="graph facts" />
        <Tile value={prompt.length.toLocaleString()} label="prompt characters" />
        <Tile
          value={`~${Math.ceil(prompt.length / 4).toLocaleString()}`}
          label="approx tokens"
        />
      </div>

      <div className="mb-2 flex items-center justify-between">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-neutral-500">
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-1 rounded bg-sky-400" /> from retrieval
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-1 rounded bg-violet-400" /> from the graph
          </span>
        </div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="rounded border border-neutral-300 px-2 py-0.5 text-[10px] text-neutral-600 transition hover:bg-neutral-100"
        >
          {open ? "Collapse" : "Expand"}
        </button>
      </div>

      {open && (
        <div className="max-h-[420px] overflow-auto rounded-lg border border-neutral-200 bg-white p-2">
          <div className="font-mono text-[10px] leading-relaxed">
            {lines.map(({ l, k }, i) => (
              <div
                key={i}
                className={`whitespace-pre-wrap break-words px-2 py-0.5 ${STYLES[k]}`}
              >
                {l === "" ? " " : l}
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="mt-2 text-[10px] text-neutral-400">
        This is the verbatim string sent to the model, not a reconstruction of
        it.
      </p>
    </div>
  );
}
