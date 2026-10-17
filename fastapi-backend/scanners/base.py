"""
Scanner adapter protocol and typed option models.

Every scanner implements ``ScannerAdapter`` and accepts a typed options
model instead of raw argument strings.  This prevents argument injection
and makes each scanner's capabilities self-documenting.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from domain.models import ScanResult


# ---------------------------------------------------------------------------
# Protocol — every scanner must implement this
# ---------------------------------------------------------------------------

@runtime_checkable
class ScannerAdapter(Protocol):
    """Interface that every scanner adapter must satisfy."""
    name: str

    async def scan(self, target: str, hostname: str, options: BaseModel | None = None) -> ScanResult:
        """Run the scanner against *target* (full URL) with *hostname* extracted."""
        ...


# ---------------------------------------------------------------------------
# Typed option models — replace raw ``arguments: str``
# ---------------------------------------------------------------------------

class NmapOptions(BaseModel):
    """Safe, typed options for Nmap scans."""
    scan_type: str = Field(
        default="fast",
        description="Scan profile: 'fast' (-F), 'standard' (-sV), 'comprehensive' (-sV -sC)"
    )
    timing: int = Field(default=3, ge=0, le=5, description="Nmap timing template (T0-T5)")
    top_ports: Optional[int] = Field(default=None, ge=1, le=65535, description="Scan top N ports")


class SqlmapOptions(BaseModel):
    """Safe, typed options for SQLMap scans."""
    level: int = Field(default=1, ge=1, le=3, description="SQLMap test level (1-3)")
    risk: int = Field(default=1, ge=1, le=2, description="SQLMap risk level (1-2)")
    timeout_seconds: int = Field(default=120, ge=10, le=600)
    test_forms: bool = Field(default=False, description="Test HTML forms for injection")


class WpscanOptions(BaseModel):
    """Safe, typed options for WPScan."""
    max_threads: int = Field(default=1, ge=1, le=5)
    request_timeout: int = Field(default=30, ge=5, le=120)
    connect_timeout: int = Field(default=10, ge=5, le=60)
    enumerate: Optional[str] = Field(
        default=None,
        description="Enumerate: 'vp' (vulnerable plugins), 'vt' (vulnerable themes), 'u' (users)"
    )


class NiktoOptions(BaseModel):
    """Safe, typed options for Nikto scans."""
    tuning: Optional[str] = Field(
        default=None,
        description="Nikto scan tuning (e.g., '1' for interesting files, '2' for misconfig)"
    )
    timeout_seconds: int = Field(default=300, ge=30, le=900)


class GobusterOptions(BaseModel):
    """Safe, typed options for Gobuster directory scans."""
    wordlist: str = Field(default="/usr/share/wordlists/dirb/common.txt")
    threads: int = Field(default=25, ge=1, le=50)
    timeout_seconds: int = Field(default=300, ge=30, le=900)
    status_codes: str = Field(
        default="200,204,301,302,307,401,403",
        description="Comma-separated HTTP status codes to include"
    )
    follow_redirects: bool = Field(default=False)


# ---------------------------------------------------------------------------
# Registry of available scanner options for the /arguments endpoint
# ---------------------------------------------------------------------------

SCANNER_PROFILES = {
    "nmap": {
        "fast": {"description": "Quick scan of top 100 ports", "flags": "-F"},
        "standard": {"description": "Service version detection", "flags": "-sV"},
        "comprehensive": {"description": "Version + script scanning", "flags": "-sV -sC"},
    },
    "sqlmap": {
        "safe": {"description": "Level 1, Risk 1 — minimal impact", "level": 1, "risk": 1},
        "standard": {"description": "Level 2, Risk 1 — moderate testing", "level": 2, "risk": 1},
        "thorough": {"description": "Level 3, Risk 2 — deep testing", "level": 3, "risk": 2},
    },
}
