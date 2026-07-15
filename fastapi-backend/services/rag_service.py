"""
RAG service — rewritten with agentic pipeline.

Fixes applied:
1. ChromaDB cosine distance configuration
2. Concurrent embeddings with semaphore (not sequential)
3. Path-component file filtering (not substring)
4. Atomic reindexing with index_version
5. Provider validation (explicit errors)
6. Chat delegates to agentic pipeline (intent → retrieve → check → answer → verify)
7. Repository analysis uses map-reduce (not truncation)
8. Code-aware chunking via Tree-sitter → Regex → Line fallback
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

import google.generativeai as genai
import chromadb
from groq import Groq
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

from rag.chunker import CodeChunk, chunk_code_file, detect_language
from rag.agent import run_agent_pipeline
from rag.prompts import FILE_SUMMARY_PROMPT, REPO_ANALYSIS_PROMPT, SYSTEM_SECURITY_ANALYST

logger = logging.getLogger("rag_service")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    logger.warning("GEMINI_API_KEY environment variable is not set!")

groq_client: Optional[Groq] = None
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)

# ChromaDB with COSINE distance (Fix #1)
CHROMA_DATA_DIR = os.path.join(os.getcwd(), "data", "chroma")
os.makedirs(CHROMA_DATA_DIR, exist_ok=True)
chroma_client = chromadb.PersistentClient(path=CHROMA_DATA_DIR)
collection = chroma_client.get_or_create_collection(
    name="repo_chunks",
    metadata={"hnsw:space": "cosine"},  # Explicit cosine distance
)

# Initialize local ONNX-based embedding function (free, runs on CPU)
onnx_ef = ONNXMiniLM_L6_V2()

# Embedding concurrency control (Fix #2)
EMBEDDING_SEMAPHORE = asyncio.Semaphore(5)

# File filtering (Fix #3)
IGNORED_DIRECTORIES = {
    "node_modules", ".git", "dist", "build", "coverage",
    "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
    ".pytest_cache", "vendor", "target",
}

IGNORED_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Pipfile.lock", "poetry.lock", "composer.lock",
    "Gemfile.lock", "Cargo.lock",
}

ALLOWED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb",
    ".php", ".cs", ".cpp", ".c", ".swift", ".kt", ".rs",
    ".vue", ".html", ".css", ".scss", ".json", ".yaml", ".yml",
    ".xml", ".toml", ".cfg", ".ini", ".env.example",
}


# ---------------------------------------------------------------------------
# Embedding generation
# ---------------------------------------------------------------------------

async def generate_embedding(text: str, is_query: bool = False) -> List[float]:
    """Generate local embeddings using ChromaDB's built-in ONNX MiniLM model.
    
    This runs 100% locally on CPU, is free, and requires no API keys.
    """
    async with EMBEDDING_SEMAPHORE:
        def get_vector():
            return onnx_ef([text])[0]
        return await asyncio.to_thread(get_vector)


async def _batch_embed(texts: List[str], is_query: bool = False) -> List[Optional[List[float]]]:
    """Embed multiple texts concurrently with semaphore control. (Fix #2)"""
    tasks = [generate_embedding(text, is_query) for text in texts]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    embeddings: List[Optional[List[float]]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Embedding failed: %s", r)
            embeddings.append(None)
        else:
            embeddings.append(r)

    return embeddings


# ---------------------------------------------------------------------------
# File filtering (Fix #3)
# ---------------------------------------------------------------------------

def _should_index_file(path_str: str, content: str, max_file_size: int = 100000) -> bool:
    """Check if a file should be indexed using path COMPONENTS, not substrings."""
    if not path_str or not content:
        return False

    path = PurePosixPath(path_str)

    # Check directory components (not substring match)
    if set(path.parts) & IGNORED_DIRECTORIES:
        return False

    # Check ignored filenames
    if path.name in IGNORED_FILES:
        return False

    # Check extension
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False

    # Check size
    file_size = len(content)
    if file_size > max_file_size:
        return False

    return True


# ---------------------------------------------------------------------------
# LLM call wrappers
# ---------------------------------------------------------------------------

async def _llm_call_gemini(
    prompt: str,
    *,
    system_instruction: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    """Call Gemini LLM."""
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini provider requested but GEMINI_API_KEY is not configured")

    def call():
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=system_instruction or None,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            },
        )
        response = model.generate_content(prompt)
        return response.text

    return await asyncio.to_thread(call)


async def _llm_call_groq(
    prompt: str,
    *,
    system_instruction: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> str:
    """Call Groq LLM."""
    if not groq_client:
        raise RuntimeError("Groq provider requested but GROQ_API_KEY is not configured")

    def call():
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_instruction or SYSTEM_SECURITY_ANALYST},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return completion.choices[0].message.content or ""

    return await asyncio.to_thread(call)


def _get_llm_call(provider: str):
    """Get the LLM call function for the specified provider. (Fix #5)"""
    if provider == "groq":
        if not groq_client:
            raise RuntimeError("Groq provider requested but GROQ_API_KEY is not configured")
        return _llm_call_groq
    else:
        if not GEMINI_API_KEY:
            raise RuntimeError("Gemini provider requested but GEMINI_API_KEY is not configured")
        return _llm_call_gemini


# ---------------------------------------------------------------------------
# Indexing (Fix #4: Atomic reindexing)
# ---------------------------------------------------------------------------

async def create_embeddings(repo_id: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Index repository files using code-aware chunking and concurrent embeddings.

    Uses atomic indexing: new chunks are indexed under a new version,
    and the old version is deleted only after the new index is complete.
    """
    logger.info("Creating embeddings for repository: %s", repo_id)

    # Generate a new index version for atomic reindexing
    index_version = str(uuid.uuid4())[:8]

    # Filter files using path-component checks (Fix #3)
    filtered_files = [
        f for f in files
        if _should_index_file(f.get("path", ""), f.get("content", ""))
    ]

    logger.info("Filtered %d → %d files for indexing", len(files), len(filtered_files))

    total_chunks = 0
    batch_size = 20  # Process chunks in batches of 20

    all_ids: List[str] = []
    all_embeddings: List[List[float]] = []
    all_metadatas: List[Dict[str, Any]] = []
    all_documents: List[str] = []

    for file_data in filtered_files:
        path = file_data.get("path", "")
        content = file_data.get("content", "")
        language = detect_language(path)

        # Code-aware chunking (Fix: Tree-sitter → Regex → Line)
        chunks = chunk_code_file(content, path)

        for chunk in chunks:
            chunk_id = f"{repo_id}_{index_version}_{path}_chunk_{chunk.chunk_index}"

            # Build metadata — ChromaDB requires simple types only
            metadata = {
                "repo_id": repo_id,
                "index_version": index_version,
                "file_path": chunk.file_path,
                "language": chunk.language,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "symbol_type": chunk.symbol_type,
                "symbol_name": chunk.symbol_name,
                "chunk_index": chunk.chunk_index,
                "part_index": chunk.part_index,
                "part_count": chunk.part_count,
                "parser_used": chunk.parser_used,
                "content_hash": chunk.content_hash,
            }
            if chunk.parent_symbol:
                metadata["parent_symbol"] = chunk.parent_symbol
            if chunk.parent_type:
                metadata["parent_type"] = chunk.parent_type
            if chunk.imports:
                metadata["imports"] = ",".join(chunk.imports[:10])

            all_ids.append(chunk_id)
            all_metadatas.append(metadata)
            all_documents.append(chunk.content)

    # Batch embed all chunks concurrently (Fix #2)
    logger.info("Embedding %d chunks concurrently...", len(all_documents))

    for i in range(0, len(all_documents), batch_size):
        batch_docs = all_documents[i:i + batch_size]
        batch_embeddings = await _batch_embed(batch_docs, is_query=False)

        for emb in batch_embeddings:
            if emb is not None:
                all_embeddings.append(emb)
            else:
                # Use a zero vector as placeholder for failed embeddings
                all_embeddings.append([0.0] * 768)

    # Filter out chunks with failed embeddings
    valid_indices = [
        j for j, emb in enumerate(all_embeddings)
        if any(v != 0.0 for v in emb)
    ]

    valid_ids = [all_ids[j] for j in valid_indices]
    valid_embeddings = [all_embeddings[j] for j in valid_indices]
    valid_metadatas = [all_metadatas[j] for j in valid_indices]
    valid_documents = [all_documents[j] for j in valid_indices]
    total_chunks = len(valid_ids)

    # Add new index to ChromaDB
    if valid_ids:
        # ChromaDB has a batch limit, so add in chunks of 100
        chroma_batch_size = 100
        for i in range(0, len(valid_ids), chroma_batch_size):
            collection.add(
                ids=valid_ids[i:i + chroma_batch_size],
                embeddings=valid_embeddings[i:i + chroma_batch_size],
                metadatas=valid_metadatas[i:i + chroma_batch_size],
                documents=valid_documents[i:i + chroma_batch_size],
            )

    # Atomic swap: delete old versions AFTER new index is complete (Fix #4)
    try:
        old_results = collection.get(where={"repo_id": repo_id})
        old_ids = [
            oid for oid, meta in zip(old_results["ids"], old_results["metadatas"])
            if meta.get("index_version") != index_version
        ]
        if old_ids:
            # Delete in batches
            for i in range(0, len(old_ids), 100):
                collection.delete(ids=old_ids[i:i + 100])
            logger.info("Deleted %d old chunks for repo %s", len(old_ids), repo_id)
    except Exception as e:
        logger.warning("Failed to clean old index versions: %s", e)

    logger.info(
        "Indexed %d files → %d chunks for repo %s (version %s)",
        len(filtered_files), total_chunks, repo_id, index_version,
    )

    return {
        "repoId": repo_id,
        "fileCount": len(filtered_files),
        "totalChunks": total_chunks,
        "indexVersion": index_version,
    }


# ---------------------------------------------------------------------------
# Chat (Fix #6: Agentic pipeline)
# ---------------------------------------------------------------------------

async def chat_with_repository(
    repo_id: str,
    query: str,
    provider: str = "gemini",
) -> Dict[str, Any]:
    """Chat with a repository using the agentic RAG pipeline.

    Pipeline: classify_intent → retrieve → check_evidence → answer → verify
    """
    # Validate provider (Fix #5)
    llm_call = _get_llm_call(provider)

    # Verify embeddings exist
    repo_check = collection.get(where={"repo_id": repo_id}, limit=1)
    if not repo_check or not repo_check["ids"]:
        raise ValueError("No embeddings found for this repository. Please index first.")

    # Run the agentic pipeline
    result = await run_agent_pipeline(
        repo_id=repo_id,
        question=query,
        provider=provider,
        collection=collection,
        embed_fn=generate_embedding,
        llm_call=llm_call,
    )

    return result


# ---------------------------------------------------------------------------
# Embeddings status & deletion
# ---------------------------------------------------------------------------

async def get_embeddings_status(repo_id: str) -> Dict[str, Any]:
    results = collection.get(where={"repo_id": repo_id})
    exists = len(results["ids"]) > 0
    file_paths = list(set(
        meta.get("file_path") for meta in results["metadatas"]
    )) if exists else []

    # Get chunking stats
    parser_counts: Dict[str, int] = {}
    symbol_counts: Dict[str, int] = {}
    for meta in results["metadatas"]:
        parser = meta.get("parser_used", "unknown")
        parser_counts[parser] = parser_counts.get(parser, 0) + 1
        sym_type = meta.get("symbol_type", "unknown")
        symbol_counts[sym_type] = symbol_counts.get(sym_type, 0) + 1

    return {
        "repoId": repo_id,
        "exists": exists,
        "fileCount": len(file_paths),
        "totalChunks": len(results["ids"]),
        "files": file_paths,
        "parserBreakdown": parser_counts,
        "symbolBreakdown": symbol_counts,
    }


async def delete_embeddings(repo_id: str) -> bool:
    collection.delete(where={"repo_id": repo_id})
    return True


# ---------------------------------------------------------------------------
# Repository analysis (Fix #7: Map-reduce)
# ---------------------------------------------------------------------------

async def get_repository_analysis(
    repo_id: str,
    provider: str = "gemini",
) -> Dict[str, Any]:
    """Map-reduce repository analysis.

    Step 1 (Map): Summarize each file independently
    Step 2 (Reduce): Aggregate file summaries into a repository-level analysis
    """
    llm_call = _get_llm_call(provider)

    results = collection.get(where={"repo_id": repo_id})
    if not results["ids"]:
        raise ValueError("No embeddings found for this repository. Please index first.")

    # Group chunks by file
    file_chunks: Dict[str, list] = {}
    for doc, meta in zip(results["documents"], results["metadatas"]):
        path = meta.get("file_path", "unknown")
        if path not in file_chunks:
            file_chunks[path] = []
        file_chunks[path].append({
            "content": doc,
            "symbol_name": meta.get("symbol_name", ""),
            "symbol_type": meta.get("symbol_type", ""),
        })

    # Map phase: summarize each file (limited concurrency)
    map_semaphore = asyncio.Semaphore(3)
    file_summaries: list[str] = []

    async def summarize_file(path: str, chunks: list) -> str:
        async with map_semaphore:
            # Reconstruct file content from chunks
            content = "\n\n".join(c["content"] for c in chunks)
            language = detect_language(path)

            prompt = FILE_SUMMARY_PROMPT.format(
                file_path=path,
                language=language,
                content=content[:5000],  # Cap per-file content
            )

            try:
                summary = await llm_call(prompt, max_tokens=500, temperature=0.2)
                return f"### {path}\n{summary}"
            except Exception as e:
                logger.warning("File summary failed for %s: %s", path, e)
                symbols = [f"{c['symbol_type']}:{c['symbol_name']}" for c in chunks if c["symbol_name"]]
                return f"### {path}\nSymbols: {', '.join(symbols)}\n(Summary generation failed)"

    # Limit to top 30 files for analysis
    top_files = list(file_chunks.items())[:30]
    tasks = [summarize_file(path, chunks) for path, chunks in top_files]
    file_summaries = await asyncio.gather(*tasks)

    # Reduce phase: aggregate into repository analysis
    all_summaries = "\n\n".join(file_summaries)

    prompt = REPO_ANALYSIS_PROMPT.format(file_summaries=all_summaries[:30000])
    analysis = await llm_call(
        prompt,
        system_instruction=SYSTEM_SECURITY_ANALYST,
        max_tokens=4096,
        temperature=0.3,
    )

    return {
        "repoId": repo_id,
        "analysis": analysis,
        "fileCount": len(file_chunks),
        "timestamp": str(datetime.now()),
        "provider": provider,
    }


# ---------------------------------------------------------------------------
# Direct file analysis (unchanged API, improved prompts)
# ---------------------------------------------------------------------------

async def analyze_files(
    files: List[Dict[str, Any]],
    query: str,
    provider: str = "gemini",
) -> Dict[str, Any]:
    llm_call = _get_llm_call(provider)

    context_parts = []
    for file in files:
        path = file.get("path", "unknown")
        content = file.get("content", "")
        context_parts.append(
            f"=== BEGIN UNTRUSTED FILE: {path} ===\n{content[:5000]}\n=== END FILE ==="
        )

    context = "\n\n".join(context_parts)[:30000]

    prompt = f"""Analyze the following code files and answer the user's question.
User Question: {query}

{context}

Provide a concise analysis focusing on:
1. Security vulnerabilities
2. Code quality issues
3. Specific recommendations
Keep response under 1000 words."""

    analysis = await llm_call(
        prompt,
        system_instruction=SYSTEM_SECURITY_ANALYST,
        max_tokens=2048,
        temperature=0.3,
    )

    return {"analysis": analysis}
