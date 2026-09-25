"use client";

import { useMemo, useState } from "react";
import type { GraphFact } from "@/lib/api";

const W = 620;
const H = 440;

type Node = {
  id: string;
  x: number;
  y: number;
  degree: number;
  seed: boolean;
};

/**
 * Small force directed layout, written out rather than pulled in as a
 * dependency. The graphs here are a handful of nodes, so a few hundred
 * deterministic iterations settle them fine and the bundle stays lean.
 * Deterministic seeding also means the layout does not jump around
 * between renders of the same result.
 */
function layout(facts: GraphFact[], seeds: Set<string>): { nodes: Node[]; edges: GraphFact[] } {
  const ids = new Set<string>();
  facts.forEach((f) => {
    ids.add(f.subject);
    ids.add(f.object);
  });
  const list = [...ids];
  const degree = new Map<string, number>();
  facts.forEach((f) => {
    degree.set(f.subject, (degree.get(f.subject) ?? 0) + 1);
    degree.set(f.object, (degree.get(f.object) ?? 0) + 1);
  });

  const n = list.length;
  const cx = W / 2;
  const cy = H / 2;

  // Seed on a circle, ordered so seed entities start nearer the middle.
  const nodes: Node[] = list.map((id, i) => {
    const isSeed = seeds.has(id);
    const angle = (i / Math.max(1, n)) * Math.PI * 2;
    const r = isSeed ? 70 : 150;
    return {
      id,
      x: cx + Math.cos(angle) * r,
      y: cy + Math.sin(angle) * r,
      degree: degree.get(id) ?? 1,
      seed: isSeed,
    };
  });

  const index = new Map(nodes.map((nd, i) => [nd.id, i]));
  const REPULSION = 9000;
  const SPRING_LEN = 110;
  const SPRING_K = 0.015;
  const CENTER_K = 0.012;
  const ITERS = 400;

  for (let step = 0; step < ITERS; step++) {
    const cool = 1 - step / ITERS;
    const fx = new Array(n).fill(0);
    const fy = new Array(n).fill(0);

    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = nodes[i].x - nodes[j].x;
        let dy = nodes[i].y - nodes[j].y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) {
          // Identical positions would divide by zero; nudge deterministically.
          dx = (i - j) * 0.5 + 0.1;
          dy = (j - i) * 0.5 + 0.1;
          d2 = dx * dx + dy * dy;
        }
        const d = Math.sqrt(d2);
        const f = REPULSION / d2;
        fx[i] += (dx / d) * f;
        fy[i] += (dy / d) * f;
        fx[j] -= (dx / d) * f;
        fy[j] -= (dy / d) * f;
      }
    }

    facts.forEach((e) => {
      const a = index.get(e.subject);
      const b = index.get(e.object);
      if (a === undefined || b === undefined) return;
      const dx = nodes[b].x - nodes[a].x;
      const dy = nodes[b].y - nodes[a].y;
      const d = Math.max(1, Math.hypot(dx, dy));
      const f = (d - SPRING_LEN) * SPRING_K;
      fx[a] += (dx / d) * f;
      fy[a] += (dy / d) * f;
      fx[b] -= (dx / d) * f;
      fy[b] -= (dy / d) * f;
    });

    for (let i = 0; i < n; i++) {
      fx[i] += (cx - nodes[i].x) * CENTER_K;
      fy[i] += (cy - nodes[i].y) * CENTER_K;
      nodes[i].x += Math.max(-12, Math.min(12, fx[i])) * cool;
      nodes[i].y += Math.max(-12, Math.min(12, fy[i])) * cool;
      // Keep everything inside the viewBox with room for labels.
      nodes[i].x = Math.max(70, Math.min(W - 70, nodes[i].x));
      nodes[i].y = Math.max(34, Math.min(H - 34, nodes[i].y));
    }
  }

  return { nodes, edges: facts };
}

