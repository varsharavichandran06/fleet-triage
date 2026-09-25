"""
Runs LLM based triple extraction over every document in data/docs and
writes the results into Neo4j. Run this once after build_index.py, and
again any time you add or change documents.

Requires a running Neo4j instance (see README) and, unless DRY_RUN=true,
a working LLM_API_KEY in your .env.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.graph import extract_and_store

if __name__ == "__main__":
    n = extract_and_store()
    print(f"Stored {n} triples in Neo4j.")
