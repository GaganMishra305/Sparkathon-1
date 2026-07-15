"""
Core domain models for the scanner service.

Every scanner returns a ScanResult containing a list of Findings.
This eliminates the ad-hoc dictionary structures that previously caused
schema mismatches between scanners, formatters, and report generators.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ScanStatus(str, Enum):
    """Outcome of a scanner execution.

    COMPLETED  – tool ran and exited cleanly (may or may not have findings).
    PARTIAL    – tool produced some output but did not finish fully.
    FAILED     – tool crashed, invalid args, DNS failure, permission error, etc.
    TIMED_OUT  – exceeded the deadline.
    CANCELLED  – user or system cancelled the scan.
    """
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class Severity(str, Enum):
    """CVSS-inspired severity tiers.  Stored lowercase, displayed via .title()."""
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Confidence(str, Enum):
    """How certain the scanner is about the finding."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Finding — one discrete security observation
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    """A single security finding produced by a scanner.

    The ``id`` is a deterministic fingerprint so that duplicate findings
    from different scanners can be correlated.
    """
    id: str = ""
    scanner: str
    category: str = "General"
    title: str
    description: str = ""
    severity: Severity = Severity.INFO
    confidence: Confidence = Confidence.MEDIUM
    location: Optional[str] = None
    evidence: Optional[str] = None
    remediation: Optional[str] = None
    references: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: object) -> None:
        """Auto-generate a fingerprint id when not provided.

        The fingerprint deliberately excludes the scanner name so that
        identical findings from different tools (e.g., Nmap and Nikto both
        reporting the same open port) can be deduplicated.
        """
        if not self.id:
            raw = f"{self.category}:{self.title}:{self.location or ''}"
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# ScanResult — output of a single scanner adapter
# ---------------------------------------------------------------------------

class ScanResult(BaseModel):
    """Uniform output produced by every scanner adapter."""
    scanner: str
    target: str
    status: ScanStatus
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: float = 0.0
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    return_code: Optional[int] = None
    raw_output_reference: Optional[str] = None

    # --- Convenience helpers ------------------------------------------------

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def has_findings(self) -> bool:
        return self.finding_count > 0

    def findings_by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]


# ---------------------------------------------------------------------------
# CommandResult — raw subprocess output (not Pydantic, just a dataclass)
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    """Raw result from the common command runner."""
    command: list[str]
    return_code: Optional[int]
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


# ---------------------------------------------------------------------------
# Severity / Confidence scoring helpers
# ---------------------------------------------------------------------------

SEVERITY_SCORE: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 4,
    Severity.HIGH: 8,
    Severity.CRITICAL: 10,
}

CONFIDENCE_MULTIPLIER: dict[Confidence, float] = {
    Confidence.LOW: 0.4,
    Confidence.MEDIUM: 0.7,
    Confidence.HIGH: 1.0,
}


def calculate_risk_score(findings: list[Finding]) -> float:
    """Deterministic risk score from findings, with per-category caps."""
    category_scores: dict[str, float] = {}
    max_per_category = 30.0

    for f in findings:
        base = SEVERITY_SCORE.get(f.severity, 0)
        multiplier = CONFIDENCE_MULTIPLIER.get(f.confidence, 0.5)
        score = base * multiplier
        cat = f.category
        category_scores[cat] = min(
            category_scores.get(cat, 0.0) + score,
            max_per_category,
        )

    return round(sum(category_scores.values()), 2)


def risk_level_label(score: float) -> str:
    """Human-readable risk level from a numeric score."""
    if score >= 25:
        return "CRITICAL"
    elif score >= 15:
        return "HIGH"
    elif score >= 5:
        return "MEDIUM"
    elif score > 0:
        return "LOW"
    return "NONE"
