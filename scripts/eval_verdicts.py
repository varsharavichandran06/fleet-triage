"""
Measures the typed verdict against the corpus, with and without retrieval.

Ground truth comes from the workbook rather than from hand labelling. Every
incident is linked to a known issue by construction, so:

  Area == "Application"  ->  user_code     (failure modes where the error
                                            originates in the workload
                                            rather than the hardware)
  anything else          ->  known_issue

The drain decision follows from that: never drain for a workload bug, drain
for a device level fault. Areas where draining is arguable (thermal,
network, scheduler, storage) are excluded from that metric rather than
scored against an assumption.

The reported figure of interest is the gap between the two conditions,
which isolates the contribution of the retrieval and graph stages.

Usage:  python scripts/eval_verdicts.py [n_per_class]
"""

import os
import sys
from collections import Counter

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import XLSX_PATH
from app.decide import classify
from app.graph import all_entities, graph_context
from app.agent import _guess_entities
from app.retrieval import hybrid_search_detailed

USER_CODE_AREA = "Application"
# Device level faults where draining the node is the correct action.
DRAIN_AREAS = {"GPU Memory", "PCIe and Bus", "NVLink and Fabric", "GPU Hardware"}


def load_incidents():
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    rows = list(wb["Documents"].iter_rows(values_only=True))[1:]
    out = []
    for r in rows:
        if not r or not r[0] or not str(r[0]).startswith("INC"):
            continue
        out.append(
            {
                "id": r[0],
                "area": r[3],
                "report": str(r[7]),
                "truth": "user_code" if r[3] == USER_CODE_AREA else "known_issue",
            }
        )
    return out


def main():
    n_per_class = int(sys.argv[1]) if len(sys.argv) > 1 else 30

    incidents = load_incidents()
    user_code = [i for i in incidents if i["truth"] == "user_code"][:n_per_class]
    known = [i for i in incidents if i["truth"] == "known_issue"][:n_per_class]
    sample = user_code + known
    print(f"evaluating {len(sample)} incidents "
          f"({len(user_code)} user_code, {len(known)} known_issue)\n")

    entities_known = all_entities()
    stats = {"blind": Counter(), "rag": Counter()}
    drain = {"blind": Counter(), "rag": Counter()}
    latency = {"blind": [], "rag": []}
    confusion = {"blind": Counter(), "rag": Counter()}

    for n, inc in enumerate(sample, 1):
        q = inc["report"]
        chunks, _ = hybrid_search_detailed(q)
        facts = graph_context(_guess_entities(q, entities_known))

        for mode, (c, f) in (("blind", ([], [])), ("rag", (chunks, facts))):
            v = classify(q, c, f)
            ok = v.verdict == inc["truth"]
            stats[mode]["correct" if ok else "wrong"] += 1
            confusion[mode][f'{inc["truth"]} -> {v.verdict}'] += 1
            if v.latency_ms:
                latency[mode].append(v.latency_ms)

            # Drain decision, only where the right answer is unambiguous.
            if v.drain_node is not None:
                if inc["truth"] == "user_code":
                    drain[mode]["correct" if v.drain_node < 0.5 else "wrong"] += 1
                elif inc["area"] in DRAIN_AREAS:
                    drain[mode]["correct" if v.drain_node >= 0.5 else "wrong"] += 1

        if n % 10 == 0:
            print(f"  {n}/{len(sample)} ...")

    print()
    print("=" * 62)
    print(f"{'':16}{'VERDICT':>18}{'DRAIN CALL':>18}{'LATENCY':>10}")
    for mode, label in (("blind", "no retrieval"), ("rag", "with retrieval")):
        s, d = stats[mode], drain[mode]
        tot = s["correct"] + s["wrong"]
        dtot = d["correct"] + d["wrong"]
        lat = sum(latency[mode]) / len(latency[mode]) if latency[mode] else 0
        vpct = 100 * s["correct"] / tot if tot else 0
        dpct = 100 * d["correct"] / dtot if dtot else 0
        print(f"{label:16}{s['correct']:>4}/{tot:<3}{vpct:>8.1f}%"
              f"{d['correct']:>6}/{dtot:<3}{dpct:>8.1f}%{lat:>8.0f}ms")
    print("=" * 62)

    gain = (
        100 * stats["rag"]["correct"] / max(1, sum(stats["rag"].values()))
        - 100 * stats["blind"]["correct"] / max(1, sum(stats["blind"].values()))
    )
    print(f"\nretrieval changes verdict accuracy by {gain:+.1f} points")
    print("\nconfusion, with retrieval:")
    for k, c in confusion["rag"].most_common():
        print(f"   {k:34} {c}")
    print("\nconfusion, without retrieval:")
    for k, c in confusion["blind"].most_common():
        print(f"   {k:34} {c}")


if __name__ == "__main__":
    main()
