"use client";

import { useState } from "react";
import type { Verdict } from "@/lib/api";

// Label and colour for each root cause. An unrecognised value uses the neutral style.
const ROOT_CAUSE_TONE: Record<string, { label: string; cls: string; dot: string }> = {
  hardware: {
    label: "Hardware fault",
    cls: "border-rose-300 bg-rose-50 text-rose-900",
    dot: "bg-rose-500",
  },
  workload: {
    label: "Workload bug",
    cls: "border-sky-300 bg-sky-50 text-sky-900",
    dot: "bg-sky-500",
  },
  infrastructure: {
    label: "Infrastructure",
    cls: "border-amber-300 bg-amber-50 text-amber-900",
    dot: "bg-amber-500",
  },
  inconclusive: {
    label: "Inconclusive",
    cls: "border-neutral-300 bg-neutral-50 text-neutral-900",
    dot: "bg-neutral-500",
  },
};

// One row of the probability distribution. Width is clamped to 0..1 so a bad value cannot overflow the track.
function Bar({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="flex items-center gap-2 text-[10px]">
      <span className="w-24 shrink-0 truncate text-neutral-500">{label}</span>
      <div className="h-2 flex-1 rounded-full bg-neutral-200">
        <div
          className={`h-2 rounded-full ${tone}`}
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </div>
      <span className="w-10 shrink-0 text-right tabular-nums text-neutral-600">
        {value.toFixed(2)}
      </span>
    </div>
  );
}

// Pretty printed raw value. Shows the exact state and typed questions sent to the decision model, and the answers it returned.
function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-lg border border-neutral-200 bg-white p-2 font-mono text-[10px] leading-relaxed text-neutral-700">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

// The typed decision: verdict, confidence, the full probability distribution, the drain
// recommendation, and the model's exact input and output behind a tab switch.
export default function VerdictPanel({ v }: { v: Verdict }) {
  const [tab, setTab] = useState<"in" | "out">("in");

  const tone = ROOT_CAUSE_TONE[v.root_cause] ?? {
    label: v.root_cause,
    cls: "border-neutral-300 bg-neutral-50 text-neutral-900",
    dot: "bg-neutral-500",
  };
  const isJev = v.backend.startsWith("jev");
  const probs = Object.entries(v.probabilities).sort((a, b) => b[1] - a[1]);
  const evidence = v.request_state?.retrieved_evidence ?? [];
  const facts = v.request_state?.knowledge_graph_facts ?? [];

  return (
    <div>
      <div className={`mb-3 rounded-xl border p-4 ${tone.cls}`}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className={`h-2.5 w-2.5 rounded-full ${tone.dot}`} />
          <span className="text-base font-semibold">{tone.label}</span>
          <span className="rounded-full bg-white/70 px-2 py-0.5 text-[11px] font-medium tabular-nums">
            confidence {v.confidence.toFixed(2)}
          </span>
          <span className="ml-auto font-mono text-[10px] opacity-70">
            {v.backend}
            {v.latency_ms !== null && ` · ${v.latency_ms}ms`}
          </span>
        </div>

        <div className="mt-3 flex flex-wrap gap-4 text-[11px]">
          {v.known_issue !== null && (
            <span>
              <span className="opacity-70">already documented: </span>
              <span className="font-semibold tabular-nums">
                {v.known_issue < 0.5 ? "no" : "yes"} ({v.known_issue.toFixed(2)})
              </span>
            </span>
          )}
          {v.drain_node !== null && (
            <span>
              <span className="opacity-70">drain the node: </span>
              <span className="font-semibold tabular-nums">
                {v.drain_node < 0.5 ? "no" : "yes"} ({v.drain_node.toFixed(2)})
              </span>
            </span>
          )}
          {v.severity !== null && (
            <span>
              <span className="opacity-70">severity: </span>
              <span className="font-semibold tabular-nums">
                {v.severity.toFixed(2)}
              </span>
            </span>
          )}
        </div>

        {v.error && (
          <p className="mt-2 rounded bg-white/60 px-2 py-1 text-[10px]">{v.error}</p>
        )}
      </div>

      {probs.length > 0 && (
        <div className="mb-4 space-y-1.5">
          <p className="text-[10px] uppercase tracking-wide text-neutral-400">
            Probability across every option
          </p>
          {probs.map(([k, p]) => (
            <Bar
              key={k}
              label={k}
              value={p}
              tone={k === v.root_cause ? "bg-neutral-900" : "bg-neutral-400"}
            />
          ))}
        </div>
      )}

      {/* The mechanism: what the model was handed, and what it returned. */}
      <div className="mb-2 flex items-center gap-2">
        {(["in", "out"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`rounded-full border px-2.5 py-1 text-[11px] transition ${
              tab === t
                ? "border-neutral-900 bg-neutral-900 text-white"
                : "border-neutral-300 bg-white text-neutral-600 hover:border-neutral-500"
            }`}
          >
            {t === "in" ? "Model input" : "Model output"}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-neutral-400">
          {isJev
            ? "unstructured state in, typed decision out"
            : "chat model with a checked JSON contract"}
        </span>
      </div>

      {tab === "in" ? (
        <div className="space-y-3">
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-neutral-400">
              State - {evidence.length} retrieved chunks, {facts.length} graph facts
            </p>
            <Json value={v.request_state} />
          </div>
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-neutral-400">
              Questions - the typed schema the answer must conform to
            </p>
            <Json value={v.request_questions} />
          </div>
        </div>
      ) : (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-neutral-400">
            Answers - typed values with their distributions, never free text
          </p>
          <Json value={v.raw_answers} />
        </div>
      )}
    </div>
  );
}
