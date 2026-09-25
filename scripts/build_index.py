"""Rebuilds the hybrid search index from data/docs. Run after adding docs."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.retrieval import build_index

if __name__ == "__main__":
    n = build_index()
    print(f"Indexed {n} chunks.")
