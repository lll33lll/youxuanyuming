#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 IP 列表更新 Cloudflare DNS 的 A 记录（优选域名）。

用法（在仓库根目录）：
    CF_API_TOKEN=xxx CF_ZONE_NAME=223226.xyz python bestdomain.py [--dry-run] [--only all|official|proxy]

安全设计：
- 只管理自己创建的记录（按 DNS 记录 comment 识别），不会动你手工加的记录
- 新列表少于 2 个 IP 时直接跳过该域名（数据源抽风也不会把你现有记录清空）
- 每个域名的记录数量有上限，超出截断
- 支持 --dry-run 只打印计划不实际改动
- 支持 --only 指定范围：all=全部 / official=官方域名(cf/cloudflare) / proxy=反代域名

来源格式：
- http(s):// 开头 → 抓取网页/接口提取 IP
- dns: 开头 → 解析优选域名的 A 记录（站长实测维护的记录）
- 其他 → 按本地文件读取（如 ip.txt / proxy.txt）

环境变量：
- CF_API_TOKEN  必填：Cloudflare API 令牌（Zone.DNS Edit 权限）
- CF_ZONE_NAME  选填：目标域名（默认 223226.xyz），按名字精确匹配，不会再用错 zone
- DRY_RUN=1     等同 --dry-run
"""
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.cloudflare.com/client/v4"
MANAGED_COMMENT = "managed-by:youxuanyuming"
MAX_RECORDS = 50  # 每个域名的 A 记录上限
TIMEOUT = 30

# 域名 -> 配置。sources 里 http(s):// 开头则抓取，dns: 开头则解析 A 记录，否则按本地文件读取；
# cf_only=True 表示只接受 Cloudflare 官方网段的 IP（反代域名设为 False）
SUBDOMAIN_IP_SOURCES = {
    # 精选官方：【临时测试模式】只保留 SIN 优选域名，以下三个源暂时移除（测试完加回）
    #   https://ip.164746.xyz/ipTop10.html
    #   https://addressesapi.090227.xyz/ct
    #   https://www.wetest.vip/page/cloudflare/address_v4.html
    "cf": {
        "sources": [
            "dns:saas.sin.fan",
        ],
        "cf_only": True,
    },
    # 全量官方：本仓库采集的合并列表（16 个数据源）
    "cloudflare": {"sources": ["ip.txt"], "cf_only": True},
    # 反代节点：第三方架设的中转 IP（流量会经过第三方服务器，自担风险）
    "proxy": {"sources": ["proxy.txt"], "cf_only": False},
}

IP_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
HEADERS = {"User-Agent": "youxuanyuming/2.0"}

# Cloudflare 官方 IPv4 网段（官方清单：https://www.cloudflare.com/ips-v4）
# 只保留官方网段的 IP，第三方反代 IP 一律不用（会把流量导到陌生服务器）
CF_V4_RANGES = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
]
_CF_NETS = tuple(ipaddress.ip_network(n) for n in CF_V4_RANGES)

# WARP 专用网段（WARP/MASQUE 端点不服务普通 SNI 代理，不能当优选 IP 用）
WARP_V4_RANGES = ["162.159.192.0/21"]
_WARP_NETS = tuple(ipaddress.ip_network(n) for n in WARP_V4_RANGES)


class CFError(Exception):
    pass


def cf(method: str, path: str, body=None):
    """调用 Cloudflare API，失败抛 CFError。"""
    url = API_BASE + path
    headers = dict(HEADERS)
    headers["Authorization"] = "Bearer " + os.environ["CF_API_TOKEN"]
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise CFError(f"HTTP {e.code} {method} {path}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise CFError(f"{type(e).__name__} {method} {path}: {e}") from e
    if not payload.get("success"):
        raise CFError(f"API 失败 {method} {path}: {json.dumps(payload.get('errors', []))[:500]}")
    return payload.get("result")


def fetch_text(source: str) -> str:
    if source.startswith("dns:"):
        # dns 型来源：解析优选域名的 A 记录（站长实测维护的记录）
        ips = sorted({ai[4][0] for ai in socket.getaddrinfo(source[4:], None, socket.AF_INET)})
        return "\n".join(ips)
    if source.startswith("http://") or source.startswith("https://"):
        req = urllib.request.Request(source, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")
    with open(source, "r", encoding="utf-8") as f:
        return f.read()


def extract_ips(text: str, cf_only: bool = True):
    out = []
    for raw in IP_PATTERN.findall(text):
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if ip.version == 4 and ip.is_global and raw not in out:
            if any(ip in net for net in _WARP_NETS):
                continue  # WARP 专用端点，不能当优选 IP
            if cf_only and not any(ip in net for net in _CF_NETS):
                continue  # 反代/非官方网段的 IP，官方域名不用
            out.append(raw)
    return out


def get_zone_id(zone_name: str) -> str:
    q = urllib.parse.urlencode({"name": zone_name, "status": "active", "per_page": 50})
    zones = cf("GET", f"/zones?{q}")
    if len(zones) == 1:
        print(f"[zone] {zones[0]['name']} ({zones[0]['id']})")
        return zones[0]["id"]
    if not zones:
        raise CFError(f"账号里找不到名为 {zone_name} 的活跃 zone，请检查 CF_ZONE_NAME")
    names = ", ".join(z["name"] for z in zones)
    raise CFError(f"zone 名字匹配异常（{names}），请检查 CF_ZONE_NAME")


def update_subdomain(zone_id: str, zone_name: str, subdomain: str, sources,
                     dry_run: bool, cf_only: bool = True):
    fqdn = zone_name if subdomain == "@" else f"{subdomain}.{zone_name}"

    # 1. 取新 IP 列表（多来源合并去重）
    desired = []
    for src in sources:
        try:
            text = fetch_text(src)
        except Exception as e:  # noqa: BLE001
            print(f"[警告] {fqdn}: 来源 {src} 获取失败（{type(e).__name__}: {e}），跳过该来源")
            continue
        for ip in extract_ips(text, cf_only=cf_only):
            if ip not in desired:
                desired.append(ip)
    desired = sorted(desired[:MAX_RECORDS], key=lambda s: tuple(int(p) for p in s.split(".")))

    if len(desired) < 2:
        print(f"[跳过] {fqdn}: 有效 IP 只有 {len(desired)} 个(<2)，为防误清空保留现有记录")
        return

    # 2. 查现有 A 记录（区分“本脚本管理的”和“用户手工加的”）
    q = urllib.parse.urlencode({"type": "A", "name": fqdn, "per_page": 100})
    existing = cf("GET", f"/zones/{zone_id}/dns_records?{q}")
    managed = [r for r in existing if r.get("comment") == MANAGED_COMMENT]
    existing_contents = {r["content"] for r in existing}

    to_delete = [r for r in managed if r["content"] not in desired]
    to_add = [ip for ip in desired if ip not in existing_contents]

    print(f"[{fqdn}] 目标 {len(desired)} 个 IP | 现有 {len(existing)} 条"
          f"（托管 {len(managed)}）| 需删 {len(to_delete)} | 需加 {len(to_add)}")
    if not to_delete and not to_add:
        print(f"[{fqdn}] 无变化")
        return

    if dry_run:
        for r in to_delete:
            print(f"  将删除: {r['content']}")
        for ip in to_add:
            print(f"  将新增: {ip}")
        print(f"[{fqdn}] dry-run 模式，未实际改动")
        return

    # 3. 先加后删（保证任意时刻域名都有记录可解析）
    for ip in to_add:
        cf("POST", f"/zones/{zone_id}/dns_records", {
            "type": "A", "name": fqdn, "content": ip,
            "ttl": 1, "proxied": False, "comment": MANAGED_COMMENT,
        })
        print(f"  已新增: {ip}")
    for r in to_delete:
        cf("DELETE", f"/zones/{zone_id}/dns_records/{r['id']}")
        print(f"  已删除: {r['content']}")
        time.sleep(0.2)


def select_subdomains(scope: str):
    """按范围挑选要更新的子域名：all=全部 / official=官方(cf_only) / proxy=反代。"""
    if scope == "all":
        return dict(SUBDOMAIN_IP_SOURCES)
    if scope == "official":
        return {k: v for k, v in SUBDOMAIN_IP_SOURCES.items() if v.get("cf_only", True)}
    if scope == "proxy":
        return {k: v for k, v in SUBDOMAIN_IP_SOURCES.items() if not v.get("cf_only", True)}
    raise CFError(f"未知范围: {scope}（可选 all/official/proxy）")


def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args or os.environ.get("DRY_RUN") == "1"

    # 范围参数：--only all/official/proxy（--only=xxx 也支持），默认 all
    scope = "all"
    if "--only" in args:
        i = args.index("--only")
        if i + 1 < len(args):
            scope = args[i + 1]
    for a in args:
        if a.startswith("--only="):
            scope = a.split("=", 1)[1]

    token = os.environ.get("CF_API_TOKEN", "").strip()
    if not token:
        print("错误：未设置 CF_API_TOKEN 环境变量（需要 Zone.DNS Edit 权限的令牌）")
        return 1
    zone_name = os.environ.get("CF_ZONE_NAME", "223226.xyz").strip().rstrip(".")

    try:
        targets = select_subdomains(scope)
        print(f"本次范围: {scope} -> {', '.join(targets)}")
        zone_id = get_zone_id(zone_name)
        for subdomain, cfg in targets.items():
            update_subdomain(zone_id, zone_name, subdomain, cfg["sources"], dry_run,
                             cf_only=cfg.get("cf_only", True))
            time.sleep(0.5)
    except CFError as e:
        print(f"错误：{e}")
        return 1
    print("完成" + ("（dry-run，未改动）" if dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
