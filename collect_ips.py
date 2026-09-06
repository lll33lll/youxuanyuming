#!/usr/bin/env python3
"""Collect public Cloudflare IPv4 addresses into ``ip.txt``.

The upstream project scraped several pages with page-specific HTML selectors.
Those selectors broke whenever a page layout changed. This version only
extracts and validates IPv4 candidates, so it works for both plain-text and
HTML sources and has no third-party runtime dependency.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List
from urllib.error import URLError
from urllib.request import Request, urlopen

from ip_utils import extract_public_ipv4


ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
OUTPUT_FILE = ROOT / "ip.txt"
USER_AGENT = "youxuanyuming/1.0 (+https://github.com/lll33lll/youxuanyuming)"


def load_config() -> Dict:
    try:
        with CONFIG_FILE.open(encoding="utf-8") as file:
            config = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 {CONFIG_FILE.name}: {exc}") from exc

    sources = config.get("ip_sources")
    if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
        raise RuntimeError("config.json 的 ip_sources 必须是字符串数组")
    return config


def source_urls(config: Dict) -> List[str]:
    override = os.getenv("IP_SOURCES", "").strip()
    if override:
        return [item.strip() for item in override.split(",") if item.strip()]
    return [item.strip() for item in config["ip_sources"] if item.strip()]


def fetch_text(url: str) -> str:
    """Fetch a text URL with short retries and a bounded timeout."""

    last_error = "unknown error"
    for attempt in range(1, 4):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,text/plain;q=0.9,*/*;q=0.8",
                },
            )
            with urlopen(request, timeout=30) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except (OSError, URLError) as exc:
            last_error = str(exc)
            if attempt < 3:
                time.sleep(attempt)
    raise RuntimeError(last_error)


def collect(urls: Iterable[str]) -> List[str]:
    addresses: List[str] = []
    seen = set()
    successful_sources = 0

    for url in urls:
        print(f"[collect] {url}")
        try:
            text = fetch_text(url)
            found = extract_public_ipv4(text)
            successful_sources += 1
            print(f"         found {len(found)} public IPv4 address(es)")
            for address in found:
                if address not in seen:
                    seen.add(address)
                    addresses.append(address)
        except Exception as exc:  # one broken source must not hide the others
            print(f"         skipped: {exc}", file=sys.stderr)

    if successful_sources == 0:
        raise RuntimeError("所有 IP 来源均请求失败，保留旧的 ip.txt，不继续更新")
    if not addresses:
        raise RuntimeError("来源请求成功但没有找到公共 IPv4，保留旧的 ip.txt")
    return addresses


def write_atomically(addresses: List[str], max_records: int) -> None:
    if max_records < 1:
        raise RuntimeError("max_records 必须大于 0")
    selected = addresses[:max_records]
    temporary = OUTPUT_FILE.with_suffix(".txt.tmp")
    temporary.write_text("".join(f"{address}\n" for address in selected), encoding="utf-8")
    temporary.replace(OUTPUT_FILE)
    print(f"[collect] wrote {len(selected)} address(es) to {OUTPUT_FILE.name}")


def main() -> int:
    config = load_config()
    try:
        max_records = int(os.getenv("MAX_RECORDS", str(config.get("max_records", 20))))
    except ValueError as exc:
        raise RuntimeError("MAX_RECORDS 必须是整数") from exc

    addresses = collect(source_urls(config))
    write_atomically(addresses, max_records)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[collect] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
