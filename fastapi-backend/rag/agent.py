"""
Agentic RAG pipeline — intent → retrieve → check → answer → verify.

This is a lightweight agent implemented as plain async functions with a
state dictionary. No LangGraph or external orchestration framework needed.

Pipeline:
1. classify_intent()   — Route question to the right retrieval strategy
2. retrieve_evidence() — Hybrid retrieval via retriever.py
3. check_evidence()    — LLM judges if evidence is sufficient
4. generate_answer()   — Synthesize answer with citations
5. verify_citations()  — Strip hallucinated citations
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional, TypedDict

import google.generativeai as genai

from rag.prompts import (
    ANSWER_PROMPT_BASE,
    EVIDENCE_CHECK_PROMPT,
    INTENT_CLASSIFICATION_PROMPT,
    INTENT_INSTRUCTIONS,
    SYSTEM_SECURITY_ANALYST,
)
from rag.retriever import hybrid_retrieve

logger = logging.getLogger("rag_agent")

MAX_RETRIEVAL_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Agent State
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    repo_id: str
    question: str
    intent: str
    evidence: list[dict]
    retrieval_attempts: int
    sufficient: bool
    answer: str
    citations: list[dict]
    limitations: list[str]
    retrieval_quality: float
    provider: str


# ---------------------------------------------------------------------------
# Node 1: Intent Classification
# ---------------------------------------------------------------------------

async def classify_intent(
    state: AgentState,
    llm_call,
) -> AgentState:
    """Classify the user's question into an intent category.

    Different intents trigger different retrieval strategies:
    - security_review → more keyword-heavy, focus on auth/injection terms
    - explain_code → more semantic, focus on function names
    - trace_flow → need imports, routes, middleware
    """
    prompt = INTENT_CLASSIFICATION_PROMPT.format(question=state["question"])

    try:
        response = await llm_call(prompt, max_tokens=50, temperature=0.0)
        intent = response.strip().lower().replace('"', '').replace("'", '')

        valid_intents = {
            "explain_code", "find_bug", "security_review",
            "architecture", "trace_flow", "general",
        }
        if intent not in valid_intents:
            intent = "general"

    except Exception as e:
        logger.warning("Intent classification failed: %s. Defaulting to 'general'", e)
        intent = "general"

    state["intent"] = intent
    logger.info("Classified intent: %s for question: %s", intent, state["question"][:60])
    return state


# ---------------------------------------------------------------------------
# Node 2: Evidence Retrieval
# ---------------------------------------------------------------------------

async def retrieve_evidence(
    state: AgentState,
    collection,
    embed_fn,
    query_override: Optional[str] = None,
) -> AgentState:
    """Retrieve evidence using hybrid retrieval.

    Adjusts retrieval parameters based on intent.
    """
    query = query_override or state["question"]
    intent = state.get("intent", "general")

    # Adjust retrieval parameters by intent
    top_k_initial = 20
    top_k_final = 8
    expand_parents = True

    if intent == "security_review":
        top_k_initial = 25
        top_k_final = 10
    elif intent == "architecture":
        top_k_initial = 15
        top_k_final = 8
        expand_parents = False  # Want diverse files, not sibling methods
    elif intent == "trace_flow":
        top_k_initial = 25
        top_k_final = 12
        expand_parents = True

    # Adjust for Groq's low TPM limits (6000 TPM) on free tiers
    max_expansion = 5
    if state.get("provider") == "groq":
        top_k_initial = min(top_k_initial, 12)
        top_k_final = 6  # Enough room for class + its expanded methods
        expand_parents = True
        max_expansion = 3  # Fetch up to 3 child/sibling methods per class

    query_embedding = await embed_fn(query, is_query=True)

    hits = await hybrid_retrieve(
        collection=collection,
        query=query,
        query_embedding=query_embedding,
        repo_id=state["repo_id"],
        top_k_initial=top_k_initial,
        top_k_final=top_k_final,
        expand_parents=expand_parents,
        max_expansion=max_expansion,
    )

    state["evidence"] = hits
    state["retrieval_attempts"] = state.get("retrieval_attempts", 0) + 1

    # Calculate retrieval quality
    if hits:
        avg_similarity = sum(h.get("similarity", 0) for h in hits) / len(hits)
        state["retrieval_quality"] = round(avg_similarity, 3)
    else:
        state["retrieval_quality"] = 0.0

    logger.info(
        "Retrieved %d chunks (attempt %d), avg similarity: %.3f",
        len(hits), state["retrieval_attempts"], state.get("retrieval_quality", 0),
    )
    return state


# ---------------------------------------------------------------------------
# Node 3: Evidence Sufficiency Check
# ---------------------------------------------------------------------------

async def check_evidence(
    state: AgentState,
    llm_call,
) -> AgentState:
    """Ask the LLM whether retrieved evidence is sufficient to answer.

    If insufficient and under max attempts, provides rewritten queries.
    """
    evidence = state.get("evidence", [])

    # If we've exhausted attempts, force sufficient
    if state.get("retrieval_attempts", 0) >= MAX_RETRIEVAL_ATTEMPTS:
        state["sufficient"] = True
        if not evidence:
            state["limitations"] = state.get("limitations", [])
            state["limitations"].append(
                "Evidence retrieval reached maximum attempts without finding sufficient context."
            )
        return state

    # If no evidence at all, insufficient
    if not evidence:
        state["sufficient"] = False
        return state

    # Build evidence summary for the LLM
    evidence_summary_parts = []
    for i, hit in enumerate(evidence[:10]):
        meta = hit.get("metadata", {})
        path = meta.get("file_path", "unknown")
        symbol = meta.get("symbol_name", "")
        symbol_type = meta.get("symbol_type", "")
        sim = hit.get("similarity", 0)
        doc_preview = hit.get("document", "")[:200]
        evidence_summary_parts.append(
            f"Chunk {i+1}: {path} | {symbol_type}:{symbol} | similarity:{sim:.2f}\n"
            f"Preview: {doc_preview}..."
        )

    evidence_summary = "\n\n".join(evidence_summary_parts)

    prompt = EVIDENCE_CHECK_PROMPT.format(
        question=state["question"],
        intent=state.get("intent", "general"),
        evidence_summary=evidence_summary,
    )

    try:
        response = await llm_call(prompt, max_tokens=500, temperature=0.1)
        parsed = _parse_json_response(response)

        state["sufficient"] = parsed.get("sufficient", True)

        if not state["sufficient"]:
            rewritten = parsed.get("rewritten_queries", [])
            missing = parsed.get("missing_information", [])
            state["limitations"] = state.get("limitations", [])
            state["limitations"].extend(
                f"Missing: {item}" for item in missing[:3]
            )
            # Store rewritten queries for the next retrieval attempt
            state["_rewritten_queries"] = rewritten[:2]
            logger.info("Evidence insufficient. Missing: %s", missing)
        else:
            logger.info("Evidence deemed sufficient.")

    except Exception as e:
        logger.warning("Evidence check failed: %s. Assuming sufficient.", e)
        state["sufficient"] = True

    return state


# ---------------------------------------------------------------------------
# Node 4: Answer Generation
# ---------------------------------------------------------------------------

async def generate_answer(
    state: AgentState,
    llm_call,
) -> AgentState:
    """Generate a grounded answer with citations from the evidence."""
    evidence = state.get("evidence", [])
    intent = state.get("intent", "general")

    if not evidence:
        state["answer"] = (
            "I couldn't find relevant code in the repository to answer your question. "
            "Please ensure the repository has been indexed and try a more specific query."
        )
        state["citations"] = []
        state["limitations"] = ["No relevant evidence was retrieved from the repository."]
        return state

    # Build context from evidence
    context_parts = []
    for i, hit in enumerate(evidence):
        meta = hit.get("metadata", {})
        path = meta.get("file_path", "unknown")
        start_line = meta.get("start_line", "?")
        end_line = meta.get("end_line", "?")
        symbol = meta.get("symbol_name", "")
        symbol_type = meta.get("symbol_type", "")
        parent = meta.get("parent_symbol", "")

        header = f"[Chunk {i+1}] File: {path} | Lines: {start_line}-{end_line}"
        if symbol:
            header += f" | {symbol_type}: {symbol}"
        if parent:
            header += f" (in {parent})"

        context_parts.append(f"{header}\n```\n{hit.get('document', '')}\n```")

    context = "\n\n".join(context_parts)
    intent_instructions = INTENT_INSTRUCTIONS.get(intent, INTENT_INSTRUCTIONS["general"])

    prompt = ANSWER_PROMPT_BASE.format(
        question=state["question"],
        context=context,
        intent_instructions=intent_instructions,
    )

    try:
        response = await llm_call(
            prompt,
            system_instruction=SYSTEM_SECURITY_ANALYST,
            max_tokens=4096,
            temperature=0.3,
        )

        parsed = _parse_json_response(response)

        state["answer"] = parsed.get("answer", response)
        state["citations"] = parsed.get("citations", [])

        # Merge limitations
        existing_limitations = state.get("limitations", [])
        new_limitations = parsed.get("limitations", [])
        state["limitations"] = list(set(existing_limitations + new_limitations))

    except Exception as e:
        logger.error("Answer generation failed: %s", e)
        state["answer"] = f"I encountered an error while generating the analysis: {str(e)}"
        state["citations"] = []
        state["limitations"] = ["Answer generation encountered an error."]

    return state


# ---------------------------------------------------------------------------
# Node 5: Citation Verification
# ---------------------------------------------------------------------------

async def verify_citations(state: AgentState) -> AgentState:
    """Validate that cited file paths exist in the retrieved evidence.

    Strips hallucinated citations and adds limitations for gaps.
    """
    evidence = state.get("evidence", [])
    citations = state.get("citations", [])

    if not citations:
        return state

    # Build set of known file paths from evidence
    known_files = set()
    for hit in evidence:
        meta = hit.get("metadata", {})
        path = meta.get("file_path", "")
        if path:
            known_files.add(path)

    # Verify each citation
    verified: list[dict] = []
    stripped_count = 0

    for citation in citations:
        cited_file = citation.get("file", "")
        if cited_file in known_files:
            verified.append(citation)
        else:
            # Check partial match (basename)
            basename = cited_file.split("/")[-1] if "/" in cited_file else cited_file
            partial_match = any(basename in f for f in known_files)
            if partial_match:
                verified.append(citation)
            else:
                stripped_count += 1
                logger.warning("Stripped hallucinated citation: %s", cited_file)

    state["citations"] = verified

    if stripped_count > 0:
        state["limitations"] = state.get("limitations", [])
        state["limitations"].append(
            f"{stripped_count} citation(s) were removed because the referenced "
            "files were not found in the retrieved evidence."
        )

    return state


# ---------------------------------------------------------------------------
# Main Pipeline Orchestrator
# ---------------------------------------------------------------------------

async def run_agent_pipeline(
    repo_id: str,
    question: str,
    provider: str,
    collection,
    embed_fn,
    llm_call,
) -> dict[str, Any]:
    """Run the full agentic RAG pipeline.

    Steps:
    1. Classify intent
    2. Retrieve evidence (with loop if insufficient)
    3. Generate grounded answer
    4. Verify citations

    Returns a response dict compatible with the existing API contract.
    """
    state: AgentState = {
        "repo_id": repo_id,
        "question": question,
        "intent": "general",
        "evidence": [],
        "retrieval_attempts": 0,
        "sufficient": False,
        "answer": "",
        "citations": [],
        "limitations": [],
        "retrieval_quality": 0.0,
        "provider": provider,
    }

    # Step 1: Classify intent
    state = await classify_intent(state, llm_call)

    # Step 2: Retrieve evidence (with sufficiency loop)
    state = await retrieve_evidence(state, collection, embed_fn)
    state = await check_evidence(state, llm_call)

    # Retrieval loop: if insufficient, rewrite query and try again
    while not state.get("sufficient", True):
        rewritten_queries = state.pop("_rewritten_queries", [])
        if rewritten_queries:
            query = rewritten_queries[0]
            logger.info("Rewriting query to: %s", query)
            state = await retrieve_evidence(
                state, collection, embed_fn, query_override=query
            )
        else:
            state = await retrieve_evidence(state, collection, embed_fn)

        state = await check_evidence(state, llm_call)

    # Step 3: Generate answer
    state = await generate_answer(state, llm_call)

    # Step 4: Verify citations
    state = await verify_citations(state)

    # Build response (backward compatible)
    evidence = state.get("evidence", [])
    relevant_files = []
    seen_files = set()
    for hit in evidence:
        meta = hit.get("metadata", {})
        path = meta.get("file_path", "unknown")
        if path not in seen_files:
            seen_files.add(path)
            relevant_files.append({
                "path": path,
                "score": hit.get("similarity", 0),
                "symbol": meta.get("symbol_name", ""),
                "symbol_type": meta.get("symbol_type", ""),
            })

    return {
        "answer": state.get("answer", ""),
        "relevantFiles": relevant_files,
        "confidence": state.get("retrieval_quality", 0),
        "provider": provider,
        "citations": state.get("citations", []),
        "limitations": state.get("limitations", []),
        "intent": state.get("intent", "general"),
        "retrieval_quality": state.get("retrieval_quality", 0),
        "retrieval_attempts": state.get("retrieval_attempts", 0),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> dict:
    """Extract JSON from an LLM response, handling markdown code fences."""
    # Strip markdown code fences
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        # Try to find JSON within the text
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(), strict=False)
            except json.JSONDecodeError:
                pass
        return {"answer": text}
