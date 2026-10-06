# fleet-triage

Failure triage for GPU training clusters. Given a failure report, it decides the
root cause, whether the failure is already documented, and whether the node
should be drained, then explains its reasoning.

## Metrics

Per 500 classifications.

| | Baseline | This system |
|---|---|---|
| Cost | ~$1.65 (est., general-purpose LLM) | ~$0.015 (measured, Jev) |
| Latency | ~25-42 min (est., general-purpose LLM) | ~2-3 min (measured, Jev) |
| Root-cause accuracy | 56.7% (no evidence) | 100% (with retrieval and graph) |
| Drain decision F1 | 0.77 (no evidence) | 1.00 (with retrieval and graph) |

Accuracy and F1 are from 60 incidents in the synthetic corpus. Each incident
cites the known issue that states its cause, so these results show the pipeline
works on this corpus, not on real failures.

## Flow

```
failure report
      |
      +-- 1. retrieval   Chroma vector search + BM25 keyword search, fused by rank (RRF),
      |                  reranked by a cross-encoder, one chunk per document.
      |                  Queries with nothing relevant return no context.
      |
      +-- 2. graph       entities matched in Neo4j, walked up to two hops,
      |                  generic hub nodes excluded.
      |
      +-- 3. decision    Jev returns the answers below.
      |
      +-- 4. synthesis   LLM writes the explanation from the evidence above.
```

The decision returns four answers:

- `root_cause`: one of `hardware`, `workload`, `infrastructure`, `inconclusive`
- `known_issue`: probability the failure is already documented
- `drain_node`: probability the node should leave the scheduler
- `severity`: position on an ordered scale

Each step is checkpointed, so an interrupted run resumes where it stopped. If no
OpenRouter key is set, the decision falls back to the chat model with a checked
JSON contract.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (static export), React 19, TypeScript, Tailwind CSS |
| Backend | Python 3.12, FastAPI, uvicorn, Pydantic |
| Vector search | Chroma (embedded), all-MiniLM-L6-v2 embeddings |
| Keyword search | BM25 (rank-bm25) |
| Reranking | ms-marco-MiniLM-L-6-v2 cross-encoder (sentence-transformers) |
| Knowledge graph | Neo4j (AuraDB Free) |
| Decision model | TypeSafe Jev via OpenRouter (typesafe-sdk) |
| Synthesis and fallback | OpenAI gpt-4o-mini |
| Hosting | AWS: EC2 and ECR (API), S3 and CloudFront (UI and API), Systems Manager, IAM |
| CI/CD | GitHub Actions, authenticated to AWS with OIDC, deploys on push to `main` |

## Data

`gpu_cluster_failures.xlsx` holds 500 synthetic documents: 474 incident reports
and 26 known-issue writeups. The failure modes are based on NVIDIA's Xid error
codes, as summarized in a third-party field guide, and on published
large-scale training failure rates. Incidents, nodes, jobs, and dates are
generated, not real.
