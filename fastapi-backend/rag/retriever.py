"""
Hybrid retriever: Vector + Keyword search with RRF fusion, MMR diversity, and reranking.

Pipeline:
1. Vector search (ChromaDB cosine similarity)
2. Keyword search (ChromaDB $contains filter)
3. Reciprocal Rank Fusion to combine results
4. MMR (Maximal Marginal Relevance) for diversity
5. Optional LLM reranking of top candidates
6. Parent expansion (fetch sibling methods when a method is retrieved)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("retriever")

# Default thresholds
# NOTE: MiniLM ONNX embeddings produce lower cosine similarities than OpenAI.
# A threshold of 0.25 is appropriate for this model to avoid filtering out
# relevant method-level chunks that typically score 0.30-0.40.
DEFAULT_RELEVANCE_THRESHOLD = 0.25
DEFAULT_TOP_K_INITIAL = 20
DEFAULT_TOP_K_FINAL = 8
RRF_K = 60  # RRF constant


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a user query for keyword search.

    Filters out stopwords and short tokens.
    """
    stopwords = {
        "the", "is", "in", "at", "of", "a", "an", "and", "or", "to", "for",
        "this", "that", "it", "on", "with", "as", "by", "from", "are", "was",
        "be", "has", "have", "had", "do", "does", "did", "not", "but", "if",
        "can", "will", "would", "should", "could", "may", "might", "what",
        "where", "when", "how", "why", "which", "who", "whom", "me", "my",
        "we", "our", "you", "your", "they", "their", "its", "i",
    }

    # Extract words, keep programming terms (underscores, dots)
    tokens = re.findall(r"[a-zA-Z_][\w.]*", query.lower())

    keywords = []
    for token in tokens:
        if token not in stopwords and len(token) > 2:
            keywords.append(token)

    return keywords[:10]  # Cap at 10 keywords


# ---------------------------------------------------------------------------
# Core retrieval functions
# ---------------------------------------------------------------------------

async def vector_search(
    collection,
    query_embedding: list[float],
    repo_id: str,
    n_results: int = DEFAULT_TOP_K_INITIAL,
) -> list[dict]:
    """ChromaDB vector search returning scored results."""
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where={"repo_id": repo_id},
        include=["documents", "metadatas", "distances"],
    )

    hits: list[dict] = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    ids = results.get("ids", [[]])[0]

    for doc, meta, dist, chunk_id in zip(docs, metas, dists, ids):
        # Cosine distance → similarity (clamped to [0, 1])
        similarity = max(0.0, min(1.0, 1.0 - dist))
        hits.append({
            "id": chunk_id,
            "document": doc,
            "metadata": meta,
            "similarity": similarity,
            "source": "vector",
        })

    return hits


async def keyword_search(
    collection,
    keywords: list[str],
    repo_id: str,
    n_results: int = DEFAULT_TOP_K_INITIAL,
) -> list[dict]:
    """ChromaDB keyword search using $contains filter.

    Searches for each keyword and unions the results.
    """
    if not keywords:
        return []

    all_hits: dict[str, dict] = {}

    for keyword in keywords[:5]:  # Limit to 5 keyword queries
        try:
            results = collection.query(
                query_texts=[keyword],
                n_results=min(n_results, 10),
                where={"repo_id": repo_id},
                include=["documents", "metadatas", "distances"],
            )

            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]
            ids = results.get("ids", [[]])[0]

            for doc, meta, dist, chunk_id in zip(docs, metas, dists, ids):
                if chunk_id not in all_hits:
                    # Check if keyword actually appears in the document
                    if keyword.lower() in doc.lower():
                        similarity = max(0.0, min(1.0, 1.0 - dist))
                        all_hits[chunk_id] = {
                            "id": chunk_id,
                            "document": doc,
                            "metadata": meta,
                            "similarity": similarity,
                            "source": "keyword",
                            "matched_keywords": [keyword],
                        }
                    else:
                        similarity = max(0.0, min(1.0, 1.0 - dist))
                        all_hits[chunk_id] = {
                            "id": chunk_id,
                            "document": doc,
                            "metadata": meta,
                            "similarity": similarity * 0.8,  # Slight penalty for non-exact match
                            "source": "keyword",
                            "matched_keywords": [keyword],
                        }
                else:
                    all_hits[chunk_id]["matched_keywords"].append(keyword)

        except Exception as e:
            logger.warning("Keyword search failed for '%s': %s", keyword, e)

    return list(all_hits.values())


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    vector_hits: list[dict],
    keyword_hits: list[dict],
    k: int = RRF_K,
) -> list[dict]:
    """Combine vector and keyword results using RRF.

    Score = sum(1 / (k + rank)) across all result lists.
    """
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    # Score vector results
    for rank, hit in enumerate(vector_hits):
        chunk_id = hit["id"]
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)
        docs[chunk_id] = hit

    # Score keyword results
    for rank, hit in enumerate(keyword_hits):
        chunk_id = hit["id"]
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)
        if chunk_id not in docs:
            docs[chunk_id] = hit
        else:
            # Merge keyword match info
            docs[chunk_id]["source"] = "hybrid"
            if "matched_keywords" in hit:
                existing_kw = docs[chunk_id].get("matched_keywords", [])
                docs[chunk_id]["matched_keywords"] = list(set(
                    existing_kw + hit.get("matched_keywords", [])
                ))

    # Sort by RRF score descending
    sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)

    results = []
    for chunk_id in sorted_ids:
        doc = docs[chunk_id]
        doc["rrf_score"] = scores[chunk_id]
        results.append(doc)

    return results


