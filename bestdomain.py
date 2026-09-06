#!/usr/bin/env python3
"""Synchronize the collected IPs to Cloudflare DNS records.

Only records created by this project are changed. Existing unmarked A records
are deliberately rejected instead of being silently deleted.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ip_utils import extract_public_ipv4, unique_ips


ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
IP_FILE = ROOT / "ip.txt"
CF_API = "https://api.cloudflare.com/client/v4"
USER_AGENT = "youxuanyuming/1.0 (+https://github.com/lll33lll/youxuanyuming)"
MANAGED_COMMENT = "managed-by=youxuanyuming"
DEFAULT_BESTCF_SOURCE = "https://ip.164746.xyz/ipTop10.html"


class CloudflareError(RuntimeError):
    """An actionable Cloudflare API error."""


def as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_config() -> Dict:
    try:
        with CONFIG_FILE.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 {CONFIG_FILE.name}: {exc}") from exc


def decode_response(raw: bytes, headers) -> str:
    charset = headers.get_content_charset() if headers else None
    return raw.decode(charset or "utf-8", errors="replace")


def fetch_text(source: str) -> str:
    """Fetch a source URL with bounded retries, or read a local file."""

    if source.startswith("file://"):
        value = source[len("file://") :]
        path = Path(value)
        if not path.is_absolute():
            path = ROOT / path
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"无法读取本地来源 {path}: {exc}") from exc

    last_error = "unknown error"
    for attempt in range(1, 4):
        try:
            request = Request(
                source,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,text/plain;q=0.9,*/*;q=0.8",
                },
            )
            with urlopen(request, timeout=30) as response:
                return decode_response(response.read(), response.headers)
        except (OSError, URLError) as exc:
            last_error = str(exc)
            if attempt < 3:
                time.sleep(attempt)
    raise RuntimeError(f"来源请求失败 {source}: {last_error}")


def api_call(
    token: str,
    method: str,
    path: str,
    params: Dict | None = None,
    body: Dict | None = None,
) -> Dict:
    """Call Cloudflare's API and fail on both HTTP and API-level errors."""

    url = f"{CF_API}{path}"
    if params:
        url += "?" + urlencode(params)
    encoded_body = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=encoded_body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urlopen(request, timeout=30) as response:
            status = response.status
            raw = response.read()
            headers = response.headers
    except HTTPError as exc:
        status = exc.code
        raw = exc.read()
        headers = exc.headers
    except (OSError, URLError) as exc:
        raise CloudflareError(f"请求 Cloudflare 失败: {exc}") from exc

    try:
        payload = json.loads(decode_response(raw, headers))
    except (TypeError, ValueError) as exc:
        raise CloudflareError(f"Cloudflare 返回了非 JSON 响应（HTTP {status}）") from exc

    if not (200 <= status < 300) or not payload.get("success"):
        errors = payload.get("errors") or []
        detail = "; ".join(str(item.get("message", item)) for item in errors)
        raise CloudflareError(f"Cloudflare API 错误（HTTP {status}）: {detail or payload}")
    return payload


def exact_zone(token: str, requested: str) -> Dict:
    normalized = requested.strip().lower().rstrip(".")
    payload = api_call(
        token,
        "GET",
        "/zones",
        params={"name": normalized, "status": "active", "per_page": 50},
    )
    matches = [
        zone
        for zone in payload.get("result", [])
        if str(zone.get("name", "")).lower().rstrip(".") == normalized
    ]
    if len(matches) != 1:
        raise CloudflareError(f"找不到唯一的 active zone: {requested!r}")
    return matches[0]


def record_name(host: str, zone_name: str) -> str:
    host = host.strip().lower().rstrip(".")
    zone_name = zone_name.strip().lower().rstrip(".")
    if not host or host == "@":
        return zone_name
    if "." in host or any(not (char.isalnum() or char == "-") for char in host):
        raise RuntimeError(f"主机名只允许单个标签: {host!r}")
    return f"{host}.{zone_name}"


def list_records(token: str, zone_id: str, name: str) -> List[Dict]:
    payload = api_call(
        token,
        "GET",
        f"/zones/{zone_id}/dns_records",
        params={"name.exact": name, "per_page": 100, "page": 1},
    )
    return payload.get("result", [])


def ips_from_source(source: str) -> List[str]:
    text = fetch_text(source)
    if source.startswith("file://"):
        # Supporting one address per line also makes a manually supplied file
        # useful when an upstream web page is temporarily unavailable.
        found = unique_ips(text.splitlines())
    else:
        found = extract_public_ipv4(text)
    if not found:
        raise RuntimeError(f"来源没有找到公共 IPv4: {source}")
    return found


def choose_bestcf_ips(sources: Iterable[str]) -> Tuple[List[str], str]:
    errors: List[str] = []
    for source in sources:
        try:
            addresses = ips_from_source(source)
            return addresses, source
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    raise RuntimeError("bestcf 的所有来源都不可用: " + " | ".join(errors))


def managed(record: Dict) -> bool:
    tags = record.get("tags") or []
    return record.get("comment") == MANAGED_COMMENT or MANAGED_COMMENT in tags


