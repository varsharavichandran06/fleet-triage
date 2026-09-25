"""
Sliding window chunker.

Splits each document into overlapping fixed size word windows. Window
size and overlap are set by CHUNK_SIZE_WORDS and CHUNK_OVERLAP_WORDS.
"""

from dataclasses import dataclass
from typing import List
import os

from app.config import DOCS_DIR, CHUNK_SIZE_WORDS, CHUNK_OVERLAP_WORDS


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    text: str


def _read_docs():
    for filename in sorted(os.listdir(DOCS_DIR)):
        if not filename.endswith(".txt"):
            continue
        path = os.path.join(DOCS_DIR, filename)
        with open(path) as f:
            content = f.read().strip()
        title, _, body = content.partition("\n\n")
        yield filename, title.strip(), body.strip()


def chunk_document(doc_id: str, doc_title: str, body: str) -> List[Chunk]:
    words = body.split()
    if not words:
        return []
    chunks = []
    step = CHUNK_SIZE_WORDS - CHUNK_OVERLAP_WORDS
    start = 0
    idx = 0
    while start < len(words):
        window = words[start : start + CHUNK_SIZE_WORDS]
        text = " ".join(window)
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}::chunk{idx}",
                doc_id=doc_id,
                doc_title=doc_title,
                text=text,
            )
        )
        idx += 1
        start += step
    return chunks


def load_all_chunks() -> List[Chunk]:
    all_chunks: List[Chunk] = []
    for doc_id, title, body in _read_docs():
        all_chunks.extend(chunk_document(doc_id, title, body))
    return all_chunks