# ---------------------------------------------------------------------------
# Maximal Marginal Relevance (diversity)
# ---------------------------------------------------------------------------

def mmr_rerank(
    candidates: list[dict],
    top_k: int = DEFAULT_TOP_K_FINAL,
    lambda_param: float = 0.7,
) -> list[dict]:
    """Rerank candidates using MMR to ensure diversity.

    Avoids returning too many chunks from the same file, but exempts
    expansion chunks (class_expansion, sibling_expansion) from the
    full penalty since they provide essential class/method context.
    """
    if len(candidates) <= top_k:
        return candidates

    selected: list[dict] = []
    remaining = list(candidates)

    # Always include the top candidate
    if remaining:
        selected.append(remaining.pop(0))

    while len(selected) < top_k and remaining:
        best_score = -1.0
        best_idx = 0

        for i, candidate in enumerate(remaining):
            # Relevance score (from RRF or similarity)
            relevance = candidate.get("rrf_score", candidate.get("similarity", 0))

            # Expansion chunks get reduced diversity penalty since they
            # were explicitly fetched to provide class/method context
            is_expansion = candidate.get("source", "").endswith("_expansion")

            # Diversity penalty: reduce score if same file already selected
            max_similarity_to_selected = 0.0
            for sel in selected:
                if candidate.get("metadata", {}).get("file_path") == sel.get("metadata", {}).get("file_path"):
                    # Expansion chunks get a much lower penalty (0.2 vs 0.8)
                    max_similarity_to_selected = 0.2 if is_expansion else 0.8
                elif candidate.get("metadata", {}).get("parent_symbol") and \
                     candidate["metadata"]["parent_symbol"] == sel.get("metadata", {}).get("parent_symbol"):
                    max_similarity_to_selected = max(max_similarity_to_selected, 0.2 if is_expansion else 0.4)

            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_similarity_to_selected

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        selected.append(remaining.pop(best_idx))

    return selected


# ---------------------------------------------------------------------------
# Relevance filtering
# ---------------------------------------------------------------------------

def filter_by_relevance(
    hits: list[dict],
    threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
) -> list[dict]:
    """Remove results below the relevance threshold."""
    return [h for h in hits if h.get("similarity", 0) >= threshold]


# ---------------------------------------------------------------------------
# Parent expansion (fetch sibling methods)
# ---------------------------------------------------------------------------