def sync_one(
    token: str,
    zone_id: str,
    name: str,
    addresses: List[str],
    dry_run: bool,
) -> None:
    desired = unique_ips(addresses)
    if not desired:
        raise RuntimeError(f"{name} 没有可用的公共 IPv4，拒绝修改 DNS")

    records = list_records(token, zone_id, name)
    cname_records = [record for record in records if record.get("type") == "CNAME"]
    if cname_records:
        raise RuntimeError(f"{name} 已存在 CNAME，不能同时创建 A 记录；请先人工处理")

    a_records = [record for record in records if record.get("type") == "A"]
    unmanaged_records = [record for record in a_records if not managed(record)]
    if unmanaged_records:
        ids = ", ".join(str(record.get("id")) for record in unmanaged_records)
        raise RuntimeError(
            f"{name} 存在未由本项目管理的 A 记录（{ids}），为避免误删已停止；"
            "请确认后手动删除，或给它们加上本项目管理标记"
        )

    current = [record.get("content") for record in a_records]
    print(f"[dns] {name}: current={current}, desired={desired}")
    if dry_run:
        return

    kept: List[str] = []
    for record in a_records:
        content = record.get("content")
        if content in desired and content not in kept:
            kept.append(content)
            if record.get("proxied") is not False or record.get("ttl") != 60:
                api_call(
                    token,
                    "PATCH",
                    f"/zones/{zone_id}/dns_records/{record['id']}",
                    body={"ttl": 60, "proxied": False, "comment": MANAGED_COMMENT},
                )
                print(f"[dns] normalized {record['id']} ({content})")
            continue
        api_call(token, "DELETE", f"/zones/{zone_id}/dns_records/{record['id']}")
        print(f"[dns] deleted {record['id']} ({content})")

    for content in desired:
        if content in kept:
            continue
        payload = {
            "type": "A",
            "name": name,
            "content": content,
            "ttl": 60,
            "proxied": False,
            "comment": MANAGED_COMMENT,
        }
        api_call(token, "POST", f"/zones/{zone_id}/dns_records", body=payload)
        print(f"[dns] added {name} -> {content}")

    # Verify the postcondition instead of assuming that every API call worked.
    verified = list_records(token, zone_id, name)
    verified_contents = sorted(
        record.get("content") for record in verified if record.get("type") == "A" and managed(record)
    )
    if verified_contents != sorted(desired):
        raise CloudflareError(
            f"{name} 校验失败: expected={sorted(desired)}, actual={verified_contents}"
        )


def main() -> int:
    token = os.getenv("CF_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 CF_API_TOKEN；请放在 GitHub Actions Secret，不要写进代码")

    config = load_config()
    zone_name = os.getenv("CF_ZONE_NAME", str(config.get("zone_name", ""))).strip()
    if not zone_name:
        raise RuntimeError("缺少 CF_ZONE_NAME")

    try:
        max_records = int(os.getenv("MAX_DNS_RECORDS", str(config.get("max_records", 20))))
    except ValueError as exc:
        raise RuntimeError("MAX_DNS_RECORDS 必须是整数") from exc
    if max_records < 1:
        raise RuntimeError("MAX_DNS_RECORDS 必须大于 0")

    configured_hosts = config.get("hosts", ["bestcf", "api"])
    if not isinstance(configured_hosts, list) or len(configured_hosts) < 2:
        raise RuntimeError("config.json 的 hosts 至少要有 bestcf 和 api 两个主机名")
    bestcf_host = os.getenv("BESTCF_HOST", str(configured_hosts[0])).strip()
    api_host = os.getenv("API_HOST", str(configured_hosts[1])).strip()

    zone_id = os.getenv("CF_ZONE_ID", str(config.get("zone_id", ""))).strip()
    if zone_id:
        # The ID is pinned in config.json so a token covering multiple zones
        # can never update the wrong one.
        canonical_zone = zone_name.rstrip(".")
    else:
        zone = exact_zone(token, zone_name)
        zone_id = zone["id"]
        canonical_zone = zone["name"]

    configured_sources = config.get("ip_sources", [])
    default_source = configured_sources[0] if configured_sources else DEFAULT_BESTCF_SOURCE
    bestcf_source = os.getenv("BESTCF_SOURCE_URL", str(default_source)).strip()
    # If the external top-list page is down or changes format, the freshly
    # collected local list is a safe fallback and prevents blanking DNS.
    bestcf_sources = [bestcf_source]
    if "file://ip.txt" not in bestcf_sources:
        bestcf_sources.append("file://ip.txt")

    bestcf_ips, selected_source = choose_bestcf_ips(bestcf_sources)
    api_ips = ips_from_source("file://ip.txt")
    bestcf_ips = bestcf_ips[:max_records]
    api_ips = api_ips[:max_records]
    print(f"[dns] zone={canonical_zone} ({zone_id})")
    print(f"[dns] bestcf source={selected_source}")

    dry_run = as_bool(os.getenv("DRY_RUN", "false"))
    sync_one(
        token,
        zone_id,
        record_name(bestcf_host, canonical_zone),
        bestcf_ips,
        dry_run,
    )
    sync_one(
        token,
        zone_id,
        record_name(api_host, canonical_zone),
        api_ips,
        dry_run,
    )
    print("[dns] done")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[dns] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
