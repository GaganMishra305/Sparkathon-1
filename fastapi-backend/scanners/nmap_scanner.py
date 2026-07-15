"""
Nmap scanner adapter.

Uses subprocess (not python-nmap library) for consistency with other
scanners and to avoid global mutable state.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from domain.command_runner import run_command
from domain.models import (
    CommandResult,
    Confidence,
    Finding,
    ScanResult,
    ScanStatus,
    Severity,
)
from scanners.base import NmapOptions

logger = logging.getLogger("nmap_scanner")


class NmapScanner:
    name = "nmap"

    def _build_command(self, hostname: str, options: NmapOptions) -> list[str]:
        cmd = ["nmap"]

        # Map scan profile to flags
        profile_flags = {
            "fast": ["-F"],
            "standard": ["-sV"],
            "comprehensive": ["-sV", "-sC"],
        }
        cmd.extend(profile_flags.get(options.scan_type, ["-F"]))

        cmd.append(f"-T{options.timing}")
        cmd.append("--open")

        if options.top_ports:
            cmd.extend(["--top-ports", str(options.top_ports)])

        cmd.append(hostname)
        return cmd

    def _parse_output(self, result: CommandResult) -> list[Finding]:
        """Parse nmap stdout into structured Findings.

        Improved regex that separates service name, product, and version.
        """
        findings: list[Finding] = []

        # Match lines like: 22/tcp   open  ssh     OpenSSH 9.0
        pattern = re.compile(
            r"^(\d+)/(tcp|udp)\s+"      # port/protocol
            r"(\w+)\s+"                   # state
            r"(\S+)"                      # service name
            r"(?:\s+(.*))?$"              # optional product + version
        )

        for line in result.stdout.splitlines():
            m = pattern.match(line.strip())
            if not m:
                continue

            port = int(m.group(1))
            protocol = m.group(2)
            state = m.group(3)
            service = m.group(4)
            version_info = (m.group(5) or "").strip()

            # Determine severity based on service risk
            severity = Severity.INFO
            confidence = Confidence.HIGH

            risky_ports = {21, 23, 25, 110, 143, 445, 1433, 3306, 3389, 5432, 5900}
            if port in risky_ports:
                severity = Severity.MEDIUM
            elif port in {22, 8080, 8443}:
                severity = Severity.LOW

            description = f"Port {port}/{protocol} is open running {service}"
            if version_info:
                description += f" ({version_info})"

            findings.append(Finding(
                scanner=self.name,
                category="Open Port",
                title=f"Open port {port}/{protocol} — {service}",
                description=description,
                severity=severity,
                confidence=confidence,
                location=f"{port}/{protocol}",
                evidence=line.strip(),
            ))

        return findings

    async def scan(
        self,
        target: str,
        hostname: str,
        options: NmapOptions | None = None,
    ) -> ScanResult:
        opts = options or NmapOptions()
        cmd = self._build_command(hostname, opts)

        logger.info("Running nmap: %s", " ".join(cmd))
        started = datetime.now(timezone.utc)
        result = await run_command(cmd, timeout_seconds=180)
        finished = datetime.now(timezone.utc)

        if result.timed_out:
            return ScanResult(
                scanner=self.name,
                target=target,
                status=ScanStatus.TIMED_OUT,
                started_at=started,
                finished_at=finished,
                duration_seconds=result.duration_seconds,
                error="Nmap scan timed out",
                return_code=result.return_code,
                raw_output_reference=result.stdout[:2000],
            )

        if result.return_code not in (0, None):
            return ScanResult(
                scanner=self.name,
                target=target,
                status=ScanStatus.FAILED,
                started_at=started,
                finished_at=finished,
                duration_seconds=result.duration_seconds,
                error=result.stderr[:500] or f"Nmap exited with code {result.return_code}",
                return_code=result.return_code,
                raw_output_reference=result.stdout[:2000],
            )

        findings = self._parse_output(result)

        return ScanResult(
            scanner=self.name,
            target=target,
            status=ScanStatus.COMPLETED,
            started_at=started,
            finished_at=finished,
            duration_seconds=result.duration_seconds,
            findings=findings,
            return_code=result.return_code,
            raw_output_reference=result.stdout[:2000],
        )