async def expand_to_siblings(
    hits: list[dict],
    collection,
    repo_id: str,
    max_expansion: int = 5,
) -> list[dict]:
    """If a method chunk is retrieved, also fetch its sibling methods.
    If a class chunk is retrieved, fetch its child methods.

    This gives the LLM full class context when answering class-level questions.
    """
    expanded = list(hits)
    seen_ids = {h["id"] for h in expanded}
    expansion_count = 0

    for hit in hits:
        meta = hit.get("metadata", {})
        symbol_type = meta.get("symbol_type", "")
        parent = meta.get("parent_symbol")
        symbol_name = meta.get("symbol_name", "")

        if expansion_count >= max_expansion:
            break

        # Case 1: Class chunk retrieved → fetch its child methods
        if symbol_type == "class" and symbol_name:
            try:
                children = collection.get(
                    where={
                        "$and": [
                            {"repo_id": repo_id},
                            {"parent_symbol": symbol_name},
                        ]
                    },
                    include=["documents", "metadatas"],
                )

                for child_id, child_doc, child_meta in zip(
                    children.get("ids", []),
                    children.get("documents", []),
                    children.get("metadatas", []),
                ):
                    if child_id not in seen_ids and expansion_count < max_expansion:
                        expanded.append({
                            "id": child_id,
                            "document": child_doc,
                            "metadata": child_meta,
                            "similarity": 0.35,
                            "source": "class_expansion",
                        })
                        seen_ids.add(child_id)
                        expansion_count += 1

            except Exception as e:
                logger.warning("Class expansion failed for %s: %s", symbol_name, e)

        # Case 2: Method chunk retrieved → fetch its sibling methods
        elif parent:
            try:
                siblings = collection.get(
                    where={
                        "$and": [
                            {"repo_id": repo_id},
                            {"parent_symbol": parent},
                        ]
                    },
                    include=["documents", "metadatas"],
                )

                for sib_id, sib_doc, sib_meta in zip(
                    siblings.get("ids", []),
                    siblings.get("documents", []),
                    siblings.get("metadatas", []),
                ):
                    if sib_id not in seen_ids and expansion_count < max_expansion:
                        expanded.append({
                            "id": sib_id,
                            "document": sib_doc,
                            "metadata": sib_meta,
                            "similarity": 0.3,
                            "source": "sibling_expansion",
                        })
                        seen_ids.add(sib_id)
                        expansion_count += 1

            except Exception as e:
                logger.warning("Sibling expansion failed for %s: %s", parent, e)

    return expanded


# ---------------------------------------------------------------------------
# Main retrieval pipeline
# ---------------------------------------------------------------------------

async def hybrid_retrieve(
    collection,
    query: str,
    query_embedding: list[float],
    repo_id: str,
    *,
    top_k_initial: int = DEFAULT_TOP_K_INITIAL,
    top_k_final: int = DEFAULT_TOP_K_FINAL,
    relevance_threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
    expand_parents: bool = True,
    max_expansion: int = 5,
) -> list[dict]:
    """Full hybrid retrieval pipeline.

    Steps:
    1. Vector search (semantic similarity)
    2. Keyword search (exact term matching)
    3. RRF fusion
    4. Relevance threshold filtering
    5. Parent expansion (optional)
    6. MMR diversity reranking
    """
    keywords = extract_keywords(query)

    # Run vector and keyword searches concurrently
    vector_hits, keyword_hits = await asyncio.gather(
        vector_search(collection, query_embedding, repo_id, top_k_initial),
        keyword_search(collection, keywords, repo_id, top_k_initial),
    )

    logger.info(
        "Retrieval: %d vector hits, %d keyword hits for query '%s'",
        len(vector_hits), len(keyword_hits), query[:50],
    )

    # Fuse results
    if keyword_hits:
        fused = reciprocal_rank_fusion(vector_hits, keyword_hits)
    else:
        fused = vector_hits

    # Filter by relevance
    relevant = filter_by_relevance(fused, relevance_threshold)

    if not relevant:
        logger.warning("No results above relevance threshold %.2f", relevance_threshold)
        # Fall back to top results even if below threshold
        relevant = fused[:top_k_final] if fused else []

    # Expand to sibling methods
    if expand_parents and relevant:
        relevant = await expand_to_siblings(relevant, collection, repo_id, max_expansion=max_expansion)

    # MMR diversity reranking
    final = mmr_rerank(relevant, top_k=top_k_final)

    logger.info("Final retrieval: %d chunks after fusion+MMR", len(final))
    return final
