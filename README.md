# fleet-triage

Failure triage for GPU training clusters. Given a failure report, it
retrieves comparable incidents and known issues, pulls related facts from a
knowledge graph, returns a typed verdict with calibrated probabilities, and
explains the reasoning in prose. Every stage is recorded so a decision can be
inspected after the fact.

The question it answers is the one that costs money to get wrong: is this a
documented known issue, a new failure worth escalating, a bug in the
workload, or an artifact of the test setup — and should the node be drained?

**Live:** UI at https://d2g92amvr8fh0b.cloudfront.net, API at
https://dx1c4iwfxwyxv.cloudfront.net.

---

## Measured impact

A single failure classification costs fractions of a cent and takes a
fraction of a second either way, so per-call numbers understate what these
choices actually mean — the table below shows them at a batch of 500
classifications instead. What matters is that the ratios hold at any
volume, and the volume is not small: Meta's published account of training
Llama 3 405B put GPU-related interruptions at roughly one every three hours
on a 16,384-GPU cluster, and estimated that a 100,000-GPU cluster fails on
the order of once every 30 minutes. At that rate, a single large cluster
alone generates on the order of 1,400 triage-worthy events a month, and a
fleet operator running several such clusters is well into the tens of
thousands.

| | Baseline | This system | Change |
|---|---|---|---|
| Cost (500 calls) | ~$1.65 (est., general-purpose LLM) | ~$0.015 (measured, Jev) | ~99% lower, ~110x |
| Latency (per 500 calls) | ~25–42 min (est., general-purpose LLM) | ~2–3 min (measured, Jev) | ~13x faster |
| Graph DB memory vs. data actually held | 780MB allocated (measured) | 65MB actually used (measured) | 12x gap closed |
| Verdict accuracy, no evidence vs. with evidence | 0% (no retrieval) | 95.8% (with retrieval + graph) | +95.8 pts |
| Drain-decision accuracy, no evidence vs. with evidence | 70.8% (no retrieval) | 100% (with retrieval + graph) | +29.2 pts |

- **Cut classification cost ~99% (~110x)** by billing the decision per
  classification rather than per token generated. That ratio is what
  compounds as event volume moves from hundreds a month to the tens of
  thousands a multi-cluster fleet can produce — not the cents on any single
  call.
- **Increased classification throughput ~13x** by routing the decision
  through a purpose-built typed model instead of a general-purpose chat
  completion. At fleet scale that is a throughput multiplier, not a
  convenience — the difference between a triage system that keeps pace
  with a cluster failing every 30 minutes and one that falls behind it.
- **Avoided an unnecessary infrastructure upgrade** by profiling actual
  resource usage against what had been provisioned before committing to
  more compute. The specific fix matters less than the practice: measuring
  real usage before scaling is what keeps waste from compounding silently
  as a fleet of services grows.
- **Made the verdict trustworthy enough to automate on** by grounding the
  decision model in retrieved evidence and knowledge-graph facts instead of
  the raw failure report alone. This is the entire reason the retrieval and
  graph layers exist rather than calling a model directly on the report.

---

## Pipeline

```
failure report
      │
      ├─ 1. retrieval    vector search + BM25, fused by rank (RRF),
      │                  reranked by a cross encoder, one chunk per document
      │
      ├─ 2. graph        entities matched against Neo4j, walked up to 2 hops,
      │                  hub entities excluded from matching and traversal
      │
      ├─ 3. decision     typed verdict with probabilities (System One model)
      │
      └─ 4. synthesis    prose explanation grounded in the evidence above
```

Each step checkpoints before the next begins, so an interrupted run resumes
from the last completed step rather than repeating work.

### Retrieval

Dense vector search over Chroma and sparse BM25 run independently, then fuse
with Reciprocal Rank Fusion. Fusion is by rank rather than score: the two
signals sit on incomparable scales, and normalising them onto a shared range
floors the weakest match in each to zero, making it indistinguishable from no
match at all.

The fused top-k goes to a cross encoder. Cross encoder scores are absolute
and comparable across queries, so they carry the relevance threshold: a query
with nothing relevant behind it returns no context rather than the least
irrelevant chunks available. The final set holds one chunk per document, so a
single verbose source cannot occupy every slot.

### Knowledge graph

Triples are extracted per document and merged into Neo4j on entity name.
Entities above a degree cap, and a blocklist of generic nouns, are excluded
both from query matching and from being walked through — without that, a
common word like `node` becomes a hub joining hundreds of unrelated
documents. Traversal runs to two hops, which is what lets an error code reach
the component it affects and the remedy recorded for it:

```
out of bounds access → custom kernel → xid 13
xid 48 → double bit ECC error → RMA
```

### Typed decision

The verdict is a typed value, not prose:

```python
verdict     Choice  known_issue | new_issue | user_code | test_artifact
drain_node  Noul    probability that the node should leave the scheduler
severity    Score   ordered rubric
```

