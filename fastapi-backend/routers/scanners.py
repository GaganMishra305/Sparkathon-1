"""
Scanner API routes — thin HTTP controller.

All business logic lives in services/scanner_service.py and scanner adapters.
This layer handles only: request parsing, validation, and response formatting.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from scanners.base import (
    NmapOptions,
    SqlmapOptions,
    WpscanOptions,
    NiktoOptions,
    GobusterOptions,
)

from services import scanner_service

router = APIRouter()
logger = logging.getLogger("scanners_router")


# ---------------------------------------------------------------------------
# Request Schemas — typed options replace raw argument strings
# ---------------------------------------------------------------------------

class NmapPayload(BaseModel):
    """Nmap scan request with typed options."""
    target: str
    scan_type: str = Field(default="fast", description="fast | standard | comprehensive")
    timing: int = Field(default=3, ge=0, le=5)
    top_ports: Optional[int] = Field(default=None, ge=1, le=65535)


class SqlmapPayload(BaseModel):
    """SQLMap scan request with typed options."""
    target: str
    level: int = Field(default=1, ge=1, le=3)
    risk: int = Field(default=1, ge=1, le=2)
    timeout_seconds: int = Field(default=120, ge=10, le=600)
    test_forms: bool = False


class WpscanPayload(BaseModel):
    """WPScan request with typed options."""
    target: str
    max_threads: int = Field(default=1, ge=1, le=5)
    request_timeout: int = Field(default=30, ge=5, le=120)
    enumerate: Optional[str] = None


class NiktoPayload(BaseModel):
    """Nikto scan request."""
    url: Optional[str] = None
    target: Optional[str] = None
    tuning: Optional[str] = None
    timeout_seconds: int = Field(default=300, ge=30, le=900)


class ScanPayload(BaseModel):
    """Combined scan request (Gobuster + optional additional scanners)."""
    url: str
    run_nikto: bool = False
    run_nmap: bool = False
    run_sqlmap: bool = False
    test_url: Optional[str] = None
    run_wpscan: bool = False
    threads: int = Field(default=25, ge=1, le=50)
    status_codes: str = "200,204,301,302,307,401,403"


class ReportPayload(BaseModel):
    """AI report generation request."""
    scan_results: dict
    target_url: str = "Unknown Target"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/nmap/scan/")
async def scan_ports(payload: NmapPayload):
    if not payload.target:
        raise HTTPException(status_code=400, detail="Target URL is required")
    try:
        options = NmapOptions(
            scan_type=payload.scan_type,
            timing=payload.timing,
            top_ports=payload.top_ports,
        )
        result = await scanner_service.execute_nmap_scan(payload.target, options)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Nmap scan failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nmap/arguments/")
async def get_nmap_arguments():
    """Return available scan profiles and options."""
    return await scanner_service.get_nmap_arguments()


@router.post("/nmap/sqlmap/")
async def sqlmap_scan(payload: SqlmapPayload):
    if not payload.target:
        raise HTTPException(status_code=400, detail="Target URL is required")
    try:
        options = SqlmapOptions(
            level=payload.level,
            risk=payload.risk,
            timeout_seconds=payload.timeout_seconds,
            test_forms=payload.test_forms,
        )
        result = await scanner_service.execute_sqlmap_scan(payload.target, options)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("SQLMap scan failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nmap/wpscan/")
async def wpscan_scan(payload: WpscanPayload):
    if not payload.target:
        raise HTTPException(status_code=400, detail="Target URL is required")
    try:
        options = WpscanOptions(
            max_threads=payload.max_threads,
            request_timeout=payload.request_timeout,
            enumerate=payload.enumerate,
        )
        result = await scanner_service.execute_wpscan_scan(payload.target, options)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("WPScan failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gobuster/scan/")
async def scan_vulnerability(payload: ScanPayload):
    if not payload.url:
        raise HTTPException(status_code=400, detail="URL is required")
    try:
        options = GobusterOptions(
            threads=payload.threads,
            status_codes=payload.status_codes,
        )
        result = await scanner_service.execute_gobuster_scan(
            url=payload.url,
            run_nikto_scan=payload.run_nikto,
            run_nmap_scan=payload.run_nmap,
            run_sqlmap_scan=payload.run_sqlmap,
            test_url=payload.test_url,
            run_wpscan_scan=payload.run_wpscan,
            options=options,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Gobuster scan failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gobuster/nikto/")
async def nikto_scan(payload: NiktoPayload):
    target_url = payload.url or payload.target
    if not target_url:
        raise HTTPException(status_code=400, detail="URL/target is required")
    try:
        options = NiktoOptions(
            tuning=payload.tuning,
            timeout_seconds=payload.timeout_seconds,
        )
        result = await scanner_service.execute_nikto_scan(target_url, options)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Nikto scan failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nmap/report/generate/")
async def generate_ai_report(payload: ReportPayload):
    if not payload.scan_results:
        raise HTTPException(status_code=400, detail="Scan results are required")
    try:
        result = await scanner_service.generate_ai_report(
            payload.scan_results, payload.target_url
        )
        return result
    except Exception as e:
        logger.exception("Report generation failed")
        raise HTTPException(status_code=500, detail=str(e))
