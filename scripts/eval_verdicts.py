"""
Evaluates the root cause and the drain decision against ground truth taken
from the corpus, with and without retrieved evidence.

Ground truth
  root cause  taken from the incident's Area (see ROOT_CAUSE_BY_AREA). Every
              area in the corpus must map to a root cause; an unmapped area
              stops the run.
  drain       hardware is a drain. workload is not. infrastructure has no
              unambiguous answer, so it is left out of the drain metric.

Metrics reported
  root-cause accuracy   share of sampled incidents whose root cause is right.
  drain F1              F1 for the positive class "drain".

Every incident in this corpus is linked to a documented known issue, so the
known_issue answer is not scored here.

Usage:  python scripts/eval_verdicts.py [n_per_cause]
"""

import os
import sys

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import XLSX_PATH
from app.decide import classify
from app.graph import all_entities, graph_context
from app.agent import _guess_entities
from app.retrieval import hybrid_search_detailed

ROOT_CAUSE_BY_AREA = {
    "GPU Memory": "hardware",
    "GPU Hardware": "hardware",
    "PCIe and Bus": "hardware",
    "NVLink and Fabric": "hardware",
    "Application": "workload",
    "Thermal": "infrastructure",
    "Power": "infrastructure",
    "Network": "infrastructure",
    "Storage": "infrastructure",
    "Scheduler": "infrastructure",
    "Driver and Firmware": "infrastructure",
    "Host": "infrastructure",
}
DRAIN_BY_CAUSE = {"hardware": True, "workload": False, "infrastructure": None}
DRAIN_THRESHOLD = 0.5


def load_incidents():
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    rows = list(wb["Documents"].iter_rows(values_only=True))[1:]
    out = []
    for r in rows:
        if not r or not r[0] or not str(r[0]).startswith("INC"):
            continue
        area = r[3]
        if area not in ROOT_CAUSE_BY_AREA:
            sys.exit(f"unmapped area {area!r} in {r[0]}; add it to ROOT_CAUSE_BY_AREA")
        cause = ROOT_CAUSE_BY_AREA[area]
        out.append({"id": r[0], "report": str(r[7]), "cause": cause,
                    "drain": DRAIN_BY_CAUSE[cause]})
    return out


def f1(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


def main():
    n_per_cause = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    incidents = load_incidents()
    sample = []
    for cause in ("hardware", "workload", "infrastructure"):
        sample += [i for i in incidents if i["cause"] == cause][:n_per_cause]
    print(f"evaluating {len(sample)} incidents ({n_per_cause} per root cause)")

    graph_ok = True
    try:
        entities_known = all_entities()
    except Exception as e:
        graph_ok = False
        entities_known = []
        print(f"WARNING: graph unavailable ({type(e).__name__}); the graph condition is retrieval only.")

    results = {m: {"cause_ok": 0, "n": 0, "dt": [], "dp": []} for m in ("blind", "rag")}
    for n, inc in enumerate(sample, 1):
        q = inc["report"]
        chunks, _ = hybrid_search_detailed(q)
        facts = []
        if graph_ok:
            try:
                facts = graph_context(_guess_entities(q, entities_known))
            except Exception:
                facts = []
        for mode, (c, f) in (("blind", ([], [])), ("rag", (chunks, facts))):
            v = classify(q, c, f)
            r = results[mode]
            r["n"] += 1
            r["cause_ok"] += v.root_cause == inc["cause"]
            if inc["drain"] is not None and v.drain_node is not None:
                r["dt"].append(inc["drain"])
                r["dp"].append(v.drain_node >= DRAIN_THRESHOLD)
        if n % 10 == 0:
            print(f"  {n}/{len(sample)} ...")

    print()
    print(f"{'':<26}{'root-cause accuracy':>22}{'drain F1':>12}")
    for mode, label in (("blind", "no retrieval"), ("rag", "retrieval + graph" if graph_ok else "retrieval only")):
        r = results[mode]
        acc = r["cause_ok"] / r["n"] if r["n"] else 0.0
        tp = sum(1 for t, p in zip(r["dt"], r["dp"]) if t and p)
        fp = sum(1 for t, p in zip(r["dt"], r["dp"]) if not t and p)
        fn = sum(1 for t, p in zip(r["dt"], r["dp"]) if t and not p)
        print(f"{label:<26}{acc:>22.3f}{f1(tp, fp, fn):>12.3f}")
    print(f"\nroot-cause accuracy over {len(sample)} incidents; drain F1 over {len(results['rag']['dt'])} drain-labelled incidents")


if __name__ == "__main__":
    main()
