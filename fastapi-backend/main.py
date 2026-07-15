import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import scanners, chatbot
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="VulnPilot FastAPI Backend", version="1.0.0")

# CORS middleware config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
async def health_check():
    return {
        "status": "OK",
        "service": "FastAPI Scanner & Chatbot Backend"
    }

# Include routers
app.include_router(scanners.router, prefix="/api")
app.include_router(chatbot.router, prefix="/api")
