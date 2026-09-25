"""
Knowledge graph layer on Neo4j.

extract_and_store() reads each document, extracts (entity, relation,
entity) triples with the LLM, and MERGEs them into Neo4j so repeated runs
do not duplicate nodes or edges.

graph_context() matches entities named in a query against the graph and
walks outward from them, returning the relations found as structured
context to accompany the free text chunks from retrieval.
"""

import json
import logging
from typing import List, Dict
from neo4j import GraphDatabase

from app.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, DOCS_DIR
from app.config import (
    GRAPH_HOPS,
    MAX_ENTITY_DEGREE,
    ENTITY_BLOCKLIST,
    MIN_ENTITY_LEN,
    GRAPH_FACT_LIMIT,
)
from app.llm import extract_triples

log = logging.getLogger(__name__)
import os


def _driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def _read_docs() -> List[Dict[str, str]]:
    docs = []
    for filename in sorted(os.listdir(DOCS_DIR)):
        if not filename.endswith(".txt"):
            continue
        with open(os.path.join(DOCS_DIR, filename)) as f:
            content = f.read().strip()
        title, _, body = content.partition("\n\n")
        docs.append({"doc_id": filename, "title": title.strip(), "body": body.strip()})
    return docs


def _store_triples(tx, doc_id: str, triples: List[Dict[str, str]]):
    for t in triples:
        tx.run(
            """
            MERGE (a:Entity {name: $subject})
            MERGE (b:Entity {name: $object})
            MERGE (a)-[r:RELATION {type: $relation}]->(b)
            SET r.source_doc = $doc_id
            """,
            subject=t["subject"].strip().lower(),
            object=t["object"].strip().lower(),
            relation=t["relation"].strip().lower(),
            doc_id=doc_id,
        )


def extract_and_store() -> int:
    """
    Runs LLM triple extraction over every document and writes the result
    into Neo4j. Returns the total number of triples stored. Safe to
    re-run, MERGE means it will not duplicate nodes or edges, though it
    will refresh source_doc to whichever run touched the edge last.
    """
    docs = _read_docs()
    total = 0
    with _driver() as driver:
        with driver.session() as session:
            for doc in docs:
                triples = extract_triples(doc["title"], doc["body"])
                if triples:
                    session.execute_write(_store_triples, doc["doc_id"], triples)
                    total += len(triples)
    return total


def graph_context(
    entities: List[str], hops: int = GRAPH_HOPS
) -> List[Dict[str, str]]:
    """
    Walks out from the entities matched in the query and returns the edges
    found along the way, as (subject, relation, object, source_doc,
    hop_distance) dicts ready to hand to the LLM as grounding.

    Two filters do the real work here.

    Hub suppression: free text triple extraction collapses every machine
    in the fleet into the bare word "node", every accelerator into "gpu",
    and so on. Those become enormous hubs that connect hundreds of
    unrelated documents, so a query mentioning "node" would pull back
    facts about fabric managers, ECC settings and cooling, none of them
    related to each other. Anything above MAX_ENTITY_DEGREE, or on the
    blocklist, is excluded both as a match and as a step on a path.

    Path purity: the same filter is applied to every intermediate node,
    not just the endpoints, which is what stops a two hop walk from
    travelling through a hub and arriving somewhere irrelevant.
    """
    if not entities:
        return []

    # Interpolated rather than parameterised because Cypher does not allow
    # a parameter in a variable length pattern. Bounded to keep it honest.
    hops = max(1, min(3, int(hops)))

    query = f"""
        MATCH path = (a:Entity)-[:RELATION*1..{hops}]-(b:Entity)
        WHERE toLower(a.name) IN $names
          AND ALL(n IN nodes(path) WHERE
                trim(n.name) <> ''
                AND NOT toLower(n.name) IN $blocked
                AND (
                    COUNT {{ (n)-[:RELATION]-() }} <= $maxdeg
                    OR toLower(n.name) IN $names
                ))
        WITH path, length(path) AS hop_distance
        UNWIND relationships(path) AS r
        RETURN DISTINCT
               startNode(r).name AS subject,
               r.type            AS relation,
               endNode(r).name   AS object,
               r.source_doc      AS source_doc,
               hop_distance      AS hop_distance
        ORDER BY hop_distance, subject
        LIMIT $limit
    """

    log.debug("graph: walking up to %d hops from %d entities", hops, len(entities))
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                query,
                names=[e.lower() for e in entities],
                blocked=sorted(ENTITY_BLOCKLIST),
                maxdeg=MAX_ENTITY_DEGREE,
                limit=GRAPH_FACT_LIMIT,
            )
            return [dict(record) for record in result]


def all_entities() -> List[str]:
    """
    Entity names worth matching a query against.

    Excludes blocklisted generic nouns, anything too short to match
    meaningfully by substring, and hubs above MAX_ENTITY_DEGREE. Doing the
    filtering here means the agent's matcher never sees them at all.
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Entity)
                WITH a, COUNT { (a)-[:RELATION]-() } AS degree
                WHERE degree > 0
                  AND degree <= $maxdeg
                  AND trim(a.name) <> ''
                  AND size(a.name) >= $minlen
                  AND NOT toLower(a.name) IN $blocked
                RETURN a.name AS name
                """,
                maxdeg=MAX_ENTITY_DEGREE,
                minlen=MIN_ENTITY_LEN,
                blocked=sorted(ENTITY_BLOCKLIST),
            )
            return [record["name"] for record in result]
