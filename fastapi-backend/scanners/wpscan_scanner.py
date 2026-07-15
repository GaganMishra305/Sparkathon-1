"""
WPScan scanner adapter.

Parses WPScan's JSON output into typed Findings.
Exit codes 0 and 4 (warnings) → COMPLETED, others → FAILED.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from domain.command_runner import run_command
from domain.models import (
    Confidence,
    Finding,
    ScanResult,
    ScanStatus,
    Severity,
)
from scanners.base import WpscanOptions

logger = logging.getLogger("wpscan_scanner")


async def is_wordpress_site(url: str) -> bool:
    """Quick check for WordPress presence."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            response = await client.get(f"{url}/wp-login.php")
            return "WordPress" in response.text or "wp-login" in response.text
    except Exception:
        return False


class WpscanScanner:
    name = "wpscan"

    def _build_command(self, target_url: str, options: WpscanOptions) -> list[str]:
        cmd = [
            "wpscan",
            "--url", target_url,
            "--random-user-agent",
            "--disable-tls-checks",
            "--format", "json",
            "--no-banner",
            f"--max-threads", str(options.max_threads),
            f"--request-timeout", str(options.request_timeout),
            f"--connect-timeout", str(options.connect_timeout),
        ]
        if options.enumerate:
            cmd.extend(["--enumerate", options.enumerate])
        return cmd

    def _parse_json_output(self, stdout: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return findings

        # WordPress version info
        version_info = data.get("version")
        if version_info:
            ver_number = version_info.get("number", "unknown")
            status = version_info.get("status", "")
            if status == "insecure":
                findings.append(Finding(
                    scanner=self.name,
                    category="Outdated Software",
                    title=f"WordPress {ver_number} is outdated/insecure",
                    description=f"WordPress version {ver_number} is marked as {status}.",
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    remediation="Update WordPress to the latest stable version.",
                ))
            elif ver_number != "unknown":
                findings.append(Finding(
                    scanner=self.name,
                    category="Information Disclosure",
                    title=f"WordPress version {ver_number} detected",
                    severity=Severity.INFO,
                    confidence=Confidence.HIGH,
                ))

        # Core vulnerabilities
        for vuln in data.get("vulnerabilities", []):
            refs = []
            for ref_type, ref_list in vuln.get("references", {}).items():
                if isinstance(ref_list, list):
                    refs.extend(ref_list)
            findings.append(Finding(
                scanner=self.name,
                category="WordPress Core Vulnerability",
                title=vuln.get("title", "Unknown vulnerability"),
                description=vuln.get("description", ""),
                severity=Severity.HIGH,
                confidence=Confidence.HIGH,
                references=refs,
                remediation="Update WordPress core to the latest patched version.",
            ))

        # Plugin vulnerabilities
        for plugin_name, plugin_data in data.get("plugins", {}).items():
            for vuln in plugin_data.get("vulnerabilities", []):
                refs = []
                for ref_type, ref_list in vuln.get("references", {}).items():
                    if isinstance(ref_list, list):
                        refs.extend(ref_list)
                findings.append(Finding(
                    scanner=self.name,
                    category="Plugin Vulnerability",
                    title=f"Plugin '{plugin_name}': {vuln.get('title', 'Unknown')}",
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    location=f"plugin:{plugin_name}",
                    references=refs,
                    remediation=f"Update or remove the '{plugin_name}' plugin.",
                ))

        # Theme vulnerabilities
        for theme_name, theme_data in data.get("themes", {}).items():
            for vuln in theme_data.get("vulnerabilities", []):
                findings.append(Finding(
                    scanner=self.name,
                    category="Theme Vulnerability",
                    title=f"Theme '{theme_name}': {vuln.get('title', 'Unknown')}",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    location=f"theme:{theme_name}",
                    remediation=f"Update or remove the '{theme_name}' theme.",
                ))

        return findings

    async def scan(
        self,
        target: str,
        hostname: str,
        options: WpscanOptions | None = None,
    ) -> ScanResult:
        opts = options or WpscanOptions()
        target_url = target if target.startswith(("http://", "https://")) else f"https://{target}"
        cmd = self._build_command(target_url, opts)

        logger.info("Running wpscan: %s", " ".join(cmd))
        started = datetime.now(timezone.utc)
        result = await run_command(cmd, timeout_seconds=180, cwd="/tmp")
        finished = datetime.now(timezone.utc)

        if result.timed_out:
            return ScanResult(
                scanner=self.name,
                target=target_url,
                status=ScanStatus.TIMED_OUT,
                started_at=started,
                finished_at=finished,
                duration_seconds=result.duration_seconds,
                error="WPScan timed out",
                return_code=result.return_code,
            )

        # WPScan: 0 = clean, 4 = warnings present, others = error
        if result.return_code not in (0, 4, None):
            return ScanResult(
                scanner=self.name,
                target=target_url,
                status=ScanStatus.FAILED,
                started_at=started,
                finished_at=finished,
                duration_seconds=result.duration_seconds,
                error=result.stderr[:500] or f"WPScan exited with code {result.return_code}",
                return_code=result.return_code,
                warnings=["Target may not be a WordPress site or WPScan encountered an error."],
            )

        findings = self._parse_json_output(result.stdout)

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