function trim(s: string, max = 24) {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

export default function GraphView({
  facts,
  entities,
}: {
  facts: GraphFact[];
  entities: string[];
}) {
  const [hover, setHover] = useState<string | null>(null);

  const seeds = useMemo(
    () => new Set(entities.map((e) => e.toLowerCase())),
    [entities]
  );
  const { nodes, edges } = useMemo(() => layout(facts, seeds), [facts, seeds]);
  const pos = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  if (facts.length === 0) {
    return (
      <p className="text-xs text-neutral-400">
        No graph facts were pulled for this query, so nothing was added to the
        prompt from the knowledge graph.
      </p>
    );
  }

  const touches = (f: GraphFact) =>
    hover !== null && (f.subject === hover || f.object === hover);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-neutral-500">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-neutral-900" /> matched
          from your query
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full border border-neutral-400 bg-white" />{" "}
          pulled in as a neighbour
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="18" height="4">
            <line x1="0" y1="2" x2="18" y2="2" stroke="#a3a3a3" strokeWidth="1.2" strokeDasharray="4 3" />
          </svg>
          reached at 2 hops
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-neutral-50">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full min-w-[520px]">
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="5"
              markerHeight="5"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#a3a3a3" />
            </marker>
          </defs>

          {edges.map((f, i) => {
            const a = pos.get(f.subject);
            const b = pos.get(f.object);
            if (!a || !b) return null;
            const lit = touches(f);
            const mx = (a.x + b.x) / 2;
            const my = (a.y + b.y) / 2;
            return (
              <g
                key={i}
                opacity={hover && !lit ? 0.12 : 1}
                className="transition-opacity"
              >
                <line
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={lit ? "#171717" : f.hop_distance > 1 ? "#e5e5e5" : "#d4d4d4"}
                  strokeWidth={lit ? 1.8 : 1.2}
                  strokeDasharray={f.hop_distance > 1 ? "4 3" : undefined}
                  markerEnd="url(#arrow)"
                />
                <text
                  x={mx}
                  y={my - 3}
                  textAnchor="middle"
                  className="select-none"
                  fontSize="8"
                  fill={lit ? "#404040" : "#a3a3a3"}
                >
                  {trim(f.relation, 18)}
                </text>
              </g>
            );
          })}

          {nodes.map((n) => {
            const r = Math.min(13, 7 + n.degree * 1.4);
            const lit = hover === n.id;
            const near =
              hover !== null &&
              edges.some(
                (f) =>
                  (f.subject === hover && f.object === n.id) ||
                  (f.object === hover && f.subject === n.id)
              );
            const faded = hover !== null && !lit && !near;
            return (
              <g
                key={n.id}
                opacity={faded ? 0.2 : 1}
                onMouseEnter={() => setHover(n.id)}
                onMouseLeave={() => setHover(null)}
                className="cursor-pointer transition-opacity"
              >
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={r}
                  fill={n.seed ? "#171717" : "#ffffff"}
                  stroke={n.seed ? "#171717" : "#a3a3a3"}
                  strokeWidth={lit ? 2.5 : 1.4}
                />
                <text
                  x={n.x}
                  y={n.y + r + 10}
                  textAnchor="middle"
                  className="select-none"
                  fontSize="9"
                  fontWeight={n.seed ? 600 : 400}
                  fill="#404040"
                >
                  {trim(n.id)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      <p className="mt-2 text-[10px] text-neutral-400">
        {nodes.length} entities, {edges.length} relations,{" "}
        {edges.filter((e) => e.hop_distance > 1).length} of them reached by
        walking a second hop out from the query. Hover a node to isolate its
        connections. Hub entities such as the bare word &quot;node&quot; are
        excluded from both matching and traversal, otherwise every failure
        mode in the fleet ends up connected to every other one. These
        relations are flattened into text and appended to the prompt below.
      </p>
    </div>
  );
}
