"""
Central config, read from environment variables so nothing secret is
hardcoded. Copy .env.example to .env and fill in your own values.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- LLM ---
# Works with any OpenAI-compatible endpoint: OpenAI itself, a local vLLM
# server, or another provider that speaks the same API shape. If you want
# to use Claude instead, swap the client in app/llm.py for the anthropic
# SDK, the rest of the pipeline does not care which model answers.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# --- System One decision model (TypeSafe Jev) ---
# Jev returns a typed decision with calibrated probabilities rather than
# prose. Reached through OpenRouter. When no key is set the decision step
# falls back to the chat model with a checked JSON contract, so the
# pipeline runs either way.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
JEV_BASE_URL = os.getenv("JEV_BASE_URL", "https://openrouter.ai/api")
# Pinned rather than tracking latest. A calibrated decision model that
# changes version invalidates accuracy measured against the previous one.
JEV_MODEL = os.getenv("JEV_MODEL", "jev-1.13")

# The verdicts the triage step is allowed to return.
VERDICTS = {
    "known_issue": "Matches a documented known issue in the retrieved evidence",
    "new_issue": "A real failure with no matching known issue, worth escalating",
    "user_code": "A bug in the workload rather than the hardware",
    "test_artifact": "A test rig, harness or measurement problem, not the product",
}

# --- Neo4j ---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "testpassword")

# --- Knowledge graph ---
# How far to walk from an entity matched in the query. 1 hop returns only
# direct statements about that entity; 2 lets an error code reach the
# component it affects and the remedy recorded for it.
GRAPH_HOPS = 2

# Entities above this degree are treated as hubs: never matched against a
# query and never walked through on a multi hop path. Free text triple
# extraction collapses every machine in the fleet into the bare word
# "node", which then bridges hundreds of unrelated documents. Capping
# degree removes those bridges at query time.
MAX_ENTITY_DEGREE = 25

# Generic nouns that appear in almost every document in this domain, so
# they carry no information but match almost every query.
ENTITY_BLOCKLIST = {
    "node", "nodes", "device", "devices", "gpu", "gpus", "job", "jobs",
    "fault", "faults", "error", "errors", "checkpoint", "checkpoints",
    "all", "none", "issue", "issues", "problem", "system", "systems",
    "test", "tests", "run", "runs", "workload", "workloads", "cluster",
} | {
    # Per incident specifics that the extractor emits as if they were
    # concepts. A GPU index is meaningful inside one incident and
    # meaningless across them: "gpu 5" on one machine is unrelated to
    # "gpu 5" on another, but string matching merges them into a bridge
    # between unrelated failures.
    f"gpu {i}" for i in range(16)
} | {
    # Bare verbs and process nouns. Free text extraction happily emits
    # these as entities, and because they appear in every document they
    # match almost any query while carrying no meaning.
    "logged", "occurred", "reset", "drained", "replaced", "verified",
    "resolution", "service", "initialisation", "placement", "pool",
    "others", "rank", "ranks", "loss", "n/a", "none identified",
} | {
    # Job names. Meaningful inside one incident but harmful across them:
    # the same job runs on many machines, so string matching turns a job
    # name into a bridge between unrelated failure modes.
    "llama3-70b-pretrain", "mixtral-8x7b-sft", "vit-huge-train",
    "whisper-lg-finetune", "nemotron-4-sft", "dlrm-ctr-train",
    "esm2-fold-train", "sd3-finetune", "rlhf-ppo-7b", "gpt-oss-20b-eval",
    "bert-large-pretrain", "moe-router-ablation", "qwen-32b-continue",
    "clip-vit-contrastive", "asr-conformer-train",
}

# Shorter names than this match far too loosely in a substring test.
MIN_ENTITY_LEN = 4

# Cap on facts handed to the model, so a well connected entity cannot
# flood the prompt.
GRAPH_FACT_LIMIT = 30

# --- Paths ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Source of truth for the corpus. scripts/ingest_xlsx.py expands this
# workbook into the .txt files under DOCS_DIR that everything else reads.
XLSX_PATH = os.path.join(BASE_DIR, "gpu_cluster_failures.xlsx")
DOCS_DIR = os.path.join(BASE_DIR, "data", "docs")
CHROMA_DIR = os.path.join(BASE_DIR, "data", "chroma")
# Overridable so run state can be written outside the project tree, for
# example to a mounted volume in a container.
RUNS_DIR = os.getenv("RUNS_DIR", os.path.join(BASE_DIR, "runs"))

# --- Server ---
# Browsers enforce the same origin policy, so a page served from one
# origin cannot read a response from another unless the API allows it.
# Origins are listed explicitly rather than wildcarded, since a wildcard
# permits any site to call this API from a visitor's browser and read the
# response. Overridable for deployment.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if o.strip()
]

# --- Abuse controls ---
# Every triage run spends money on two external APIs, so a public endpoint
# needs a spend ceiling, not just a load limit.
#
# The daily budget is the control that actually bounds cost: it caps total
# runs per day across all callers. The per caller rate limits stop one
# source consuming that budget in a burst.
# Longest accepted query. Unbounded input goes straight into two paid
# models, so the cap is a cost control as much as a validation rule.
MAX_QUERY_CHARS = int(os.getenv("MAX_QUERY_CHARS", "4000"))

RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "5"))
RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "30"))
DAILY_RUN_BUDGET = int(os.getenv("DAILY_RUN_BUDGET", "200"))

# Optional. A caller presenting this in X-API-Key bypasses the rate limit,
# though not the daily budget. Not a browser secret: a static frontend
# cannot hold one privately, so this is for programmatic callers and for
# the operator rather than for authenticating end users.
API_KEY = os.getenv("API_KEY", "")

# Shared secret between the CDN and this service. When set, requests that
# do not carry it in X-Origin-Secret are refused, so the service can only be
# reached through the CDN and cannot be hit directly at its address,
# bypassing the edge controls. Unlike an API key sent from a browser, this
# value never leaves the CDN configuration and the server environment.
ORIGIN_SECRET = os.getenv("ORIGIN_SECRET", "")

# Only trust X-Forwarded-For when a proxy that rewrites it sits in front.
# The header is caller supplied, so trusting it on a directly reachable
# service lets any client forge its own rate limit identity.
TRUST_PROXY_HEADER = os.getenv("TRUST_PROXY_HEADER", "false").lower() == "true"

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Step inputs and outputs are verbose and can contain whole documents, so
# they only appear at DEBUG. INFO stays a readable one line per step.
LOG_PAYLOADS = os.getenv("LOG_PAYLOADS", "false").lower() == "true"

# --- Timeouts and retries ---
# The decision model normally answers in 240-370ms, but provider tail
# latency has been measured in seconds. A timeout plus bounded retry turns
# a stalled request into a fast recovery or a fast, explicit failure.
JEV_TIMEOUT_S = float(os.getenv("JEV_TIMEOUT_S", "5.0"))
JEV_MAX_RETRIES = int(os.getenv("JEV_MAX_RETRIES", "2"))

# --- Retrieval ---
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
CHUNK_SIZE_WORDS = 60
CHUNK_OVERLAP_WORDS = 15
TOP_K_CANDIDATES = 8  # ceiling on candidates pulled before reranking
TOP_K_FINAL = 4  # ceiling on chunks kept after reranking

# Reciprocal Rank Fusion constant. Vector similarity and BM25 sit on
# different scales that cannot be compared directly, and normalising them
# onto a shared range floors the weakest match in each signal to zero,
# making it indistinguishable from no match at all. RRF fuses on rank, so
# only the ordering matters. 60 is the value from the original RRF paper
# and results are not sensitive to it.
RRF_K = 60

# No score gate is applied before reranking. RRF scores barely spread: a
# chunk found by both signals at rank 1 scores 2/61, one found by a single
# signal at rank 8 scores 1/68, so the worst candidate is always around 45%
# of the best. A threshold there would gate on how many signals agreed
# rather than on relevance, and would systematically drop single signal
# hits, which are often the most relevant. TOP_K_CANDIDATES is the rank
# cap; MIN_RERANK_SCORE below is the relevance gate.

# Cross encoder scores are absolute and comparable across queries, so this
# is a real threshold. Below zero the reranker is reporting that the chunk
# does not answer the query, and it should not occupy a context slot. An
# off topic query scores every candidate well below zero and so returns no
# context at all, rather than the least irrelevant chunks available.
MIN_RERANK_SCORE = 0.0

# Set to True to run the agent without calling Neo4j or an LLM, using
# canned responses instead. Useful for testing the API and frontend
# wiring before you have a Neo4j instance or an API key set up.
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