Backed by TypeSafe Jev through OpenRouter, with the chat model and a checked
JSON contract as a fallback when no key is configured. Classification runs
before synthesis because it is a fast decision over evidence already
gathered and does not need the model that writes the explanation.

---

## Data

`gpu_cluster_failures.xlsx` holds 500 synthetic documents: 474 incident
reports and 26 known-issue writeups across 12 areas (GPU memory, PCIe,
NVLink, thermal, power, network, storage, scheduler, driver, host,
application, GPU hardware).

Failure modes are grounded in the NVIDIA Xid error catalogue and in published
analyses of large training runs. All incidents, node names, jobs and dates are
invented. Incidents cite their known issue and each other, giving 1,346
cross-references with no dangling identifiers.

Five failure modes are deliberately ambiguous, because distinguishing them is
the judgement the system exists to make:

- **Xid 13 / Xid 31** arrive as kernel-log errors but originate in the
  workload. Draining the node wastes capacity.
- **Xid 43** is genuinely ambiguous between a long-running kernel and an
  unresponsive device.
- **NCCL timeouts** look identical whether caused by fabric faults or by one
  slow rank.
- **Host OOM** kills the job with no GPU error at all.

---

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/triage` | Run a triage. Body: `{"query": "...", "run_id": null}` |
| `POST` | `/triage/stream` | Same pipeline as Server-Sent Events, one frame per completed step |
| `GET` | `/runs/{run_id}` | Stored result of a run |
| `GET` | `/runs/{run_id}/audit` | Full audit trail |
| `GET` | `/health` | Liveness |

Passing a previous `run_id` to `/triage` resumes that run from its last
completed step. An unreachable language model returns `502` rather than a
fabricated answer.

`/triage/stream` emits a `step` event as each stage completes, a terminal
`done` event carrying the full result, and `error` on failure. The frontend
uses it to render retrieval and scoring while the graph, decision and
synthesis stages are still running.

---

## Layout

```
app/
  main.py            FastAPI application
  agent.py           four-step loop with checkpointing
  retrieval.py       hybrid search, RRF fusion, reranking
  graph.py           Neo4j triple storage and traversal
  decide.py          typed verdict, Jev with a chat-model fallback
  llm.py             language model wrapper
  chunking.py        sliding window chunker
  audit.py           per-run audit trail
  logging_setup.py   logging configuration
  config.py          configuration and tuning constants
scripts/
  ingest_xlsx.py     workbook -> text corpus
  build_index.py     build the vector and keyword indexes
  extract_graph.py   extract triples into Neo4j
  eval_verdicts.py   verdict accuracy with and without retrieval
frontend/            Next.js interface showing every pipeline stage
```

---

## Deployment

The backend runs as a Docker container on a single EC2 instance, reached
only through CloudFront: a custom origin header, checked on every request,
means the instance refuses anything that did not come through the CDN, and
the security group only accepts inbound traffic from CloudFront's own IP
range. The instance has no SSH key and no open management port — it is
reached through SSM Session Manager, authenticated by IAM rather than a
credential that can be lost or leaked.

The knowledge graph runs on Neo4j AuraDB rather than a container beside the
app: a self-hosted graph for data this size cost more in JVM overhead than
the data itself occupies. The frontend is a static export served from S3
through a second, independent CloudFront distribution, kept separate from
the API distribution so a caching policy tuned for one can never leak into
the other.

Deployment is automated: pushing to `main` builds the image, pushes it to
ECR, and redeploys the running container over SSM, all authenticated
through GitHub's OIDC federation rather than a stored AWS key.

## Security

The API has no user accounts — a static frontend cannot hold a secret a
browser can't read back, so "auth" here means abuse resistance rather than
login. Three things bound it: a daily cap on total runs across every
caller, a per-caller rate limit, and a maximum query length, since
unbounded input goes straight into two paid model calls. An optional API
key lets a caller bypass the per-caller limit, though never the daily cap;
it is not sent by the browser UI and is not a browser-held secret. CORS
lists allowed origins explicitly rather than a wildcard.

## Configuration

Set in `.env`; see `.env.example`. Tuning constants live in `app/config.py`:
chunk size and overlap, candidate and final result counts, the RRF constant,
the rerank threshold, graph hop depth and the entity degree cap.

`LOG_LEVEL=DEBUG` adds per-stage internals. `LOG_PAYLOADS=true` additionally
logs prompts and retrieved text, which may contain document contents.

## Limitations

- The corpus is synthetic. Failure modes are grounded in public
  documentation; the incidents are not real.
- Triple extraction is free text, so the graph relies on degree capping and a
  blocklist to suppress hub entities rather than on a typed schema.
- Chroma runs embedded and in-process, so the service does not scale
  horizontally as written.
- Rate limiting and the daily budget are held in memory on the single
  instance; they reset on restart and would not be shared across replicas
  if the service were ever scaled beyond one.
- There are no user accounts. The controls in Security bound abuse of a
  public endpoint; they are not identity or access control for the data
  itself.
