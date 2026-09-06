"""Small helpers shared by the IP collector and DNS updater."""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable, List


# Keep the expression deliberately broad; ipaddress.ip_address() performs the
# actual validation of each candidate and filters private/reserved addresses.
IPV4_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def extract_public_ipv4(text: str) -> List[str]:
    """Return public IPv4 addresses in first-seen order, without duplicates."""

    result: List[str] = []
    seen = set()
    for candidate in IPV4_PATTERN.findall(text):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version != 4 or not address.is_global or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def unique_ips(values: Iterable[str]) -> List[str]:
    """Normalize and validate an iterable of IPv4 strings."""

    result: List[str] = []
    seen = set()
    for value in values:
        candidate = value.strip()
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version != 4 or not address.is_global or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result
