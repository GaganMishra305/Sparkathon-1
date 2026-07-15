"""
SQLMap scanner adapter.

Key improvements over the old code:
- Non-zero return codes → FAILED (not "no vulnerabilities found")
- Negative sentences ("does not appear to be injectable") are excluded
- Typed SqlmapOptions instead of raw argument strings
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from domain.command_runner import run_command
from domain.models import (
    Confidence,
    Finding,
    ScanResult,
    ScanStatus,
    Severity,
)
from scanners.base import SqlmapOptions

logger = logging.getLogger("sqlmap_scanner")

# Patterns that indicate a CONFIRMED injection
_POSITIVE_PATTERNS = [
    re.compile(r"Parameter '([^']+)' is vulnerable", re.IGNORECASE),
    re.compile(r"(\w+) parameter '([^']+)' (?:is|appears to be) (?:dynamic|injectable)", re.IGNORECASE),
]

# Patterns that negate a finding — must be checked BEFORE positive
_NEGATIVE_PATTERNS = [
    re.compile(r"does not appear to be injectable", re.IGNORECASE),
    re.compile(r"not injectable", re.IGNORECASE),
    re.compile(r"all tested parameters do not appear", re.IGNORECASE),
]


class SqlmapScanner:
    name = "sqlmap"

    def _build_command(self, target_url: str, options: SqlmapOptions) -> list[str]:
        cmd = [
            "python3", "/opt/sqlmap/sqlmap.py",
            "--url", target_url,
            "--batch",
            "--random-agent",
            "--text-only",
            "--output-dir", "/tmp/sqlmap_output",
            "--timeout", "30",
            "--threads", "1",
            f"--level={options.level}",
            f"--risk={options.risk}",
        ]
        if options.test_forms:
            cmd.append("--forms")
        return cmd

    def _parse_output(self, stdout: str) -> list[Finding]:
        findings: list[Finding] = []

        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Check negative patterns first — skip the line if it's a negation
            if any(neg.search(stripped) for neg in _NEGATIVE_PATTERNS):
                continue

            for pattern in _POSITIVE_PATTERNS:
                m = pattern.search(stripped)
                if m:
                    param = m.group(1) if m.lastindex and m.lastindex >= 1 else "unknown"
                    findings.append(Finding(
                        scanner=self.name,
                        category="Injection Vulnerabilities",
                        title=f"SQL Injection — parameter '{param}'",
                        description=stripped,
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        location=param,
                        evidence=stripped,
                        remediation="Use parameterized queries or prepared statements. Never concatenate user input into SQL.",
                        references=["https://owasp.org/www-community/attacks/SQL_Injection"],
                    ))
                    break  # don't double-count on the same line

        return findings

    async def scan(
        self,
        target: str,
        hostname: str,
        options: SqlmapOptions | None = None,
    ) -> ScanResult:
        opts = options or SqlmapOptions()
        # Ensure target has scheme
        target_url = target if target.startswith(("http://", "https://")) else f"https://{target}"
        cmd = self._build_command(target_url, opts)

        logger.info("Running sqlmap: %s", " ".join(cmd))
        started = datetime.now(timezone.utc)
        result = await run_command(cmd, timeout_seconds=opts.timeout_seconds, cwd="/tmp")
        finished = datetime.now(timezone.utc)

        if result.timed_out:
            return ScanResult(
                scanner=self.name,
                target=target_url,
                status=ScanStatus.TIMED_OUT,
                started_at=started,
                finished_at=finished,
                duration_seconds=result.duration_seconds,
                error="SQLMap scan timed out",
                return_code=result.return_code,
                raw_output_reference=result.stdout[:2000],
            )

        # Non-zero return ≠ "no vulnerabilities". It means the tool FAILED.
        if result.return_code not in (0, None):
            return ScanResult(
                scanner=self.name,
                target=target_url,
                status=ScanStatus.FAILED,
                started_at=started,
                finished_at=finished,
                duration_seconds=result.duration_seconds,
                error=result.stderr[:500] or f"SQLMap exited with code {result.return_code}",
                return_code=result.return_code,
                raw_output_reference=result.stdout[:2000],
                warnings=["A failed scan gives unknown security status, not a clean result."],
            )

        findings = self._parse_output(result.stdout)

        return ScanResult(
            scanner=self.name,
            target=target_url,
            status=ScanStatus.COMPLETED,
            started_at=started,
            finished_at=finished,
            duration_seconds=result.duration_seconds,
            findings=findings,
            return_code=result.return_code,
            raw_output_reference=result.stdout[:2000],
        )
