#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集 Cloudflare 优选 IPv4，产出两个文件：

- ip.txt    官方网段优选 IP（cf. / cloudflare. 域名用，只保留 CF 官方网段）
- proxy.txt 第三方反代节点 IP（proxy. 域名用）

用法：python collect_ips.py [official|proxy|all]（默认 all）

ip.txt 的筛选法（三网质量打分，而非按来源优先级截取）：
- 三网覆盖（每覆盖一家 +20 分）：数据源带的电信/联通/移动实测标签
- 实测数值：uouin 等源自带的延迟（越低越好，最高 +30）与速度 mb/s（最高 +50）
- 多源共识（每个独立来源 +10）：被越多数据源同时收录，说明各家测试都认可
- 存活验证：写入前对候选做 TCP 443 连通测试（并发），死 IP 不入库
- 最终按总分排序取前 50
（注：runner 在海外无法直接测三网延迟/网速，三网数据借力各数据源自己的实测标注）

其他特点：
- 零第三方依赖（只用标准库），GitHub Actions 上不需要 pip install
- 单个数据源挂了自动跳过；官方源大面积异常时保留旧文件不动（防池子被砍残）
- 反代源里只保留 "IP:443" 格式的行（DNS 优选域名只对 443 端口有意义）
- 排除 WARP 专用网段（162.159.192.0/21，WARP 端点不能当普通优选 IP 用）
"""
import ipaddress
import re
import socket
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ============ 官方 IP 数据源（打分制，顺序只影响同分排序）============
# carriers 字段：该源整体代表的运营商视角（行内还会再解析 电信/联通/移动 标签）
SOURCES = [
    # IPDB 优选官方 IP（每小时更新，原项目数据源 ipdb 的新版 API 格式）
    {"name": "IPDB bestcf", "url": "https://ipdb.api.030101.xyz/?type=bestcf"},
    # SIN 优选域名（站长实测维护，优先稳定性；CNAME 到 singgnetworkcdn）
    {"name": "SIN 优选域名", "dns": "saas.sin.fan"},
    # 每 10 分钟测速的 Top 优选（纯文本，逗号分隔）
    {"name": "ip.164746.xyz Top10", "url": "https://ip.164746.xyz/ipTop10.html"},
    # CloudFlareYes 电信优选（纯文本）
    {"name": "addressesapi 电信", "url": "https://addressesapi.090227.xyz/ct", "carriers": ["电信"]},
    # 090227 三网分类接口（电信/移动/联通）
    {"name": "cf.090227 电信", "url": "https://cf.090227.xyz/ct?ips=6", "carriers": ["电信"]},
    {"name": "cf.090227 移动", "url": "https://cf.090227.xyz/cmcc?ips=8", "carriers": ["移动"]},
    {"name": "cf.090227 联通", "url": "https://cf.090227.xyz/cu", "carriers": ["联通"]},
    # CloudFlareYes 三网（CM API 备用入口）
    {"name": "CloudFlareYes 三网", "url": "https://addressesapi.090227.xyz/CloudFlareYes",
     "carriers": ["电信", "联通", "移动"]},
    # 以下为 bestcf.pages.dev 导航站收录的优选源（多为三网实测，行内带运营商标签）
    {"name": "vvhan 三网", "url": "https://bestcf.pages.dev/vvhan/ipv4.txt"},
    {"name": "NiREvil 三网", "url": "https://bestcf.pages.dev/nirevil/ipv4.txt"},
    {"name": "天诚 三网", "url": "https://raw.githubusercontent.com/gshtwy/CF-DNS-Clone/refs/heads/main/wetest-cloudflare-v4.txt"},
    {"name": "Senflare", "url": "https://raw.githubusercontent.com/Senflare/Senflare-IP/refs/heads/main/IPlist-Pro.txt"},
    {"name": "Einsitang", "url": "https://raw.githubusercontent.com/einsitang/my-fast-cf-ip/refs/heads/master/fastips.txt"},
    # Joname 采集聚合（每 4 小时更新，量大）
    {"name": "Joname 聚合", "url": "https://raw.githubusercontent.com/joname1/BestCFip/refs/heads/main/ipv4.txt"},
    # 麒麟域名检测优选（HTML 表格，带电信标签 + 延迟/速度实测数值）
    {"name": "api.uouin.com", "url": "https://api.uouin.com/cloudflare.html"},
    # 微测网优选 IPv4（HTML 表格；2026-09-07 改版 JS 渲染后已无静态数据，保留占位）
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
MAX_IPS = 50        # ip.txt（官方优选，打分排序取前 50）
MAX_PROXY_IPS = 50  # proxy.txt（反代，取质量优先的前 50 个）
TIMEOUT = 20
RETRIES = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

IP_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
LINE_443_PATTERN = re.compile(r"^\s*((?:\d{1,3}\.){3}\d{1,3}):443\b")
LATENCY_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*ms")
SPEED_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*mb(?:/s)?\b", re.I)


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


def enrich_info(info, text, src_carriers):
    """从源文本提取三网标签与延迟/速度数值，更新到 info。

    按行或 HTML 表格行（<tr>）分块；块内出现 电信/联通/移动 记运营商覆盖，
    出现 "136.85ms" 记延迟、“55.36mb"/"6.92mb/s" 记带宽（多块取最优值）。
    uouin 的 HTML 表格里运营商、IP、延迟、带宽同在一个 <tr> 行内（每个 <td>
    内部有换行），必须按 <tr> 整行切块而不是按换行切。
    """
    chunks = (re.split(r"<tr\b", text, flags=re.I) if re.search(r"<tr\b", text, re.I)
              else text.split("\n"))
    for chunk in chunks:
        found = [raw for raw in IP_PATTERN.findall(chunk) if raw in info]
        if not found:
            continue
        carriers = set(src_carriers)
        for c in ("电信", "联通", "移动"):
            if c in chunk:
                carriers.add(c)
        m_lat = LATENCY_PATTERN.search(chunk)
        m_spd = SPEED_PATTERN.search(chunk)
        lat = float(m_lat.group(1)) if m_lat else None
        spd = float(m_spd.group(1)) if m_spd else None
        for ip in found:
            ent = info[ip]
            ent["carriers"] |= carriers
            if lat is not None and (ent["latency"] is None or lat < ent["latency"]):
                ent["latency"] = lat
            if spd is not None and (ent["speed"] is None or spd > ent["speed"]):
                ent["speed"] = spd


def score_of(ent):
    """综合打分：三网覆盖 + 实测数值 + 多源共识。"""
    s = 10.0 * len(ent["sources"])          # 多源共识：每个独立来源 +10
    s += 20.0 * len(ent["carriers"])        # 三网覆盖：每覆盖一家运营商 +20
    if ent["latency"] is not None:
        s += max(0.0, 300.0 - ent["latency"]) / 10.0   # 延迟越低越高，最高 +30
    if ent["speed"] is not None:
        s += min(ent["speed"], 50.0)        # 速度越快越高，最高 +50
    return s


def alive_check(ips, timeout=5, workers=20):
    """TCP 443 连通性验证（并发），返回存活 IP（保持原顺序）。"""
    def test(ip):
        try:
            with socket.create_connection((ip, 443), timeout=timeout):
                return True
        except Exception:  # noqa: BLE001
            return False
    with ThreadPoolExecutor(max_workers=workers) as ex:
        flags = list(ex.map(test, ips))
    return [ip for ip, ok in zip(ips, flags) if ok]


def run_collection(sources, extract, label):
    """跑一组数据源。返回 (按优先级去重后的 IP 列表, 可用源数量, 逐 IP 信息表)。"""
    merged = []
    seen = set()
    info = {}
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
                info[ip] = {"sources": set(), "carriers": set(src.get("carriers", [])),
                            "latency": None, "speed": None}
            info[ip]["sources"].add(src["name"])
        if ips:
            ok_sources += 1
        enrich_info(info, text, src.get("carriers", []))
        print(f"[OK][{label}] {src['name']}: 抓到 {len(ips)} 个（新增 {len(new)}）")
        time.sleep(1)
    print(f"[{label}] 合计：{ok_sources}/{len(sources)} 个源可用，去重后 {len(merged)} 个 IP")
    return merged, ok_sources, info


def write_file(path: str, ips, cap: int):
    # 排序输出（排序是为了让 git diff 稳定，避免无谓提交）
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
        official, ok, info = run_collection(SOURCES, lambda t, s: extract_ips(t), "官方")
        if ok >= min_sources_ok(len(SOURCES)) and len(official) >= 10:
            # 三网质量打分排序（同分按 IP 排序保持稳定）
            ranked = sorted(official,
                            key=lambda ip: (-score_of(info[ip]), tuple(int(p) for p in ip.split("."))))
            # 存活验证：多验 30 个备选，死的被顶下去
            to_verify = ranked[:MAX_IPS + 30]
            print(f"[官方] 存活验证 {len(to_verify)} 个候选（TCP 443，并发）...")
            alive = alive_check(to_verify)
            print(f"[官方] 存活 {len(alive)}/{len(to_verify)}")
            if len(alive) >= 10:
                final = alive[:MAX_IPS]
            else:
                print("[官方] 警告：存活数过少（疑似网络故障），跳过存活过滤按分数取前 50")
                final = ranked[:MAX_IPS]
            write_file("ip.txt", final, MAX_IPS)
            # 打印 Top10 便于在 Actions 日志里观察打分效果
            print("[官方] 三网质量打分 Top10：")
            for ip in final[:10]:
                ent = info[ip]
                lat = f"{ent['latency']:.0f}ms" if ent["latency"] is not None else "-"
                spd = f"{ent['speed']:.1f}MB/s" if ent["speed"] is not None else "-"
                nets = "/".join(sorted(ent["carriers"])) if ent["carriers"] else "-"
                print(f"  {ip:18s} 分数{score_of(ent):6.1f} | 来源{len(ent['sources'])} | "
                      f"三网{nets} | {lat} {spd}")
        else:
            print(f"错误：官方源大面积异常（{ok}/{len(SOURCES)} 可用，仅 {len(official)} 个 IP），"
                  "保留旧 ip.txt 不动，退出码 1")
            rc = 1

    if scope in ("all", "proxy"):
        proxies, ok, _ = run_collection(
            PROXY_SOURCES, lambda t, s: extract_proxy_ips(t, s.get("port443_only", False)), "反代")
        if ok >= min_sources_ok(len(PROXY_SOURCES)) and len(proxies) >= 10:
            write_file("proxy.txt", proxies, MAX_PROXY_IPS)
        else:
            print(f"[反代] 警告：反代源大面积异常（{ok}/{len(PROXY_SOURCES)} 可用，仅 {len(proxies)} 个 IP），"
                  "保留旧 proxy.txt（不影响官方域名维护）")

    return rc


if __name__ == "__main__":
    sys.exit(main())
