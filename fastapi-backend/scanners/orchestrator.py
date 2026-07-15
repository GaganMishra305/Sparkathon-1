"""
Scan orchestrator — runs multiple scanner adapters concurrently.

Key improvements:
- All scanners start simultaneously (not Gobuster-first-then-others)
- Concurrency semaphore prevents resource exhaustion
- Target validation happens once before any scanner runs
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from domain.models import ScanResult, ScanStatus
from scanners.base import ScannerAdapter
from scanners.gobuster_scanner import GobusterScanner
from scanners.nikto_scanner import NiktoScanner
from scanners.nmap_scanner import NmapScanner
from scanners.sqlmap_scanner import SqlmapScanner
from scanners.wpscan_scanner import WpscanScanner
from security.target_validator import validate_scan_target

logger = logging.getLogger("orchestrator")

# Global instances (stateless, safe to share)
SCANNER_REGISTRY: dict[str, ScannerAdapter] = {
    "nmap": NmapScanner(),
    "sqlmap": SqlmapScanner(),
    "wpscan": WpscanScanner(),
    "nikto": NiktoScanner(),
    "gobuster": GobusterScanner(),
}


class ScanOrchestrator:
    """Orchestrates concurrent scanner execution with resource control."""

    def __init__(self, max_concurrent_tools: int = 5):
        self._semaphore = asyncio.Semaphore(max_concurrent_tools)

    async def _run_one(
        self,
        scanner: ScannerAdapter,
        target: str,
        hostname: str,
        options=None,
    ) -> ScanResult:
        """Run a single scanner within the semaphore."""
        async with self._semaphore:
            try:
                return await scanner.scan(target, hostname, options)
            except Exception as e:
                logger.error("Scanner %s failed: %s", scanner.name, e)
                return ScanResult(
                    scanner=scanner.name,
                    target=target,
                    status=ScanStatus.FAILED,
                    error=str(e),
                )

    async def run_scans(
        self,
        raw_target: str,
        scanner_names: list[str],
        scanner_options: Optional[dict] = None,
    ) -> list[ScanResult]:
        """Validate target, then run requested scanners concurrently.

        Args:
            raw_target: User-supplied target URL or hostname.
            scanner_names: List of scanner names to run (e.g., ["nmap", "nikto"]).
            scanner_options: Optional dict mapping scanner name to options model.

        Returns:
            List of ScanResult, one per requested scanner.
        """
        # Validate target once
        url, hostname = await validate_scan_target(raw_target)

        options_map = scanner_options or {}
        tasks = []

        for name in scanner_names:
            scanner = SCANNER_REGISTRY.get(name)
            if not scanner:
                logger.warning("Unknown scanner requested: %s", name)
                tasks.append(
                    asyncio.coroutine(lambda n=name: ScanResult(
                        scanner=n,
                        target=url,
                        status=ScanStatus.FAILED,
                        error=f"Unknown scanner: {n}",
                    ))()
                )
                continue

            opts = options_map.get(name)
            tasks.append(self._run_one(scanner, url, hostname, opts))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert any uncaught exceptions to ScanResult
        final: list[ScanResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                name = scanner_names[i] if i < len(scanner_names) else "unknown"
                final.append(ScanResult(
                    scanner=name,
                    target=url,
                    status=ScanStatus.FAILED,
                    error=str(result),
                ))
            else:
                final.append(result)

        return final


# Singleton orchestrator
orchestrator = ScanOrchestrator(max_concurrent_tools=5)
