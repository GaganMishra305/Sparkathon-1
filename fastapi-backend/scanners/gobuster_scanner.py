"""
Gobuster scanner adapter.

Key fixes from the review:
- Uses ``-s`` (status code allowlist) instead of ``-b`` (blacklist) which
  was excluding all useful responses.
- Improved regex supports nested paths, URL-encoded characters.
- Returns DiscoveredEndpoint findings instead of guessing file vs directory.
- Temp file cleanup in finally block.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime, timezone

from domain.command_runner import run_command
from domain.models import (
    Confidence,
    Finding,
    ScanResult,
    ScanStatus,
    Severity,
)
from scanners.base import GobusterOptions

logger = logging.getLogger("gobuster_scanner")


class GobusterScanner:
    name = "gobuster"

    def _build_command(
        self, target_url: str, output_file: str, options: GobusterOptions
    ) -> list[str]:
        cmd = [
            "gobuster", "dir",
            "--url", target_url,
            "--wordlist", options.wordlist,
            "--quiet",
            "-k",                            # skip TLS verification
            "-t", str(options.threads),
            # Use -s (status code allowlist) instead of -b (blacklist)
            "-s", options.status_codes,
            "--random-agent",
            "--timeout", "10s",
            "--output", output_file,
        ]
        if options.follow_redirects:
            cmd.append("-r")
        return cmd

    def _parse_output(self, raw_output: str) -> list[Finding]:
        """Parse Gobuster output into Findings.

        Improved regex supports:
        - Nested paths (/admin/config)
        - URL-encoded characters (%20)
        - Paths with special characters
        """
        findings: list[Finding] = []

        # Match lines like: /admin                 (Status: 403) [Size: 277]
        # or: /admin/config          (Status: 200) [Size: 1234]
        pattern = re.compile(
            r"^(/\S+)\s+"                          # path (anything non-space after /)
            r"\(Status:\s*(\d+)\)"                  # status code
            r"(?:\s*\[Size:\s*(\d+)\])?"            # optional size
        )

        for line in raw_output.splitlines():
            m = pattern.match(line.strip())
            if not m:
                continue

            path = m.group(1)
            status_code = int(m.group(2))
            size = int(m.group(3)) if m.group(3) else None

            # Determine severity based on status code and path
            severity = Severity.INFO
            confidence = Confidence.MEDIUM

            # Interesting paths
            sensitive_patterns = [
                "admin", "config", "backup", ".env", "debug",
                "phpmyadmin", "wp-admin", "cpanel", ".git",
                "server-status", "server-info",
            ]
            if any(sp in path.lower() for sp in sensitive_patterns):
                severity = Severity.MEDIUM
                if status_code in (200, 301, 302):
                    severity = Severity.HIGH

            if status_code == 403:
                # Forbidden but exists — could be interesting
                severity = max(severity, Severity.LOW, key=lambda s: list(Severity).index(s))

            description = f"Discovered endpoint: {path} (HTTP {status_code})"
            if size is not None:
                description += f", size: {size} bytes"

            findings.append(Finding(
                scanner=self.name,
                category="Directory/Endpoint Discovery",
                title=f"Endpoint found: {path} [{status_code}]",
                description=description,
                severity=severity,
                confidence=confidence,
                location=path,
                evidence=line.strip(),
            ))

        return findings

    async def scan(
        self,
        target: str,
        hostname: str,
        options: GobusterOptions | None = None,
    ) -> ScanResult:
        opts = options or GobusterOptions()
        target_url = target if target.startswith(("http://", "https://")) else f"http://{target}"

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
                temp_path = tmp.name

            cmd = self._build_command(target_url, temp_path, opts)
            logger.info("Running gobuster: %s", " ".join(cmd))

            started = datetime.now(timezone.utc)
            result = await run_command(cmd, timeout_seconds=opts.timeout_seconds)
            finished = datetime.now(timezone.utc)

            if result.timed_out:
                return ScanResult(
                    scanner=self.name,
                    target=target_url,
                    status=ScanStatus.TIMED_OUT,
                    started_at=started,
                    finished_at=finished,
                    duration_seconds=result.duration_seconds,
                    error="Gobuster scan timed out",
                    return_code=result.return_code,
                )

            # Read output file
            raw_output = ""
            if temp_path and os.path.exists(temp_path):
                with open(temp_path, "r") as f:
                    raw_output = f.read()

            findings = self._parse_output(raw_output)

            # Gobuster non-zero can just mean no results or a minor error
            status = ScanStatus.COMPLETED
            warnings = []
            if result.return_code not in (0, None) and not findings:
                status = ScanStatus.PARTIAL
                warnings.append(
                    f"Gobuster exited with code {result.return_code}. "
                    "Results may be incomplete."
                )

            return ScanResult(
                scanner=self.name,
                target=target_url,
                status=status,
                started_at=started,
                finished_at=finished,
                duration_seconds=result.duration_seconds,
                findings=findings,
                warnings=warnings,
                return_code=result.return_code,
                raw_output_reference=raw_output[:2000],
            )

        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
