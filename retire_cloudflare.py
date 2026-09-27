#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性脚本：清理已停用子域名 cloudflare.223226.xyz 的全部 managed 记录。

背景：2026-09-27 起该域名下线（数据源已从 collect_ips.py / bestdomain.py 移除），
本脚本负责把 DNS 里遗留的 managed 记录删干净；只删带 managed 注释的记录，
手工记录保留并打印提醒。清理完成后，本脚本与 workflow 中的对应步骤一并移除。

用法：CF_API_TOKEN=xxx CF_ZONE_NAME=223226.xyz python retire_cloudflare.py
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.cloudflare.com/client/v4"
MANAGED_COMMENT = "managed-by:youxuanyuming"
RETIRED_SUBDOMAIN = "cloudflare"


def cf(method: str, path: str):
    url = API_BASE + path
    headers = {"Authorization": "Bearer " + os.environ["CF_API_TOKEN"],
               "User-Agent": "youxuanyuming/2.0 retire"}
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {detail}") from e
    if not payload.get("success"):
        raise RuntimeError(f"API 失败 {method} {path}: {payload.get('errors')}")
    return payload.get("result")


def main() -> int:
    zone_name = os.environ.get("CF_ZONE_NAME", "223226.xyz").strip().rstrip(".")
    fqdn = f"{RETIRED_SUBDOMAIN}.{zone_name}"

    q = urllib.parse.urlencode({"name": zone_name, "status": "active", "per_page": 50})
    zones = cf("GET", f"/zones?{q}")
    if not zones:
        print(f"错误：找不到 zone {zone_name}")
        return 1
    zone_id = zones[0]["id"]
    print(f"[zone] {zone_name} ({zone_id})")

    deleted = kept = 0
    for rtype in ("A", "AAAA", "CNAME"):
        q = urllib.parse.urlencode({"type": rtype, "name": fqdn, "per_page": 100})
        records = cf("GET", f"/zones/{zone_id}/dns_records?{q}")
        for r in records:
            if r.get("comment") == MANAGED_COMMENT:
                cf("DELETE", f"/zones/{zone_id}/dns_records/{r['id']}")
                print(f"  已删除: {rtype} {r['content']}")
                deleted += 1
            else:
                print(f"  保留（非托管记录，不碰）: {rtype} {r['content']}")
                kept += 1
    print(f"完成：删除 {deleted} 条 managed 记录，保留 {kept} 条非托管记录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
