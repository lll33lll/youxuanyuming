#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集 Cloudflare 优选 IPv4，产出两个文件：

- ip.txt    官方网段优选 IP（cf. / cloudflare. 域名用，只保留 CF 官方网段）
- proxy.txt 第三方反代节点 IP（proxy. 域名用）

用法：python collect_ips.py [official|proxy|all]（默认 all）

特点：
- 零第三方依赖（只用标准库），GitHub Actions 上不需要 pip install
- 单个数据源挂了自动跳过；源大面积异常时保留旧文件不动（防池子被砍残）
- 反代源里只保留 "IP:443" 格式的行（DNS 优选域名只对 443 端口有意义）
- 排除 WARP 专用网段（162.159.192.0/21 是 WARP/MASQUE 端点，
  不能当普通优选 IP 用；bestcf.pages.dev 的文件头一行就是它）
"""
import ipaddress
import re
import socket
import sys
import time
import urllib.request

# ============ 官方 IP 数据源（按优先级排序，越靠前质量越高）============
# url 型：抓取网页/接口提取 IP；dns 型：解析优选域名的 A 记录
# （这些优选域名的记录由社区站长实测维护，灰云直指筛好的 CF IP）
SOURCES = [
    # IPDB 优选官方 IP（每小时更新，原项目数据源 ipdb 的新版 API 格式）
    {"name": "IPDB bestcf", "url": "https://ipdb.api.030101.xyz/?type=bestcf"},
    # SIN 优选域名（站长实测维护，优先稳定性；CNAME 到 singgnetworkcdn）
    {"name": "SIN 优选域名", "dns": "saas.sin.fan"},
    # 每 10 分钟测速的 Top 优选（纯文本，逗号分隔）
    {"name": "ip.164746.xyz Top10", "url": "https://ip.164746.xyz/ipTop10.html"},
    # CloudFlareYes 电信优选（纯文本）
    {"name": "addressesapi 电信", "url": "https://addressesapi.090227.xyz/ct"},
    # 090227 三网分类接口（电信/移动/联通）
    {"name": "cf.090227 电信", "url": "https://cf.090227.xyz/ct?ips=6"},
    {"name": "cf.090227 移动", "url": "https://cf.090227.xyz/cmcc?ips=8"},
    {"name": "cf.090227 联通", "url": "https://cf.090227.xyz/cu"},
    # CloudFlareYes 三网（CM API 备用入口）
    {"name": "CloudFlareYes 三网", "url": "https://addressesapi.090227.xyz/CloudFlareYes"},
    # 以下为 bestcf.pages.dev 导航站收录的优选源（多为三网实测）
    {"name": "vvhan 三网", "url": "https://bestcf.pages.dev/vvhan/ipv4.txt"},
    {"name": "NiREvil 三网", "url": "https://bestcf.pages.dev/nirevil/ipv4.txt"},
    {"name": "天诚 三网", "url": "https://raw.githubusercontent.com/gshtwy/CF-DNS-Clone/refs/heads/main/wetest-cloudflare-v4.txt"},
    {"name": "Senflare", "url": "https://raw.githubusercontent.com/Senflare/Senflare-IP/refs/heads/main/IPlist-Pro.txt"},
    {"name": "Einsitang", "url": "https://raw.githubusercontent.com/einsitang/my-fast-cf-ip/refs/heads/master/fastips.txt"},
    # Joname 采集聚合（每 4 小时更新，量大）
    {"name": "Joname 聚合", "url": "https://raw.githubusercontent.com/joname1/BestCFip/refs/heads/main/ipv4.txt"},
    # 麒麟域名检测优选（HTML，用正则提取，量大）
    {"name": "api.uouin.com", "url": "https://api.uouin.com/cloudflare.html"},
    # 微测网优选 IPv4（HTML 表格）
    {"name": "wetest.vip", "url": "https://www.wetest.vip/page/cloudflare/address_v4.html"},
]

# ============ 反代 IP 数据源（第三方架设的中转节点，流量会经过第三方服务器）============
# port443_only=True 的源只保留 "IP:443" 格式的行（非 443 端口对 DNS 优选域名无意义）
PROXY_SOURCES = [
    # IPDB 优选反代 IP（每小时实测）
    {"name": "IPDB bestproxy", "url": "https://ipdb.api.030101.xyz/?type=bestproxy", "port443_only": False},
    # MJZ 三网实测（每 45 分钟更新）
    {"name": "MJZ 联通", "url": "https://cf.junzhen.qzz.io/best_ips.txt", "port443_only": True},
    {"name": "MJZ 电信", "url": "https://cf.junzhen.qzz.io/best_ips_bj.txt", "port443_only": True},
    # 陕西移动实测高速优选（带延迟/带宽标注）
    {"name": "gaoji.uk 移动", "url": "https://ips.gaoji.uk/best_ips.txt", "port443_only": True},
    # LZ 联通实测（每 2 小时更新）
    {"name": "LZ 联通", "url": "https://raw.githubusercontent.com/love-ztm/cfip/refs/heads/main/ubest_ips.txt", "port443_only": True},
    # Xiaobei09 二筛稳定版（带速度标注）
    {"name": "Xiaobei09 稳定", "url": "https://raw.githubusercontent.com/Xiaobei09/ProxyIP/main/data/valid/all_46_ltd_stable.txt", "port443_only": True},
    # 多项目聚合 bestips（每 3 小时更新，量大兜底）
    {"name": "LancelotRar 聚合", "url": "https://raw.githubusercontent.com/LancelotRar/best-cf-ips/main/best-cf-ipv4.txt", "port443_only": True},
    # 以下为兜底大池子
    {"name": "S5公益", "url": "https://bestcf.pages.dev/s5gy/all.txt", "port443_only": True},
    {"name": "Laziji", "url": "https://bestcf.pages.dev/lzj/all.txt", "port443_only": True},
]

# Cloudflare 官方 IPv4 网段（官方清单：https://www.cloudflare.com/ips-v4）
# 若 Cloudflare 将来调整网段，需要同步更新这里
CF_V4_RANGES = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
]
_CF_NETS = tuple(ipaddress.ip_network(n) for n in CF_V4_RANGES)

# WARP 专用网段（WARP/MASQUE 端点只服务 WireGuard/MASQUE，不服务普通 SNI 代理，
# 不能混进优选池；bestcf.pages.dev 每个文件的头一行 162.159.198.1 就是它）
WARP_V4_RANGES = ["162.159.192.0/21"]
_WARP_NETS = tuple(ipaddress.ip_network(n) for n in WARP_V4_RANGES)

# 输出上限
MAX_IPS = 50        # ip.txt（官方优选）
MAX_PROXY_IPS = 50  # proxy.txt（反代，取质量优先的前 50 个）
TIMEOUT = 20
RETRIES = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

IP_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
LINE_443_PATTERN = re.compile(r"^\s*((?:\d{1,3}\.){3}\d{1,3}):443\b")


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


def resolve_dns_source(hostname: str) -> str:
    """解析优选域名的 A 记录，返回换行分隔的 IP 文本（供统一提取）。"""
    ips = sorted({ai[4][0] for ai in socket.getaddrinfo(hostname, None, socket.AF_INET)})
    return "\n".join(ips)


def _valid_public_ipv4(raw: str):
    """是公网 IPv4 且不属于 WARP 专用段，返回 ip 对象；否则 None。"""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if ip.version != 4 or not ip.is_global:
        return None
    if any(ip in net for net in _WARP_NETS):
        return None
    return ip


def extract_ips(text: str):
    """官方优选：只收 Cloudflare 官方网段的公网 IPv4（排除 WARP 段）。"""
    found = []
    for raw in IP_PATTERN.findall(text):
        ip = _valid_public_ipv4(raw)
        if ip and any(ip in net for net in _CF_NETS):
            found.append(raw)
    return found


def extract_proxy_ips(text: str, port443_only: bool = False):
    """反代 IP：公网 IPv4 即可（不限 CF 网段）；可只保留 :443 端口的行。"""
    found = []
    if port443_only:
        for line in text.splitlines():
            m = LINE_443_PATTERN.match(line)
            if m and _valid_public_ipv4(m.group(1)):
                found.append(m.group(1))
        return found
    for raw in IP_PATTERN.findall(text):
        if _valid_public_ipv4(raw):
            found.append(raw)
    return found


def run_collection(sources, extract, label):
    """跑一组数据源，返回 (按优先级去重后的 IP 列表, 可用源数量)。"""
    merged = []
    seen = set()
    ok_sources = 0
    for src in sources:
        try:
            if "dns" in src:
                text = resolve_dns_source(src["dns"])
            else:
                text = fetch_text(src["url"])
        except Exception as e:  # noqa: BLE001
            print(f"[跳过][{label}] {src['name']}: {type(e).__name__}: {e}")
            continue
        ips = extract(text, src)
        new = []
        for ip in ips:
            if ip not in seen:  # 同一来源内部和跨来源都去重
                seen.add(ip)
                new.append(ip)
                merged.append(ip)
        if ips:
            ok_sources += 1
        print(f"[OK][{label}] {src['name']}: 抓到 {len(ips)} 个（新增 {len(new)}）")
        time.sleep(1)
    print(f"[{label}] 合计：{ok_sources}/{len(sources)} 个源可用，去重后 {len(merged)} 个 IP")
    return merged, ok_sources


def write_file(path: str, ips, cap: int):
    # 按优先级截断配额，再排序输出（排序是为了让 git diff 稳定，避免无谓提交）
    final = sorted(ips[:cap], key=lambda s: tuple(int(p) for p in s.split(".")))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(final) + "\n")
    print(f"已写入 {path}（{len(final)} 个 IP，上限 {cap}）")


def min_sources_ok(n_sources: int) -> int:
    """一组源至少要有多少个可用才认为采集正常（防网络/源大面积异常时砍残池子）。"""
    return max(2, n_sources // 4)


def main() -> int:
    # 范围参数：official=只采官方(ip.txt)，proxy=只采反代(proxy.txt)，all=都采
    scope = "all"
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args and args[0] in ("official", "proxy", "all"):
        scope = args[0]
    rc = 0

    if scope in ("all", "official"):
        official, ok = run_collection(SOURCES, lambda t, s: extract_ips(t), "官方")
        if ok >= min_sources_ok(len(SOURCES)) and len(official) >= 10:
            write_file("ip.txt", official, MAX_IPS)
        else:
            print(f"错误：官方源大面积异常（{ok}/{len(SOURCES)} 可用，仅 {len(official)} 个 IP），"
                  "保留旧 ip.txt 不动，退出码 1")
            rc = 1

    if scope in ("all", "proxy"):
        proxies, ok = run_collection(
            PROXY_SOURCES, lambda t, s: extract_proxy_ips(t, s.get("port443_only", False)), "反代")
        if ok >= min_sources_ok(len(PROXY_SOURCES)) and len(proxies) >= 10:
            write_file("proxy.txt", proxies, MAX_PROXY_IPS)
        else:
            print(f"[反代] 警告：反代源大面积异常（{ok}/{len(PROXY_SOURCES)} 可用，仅 {len(proxies)} 个 IP），"
                  "保留旧 proxy.txt（不影响官方域名维护）")

    return rc


if __name__ == "__main__":
    sys.exit(main())
