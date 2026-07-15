"""
Nikto scanner adapter.

Key improvements:
- Temp file cleanup in finally block (no leak on exception)
- Distinguishes INFORMATIONAL / OBSERVATION / POTENTIAL findings
  instead of marking everything as "vulnerability"
- Uses common command runner with timeout
"""

from __future__ import annotations

import json
import logging
import os
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
from scanners.base import NiktoOptions

logger = logging.getLogger("nikto_scanner")


class NiktoScanner:
    name = "nikto"

    def _build_command(
        self, target_url: str, output_file: str, options: NiktoOptions
    ) -> list[str]:
        cmd = [
            "nikto",
            "-h", target_url,
            "-Format", "json",
            "-o", output_file,
        ]
        if options.tuning:
            cmd.extend(["-Tuning", options.tuning])
        return cmd

    def _classify_finding(self, msg: str) -> tuple[str, Severity, Confidence]:
        """Classify a Nikto message into category, severity, and confidence.

        Unlike keyword-only matching, this checks for negation and uses
        context-aware grouping.
        """
        msg_lower = msg.lower()

        # Skip informational lines
        info_keywords = [
            "target ip:", "host:", "start time:", "server:", "retrieved",
            "end time:", "requests:", "error(s):",
        ]
        if any(kw in msg_lower for kw in info_keywords):
            return "Informational", Severity.INFO, Confidence.HIGH

        # Check for negation — if the message says something is NOT found, it's info
        negation_phrases = [
            "not found", "is not", "no ", "does not", "aren't", "cannot",
        ]

        # Injection / XSS indicators
        injection_keywords = ["sql", "injection", "xss", "script", "exploit", "rce", "command"]
        if any(kw in msg_lower for kw in injection_keywords):
            if any(neg in msg_lower for neg in negation_phrases):
                return "Injection Vulnerabilities", Severity.INFO, Confidence.LOW
            return "Injection Vulnerabilities", Severity.HIGH, Confidence.MEDIUM

        # Authentication issues
        auth_keywords = ["admin", "password", "login", "authentication", "bypass", "credential"]
        if any(kw in msg_lower for kw in auth_keywords):
            if any(neg in msg_lower for neg in negation_phrases):
                return "Authentication Issues", Severity.INFO, Confidence.LOW
            return "Authentication Issues", Severity.MEDIUM, Confidence.MEDIUM

        # Configuration issues
        config_keywords = ["config", "configuration", "backup", "sensitive", "phpinfo", ".env"]
        if any(kw in msg_lower for kw in config_keywords):
            return "Configuration Issues", Severity.MEDIUM, Confidence.MEDIUM

        # Missing security headers
        header_keywords = ["x-frame-options", "x-content-type", "strict-transport", "content-security-policy"]
        if any(kw in msg_lower for kw in header_keywords):
            return "Missing Security Headers", Severity.LOW, Confidence.HIGH

        # Directory/file disclosure
        dir_keywords = ["directory", "listing", "indexing", "browse"]
        if any(kw in msg_lower for kw in dir_keywords):
            return "Information Disclosure", Severity.MEDIUM, Confidence.MEDIUM

        # Server info leakage
        info_keywords = ["version", "banner", "server", "header", "powered-by"]
        if any(kw in msg_lower for kw in info_keywords):
            return "Information Leakage", Severity.LOW, Confidence.HIGH

        # Default: observation
        return "General", Severity.LOW, Confidence.LOW

    def _parse_json_file(self, file_content: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            data = json.loads(file_content)
        except json.JSONDecodeError:
            return findings

        # Nikto JSON can be a list or dict with 'hosts' key
        hosts = []
        if isinstance(data, list):
            hosts = data
        elif isinstance(data, dict):
            hosts = data.get("hosts", [data])

        for host_entry in hosts:
            if not isinstance(host_entry, dict):
                continue
            items = host_entry.get("vulnerabilities", host_entry.get("items", []))
            for item in items:
                if not isinstance(item, dict):
                    continue
                msg = item.get("msg", item.get("description", str(item)))
                uri = item.get("uri", item.get("url", ""))
                method = item.get("method", "GET")
                osvdb_id = item.get("OSVDB", item.get("id", ""))

                category, severity, confidence = self._classify_finding(msg)

                # Skip pure informational noise
                if severity == Severity.INFO and category == "Informational":
                    continue

                refs = []
                if osvdb_id:
                    refs.append(f"OSVDB-{osvdb_id}")

                findings.append(Finding(
                    scanner=self.name,
                    category=category,
                    title=msg[:120],
                    description=msg,
                    severity=severity,
                    confidence=confidence,
                    location=uri,
                    evidence=f"{method} {uri}" if uri else None,
                    references=refs,
                ))

        return findings

    def _parse_raw_stdout(self, stdout: str) -> list[Finding]:
        """Fallback parser for raw Nikto text output."""
        findings: list[Finding] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("+"):
                continue
            text = line[1:].strip()
            if not text:
                continue

            category, severity, confidence = self._classify_finding(text)
            if severity == Severity.INFO and category == "Informational":
                continue

            findings.append(Finding(
                scanner=self.name,
                category=category,
                title=text[:120],
                description=text,
                severity=severity,
                confidence=confidence,
            ))

        return findings

    async def scan(
        self,
        target: str,
        hostname: str,
        options: NiktoOptions | None = None,
    ) -> ScanResult:
        opts = options or NiktoOptions()
        target_url = target if target.startswith(("http://", "https://")) else f"https://{target}"

        # Create temp file for JSON output — always cleaned up in finally
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+", delete=False, suffix=".json"
            ) as tmp:
                temp_path = tmp.name

            cmd = self._build_command(target_url, temp_path, opts)
            logger.info("Running nikto: %s", " ".join(cmd))

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
                    error="Nikto scan timed out",
                    return_code=result.return_code,
                )

            # Try JSON file first, fall back to stdout parsing
            findings: list[Finding] = []
            if temp_path and os.path.exists(temp_path):
                with open(temp_path, "r") as f:
                    file_content = f.read().strip()
                if file_content:
                    findings = self._parse_json_file(file_content)

            # If JSON parsing yielded nothing, parse stdout
            if not findings and result.stdout:
                findings = self._parse_raw_stdout(result.stdout)

            status = ScanStatus.COMPLETED
            if result.return_code not in (0, None) and not findings:
                status = ScanStatus.FAILED

            return ScanResult(
                scanner=self.name,
                target=target_url,
                status=status,
                started_at=started,
                finished_at=finished,
                duration_seconds=result.duration_seconds,
                findings=findings,
                return_code=result.return_code,
                raw_output_reference=result.stdout[:2000],
            )

        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
