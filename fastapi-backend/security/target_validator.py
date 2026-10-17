"""
Target validation and SSRF protection.

Every scan endpoint must call ``validate_scan_target()`` before launching
any scanner process.  This prevents:
- Scanning localhost / 127.0.0.1
- Scanning private RFC-1918 ranges (10.x, 172.16-31.x, 192.168.x)
- Scanning cloud metadata endpoints (169.254.169.254)
- Scanning link-local, multicast, reserved, and unspecified addresses
- IPv6 loopback (::1)
- Scheme injection (ftp://, file://, etc.)
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlsplit

logger = logging.getLogger("target_validator")

# Schemes we allow
_ALLOWED_SCHEMES = {"http", "https"}


def normalize_target(value: str) -> tuple[str, str]:
    """Parse a user-supplied target into ``(full_url, hostname)``.

    Handles:
    - Bare hostnames (``example.com``)
    - URLs with auth info (``https://user:pass@example.com``)
    - IPv6 (``https://[2001:db8::1]:8443``)

    Raises ``ValueError`` for invalid or unsupported targets.
    """
    candidate = value.strip()
    if not candidate:
        raise ValueError("Target cannot be empty")

    # Add scheme if missing
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlsplit(candidate)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Unsupported scheme '{parsed.scheme}'. "
            f"Only {_ALLOWED_SCHEMES} are allowed."
        )

    hostname = parsed.hostname  # handles IPv6 brackets, strips port & auth
    if not hostname:
        raise ValueError(f"Target has no valid hostname: {value!r}")

    return candidate, hostname


async def validate_public_hostname(hostname: str) -> None:
    """Resolve *hostname* and reject private / internal addresses.

    Raises ``ValueError`` if any resolved address is non-public.
    """
    loop = asyncio.get_running_loop()

    try:
        addresses = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(hostname, None, socket.AF_UNSPEC),
        )
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    if not addresses:
        raise ValueError(f"No DNS records found for '{hostname}'")

    for entry in addresses:
        ip_text = entry[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(
                f"Target '{hostname}' resolves to a disallowed address: {ip}. "
                "Scanning internal/private networks is not permitted."
            )


async def validate_scan_target(raw_target: str) -> tuple[str, str]:
    """Full validation pipeline for a user-supplied scan target.

    Returns ``(normalized_url, hostname)`` or raises ``ValueError``.

    Steps:
    1. Normalize and parse the URL.
    2. Resolve DNS and reject private addresses.
    """
    url, hostname = normalize_target(raw_target)

    # Skip SSRF checks for explicitly allowed test targets
    _test_targets = {"scanme.nmap.org", "testphp.vulnweb.com", "juice-shop"}
    if hostname not in _test_targets:
        await validate_public_hostname(hostname)

    logger.info("Target validated: %s → %s", raw_target, hostname)
    return url, hostname
