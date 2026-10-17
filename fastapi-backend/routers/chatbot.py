"""
Chatbot API routes — thin HTTP controller for the RAG pipeline.

Response schema is enriched with citations, limitations, and intent
while preserving backward compatibility (answer, relevantFiles, confidence, provider).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from services import rag_service

router = APIRouter()


# --- Request Schemas ---

class FileObject(BaseModel):
    path: str
    content: str
    size: Optional[int] = None


class CreateEmbeddingsPayload(BaseModel):
    repoId: str
    files: List[FileObject]


class ChatPayload(BaseModel):
    repoId: str
    query: str
    provider: Optional[str] = "groq"


class AnalyzeFilesPayload(BaseModel):
    files: List[FileObject]
    provider: Optional[str] = "gemini"
    query: Optional[str] = "Provide a comprehensive security and code quality analysis of this repository"


# --- Controller Routes ---

@router.post("/chatbot/embeddings")
async def create_embeddings(payload: CreateEmbeddingsPayload):
    if not payload.repoId:
        raise HTTPException(status_code=400, detail="repoId is required")

    files_list = [f.model_dump() for f in payload.files]
    result = await rag_service.create_embeddings(payload.repoId, files_list)
    return {
        "success": True,
        **result
    }


@router.post("/chatbot/chat")
async def chat_with_repository(payload: ChatPayload):
    """Chat with an indexed repository using the agentic RAG pipeline.

    Response includes:
    - answer: The generated analysis (markdown)
    - relevantFiles: List of files with similarity scores
    - confidence: Overall retrieval quality score
    - provider: LLM provider used
    - citations: List of {file, start_line, end_line, description}
    - limitations: List of evidence gaps or caveats
    - intent: Classified question intent
    - retrieval_quality: Average similarity of retrieved chunks
    - retrieval_attempts: Number of retrieval iterations used
    """
    if not payload.repoId or not payload.query:
        raise HTTPException(status_code=400, detail="repoId and query are required")

    try:
        result = await rag_service.chat_with_repository(
            payload.repoId, payload.query, payload.provider
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chatbot/embeddings/{repo_id:path}")
async def get_embeddings_status(repo_id: str):
    """Get indexing status including parser and symbol breakdowns."""
    try:
        result = await rag_service.get_embeddings_status(repo_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/chatbot/embeddings/{repo_id:path}")
async def delete_embeddings(repo_id: str):
    try:
        await rag_service.delete_embeddings(repo_id)
        return {
            "success": True,
            "message": f"Successfully deleted embeddings for repository: {repo_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chatbot/analysis/{repo_id:path}")
async def get_repository_analysis(repo_id: str, provider: Optional[str] = "gemini"):
    """Map-reduce repository analysis."""
    try:
        result = await rag_service.get_repository_analysis(repo_id, provider)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chatbot/analyze")
async def analyze_files(payload: AnalyzeFilesPayload):
    try:
        files_list = [f.model_dump() for f in payload.files]
        result = await rag_service.analyze_files(files_list, payload.query, payload.provider)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
