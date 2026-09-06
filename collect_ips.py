#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集 Cloudflare 优选 IPv4，写入 ip.txt（去重、校验、限量）。

特点：
- 零第三方依赖（只用标准库），GitHub Actions 上不需要 pip install
- 单个数据源挂了自动跳过，全部挂掉则报错退出且不动旧 ip.txt
- 只保留公网 IPv4，自动剔除内网/保留地址，避免把网页里的版本号等误当代码抓进来
"""
import ipaddress
import os
import re
import sys
import time
import urllib.request

# 数据源（按优先级排序，越靠前质量越高；抓不满配额时优先用靠前的）
SOURCES = [
    # 每 10 分钟测速的 Top 优选（纯文本，逗号分隔）
    {"name": "ip.164746.xyz Top10", "url": "https://ip.164746.xyz/ipTop10.html"},
    # CloudFlareYes 电信优选（纯文本）
    {"name": "addressesapi.090227.xyz/ct", "url": "https://addressesapi.090227.xyz/ct"},
    # 麒麟域名检测优选（HTML，用正则提取）
    {"name": "api.uouin.com", "url": "https://api.uouin.com/cloudflare.html"},
    # 微测网优选 IPv4（HTML 表格）
    {"name": "wetest.vip", "url": "https://www.wetest.vip/page/cloudflare/address_v4.html"},
]

# ip.txt 最多保留多少个 IP（防止数据源异常返回海量地址，撑爆 DNS 记录）
MAX_IPS = 50
OUTPUT_FILE = "ip.txt"
TIMEOUT = 20
RETRIES = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

IP_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def fetch_text(url: str) -> str:
    last_err = None
    for _ in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    raise last_err


def extract_ips(text: str):
    found = []
    for raw in IP_PATTERN.findall(text):
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        # 只收公网 IPv4，过滤 127.0.0.1 / 10.x / 192.168.x / 169.254.x 等
        if ip.version == 4 and ip.is_global:
            found.append(raw)
    return found


def main() -> int:
    merged = []  # 按数据源优先级顺序保留
    seen = set()
    ok_sources = 0

    for src in SOURCES:
        try:
            text = fetch_text(src["url"])
        except Exception as e:  # noqa: BLE001
            print(f"[跳过] {src['name']}: {type(e).__name__}: {e}")
            continue
        ips = extract_ips(text)
        new = []
        for ip in ips:
            if ip not in seen:  # 同一来源内部和跨来源都去重
                seen.add(ip)
                new.append(ip)
                merged.append(ip)
        if ips:
            ok_sources += 1
        print(f"[OK] {src['name']}: 抓到 {len(ips)} 个（新增 {len(new)}）")
        time.sleep(1)

    print(f"合计：{ok_sources}/{len(SOURCES)} 个源可用，去重后 {len(merged)} 个 IP")

    if not merged:
        print("错误：一个 IP 都没抓到，保留旧 ip.txt 不动，退出码 1")
        return 1

    # 按优先级截断配额，再排序输出（排序是为了让 git diff 稳定，避免无谓提交）
    final = sorted(merged[:MAX_IPS], key=lambda s: tuple(int(p) for p in s.split(".")))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(final) + "\n")

    print(f"已写入 {OUTPUT_FILE}（{len(final)} 个 IP，上限 {MAX_IPS}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
