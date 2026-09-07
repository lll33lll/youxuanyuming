#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地单测：不碰真实 API，用假 cf() 验证 bestdomain.py 的核心逻辑。

运行：python3 test_local.py（需要仓库根目录有 ip.txt）
"""
import sys
import bestdomain

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


def run_case(existing_records, sources=None, dry_run=False, existing6_records=None):
    """existing_records: [{'id','content','comment'}]（A）；existing6_records 同理（AAAA）"""
    calls = []
    zone_id = "z0"

    def fake_cf(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET" and path.startswith(f"/zones/{zone_id}/dns_records"):
            if "type=AAAA" in path:
                return existing6_records or []
            return existing_records
        if method == "GET" and path.startswith("/zones?"):
            return [{"id": zone_id, "name": "223226.xyz"}]
        return {"id": "new"}

    bestdomain.cf = fake_cf
    srcs = sources if sources is not None else ["ip.txt"]
    bestdomain.update_subdomain(zone_id, "223226.xyz", "test", srcs, dry_run)
    return calls


print("== 场景1：全新域名，应只新增不删除 ==")
calls = run_case(existing_records=[])
posts = [c for c in calls if c[0] == "POST"]
dels = [c for c in calls if c[0] == "DELETE"]
check("有新增", len(posts) > 0, f"posts={len(posts)}")
check("无删除", len(dels) == 0)
check("带管理注释", all(c[2].get("comment") == bestdomain.MANAGED_COMMENT for c in posts))
check("灰云+ttl1", all(c[2].get("proxied") is False and c[2].get("ttl") == 1 for c in posts))
check("不超上限", len(posts) <= bestdomain.MAX_RECORDS)

print("== 场景2：已有托管记录，部分过期，应先加后删 ==")
old_ips = [line.strip() for line in open("ip.txt")][:5]
existing = [{"id": f"r{i}", "content": ip, "comment": bestdomain.MANAGED_COMMENT} for i, ip in enumerate(old_ips)]
existing.append({"id": "stale", "content": "1.2.3.4", "comment": bestdomain.MANAGED_COMMENT})
calls = run_case(existing_records=existing)
deleted = [c for c in calls if c[0] == "DELETE"]
check("删除了过期记录 1.2.3.4", any(c[1].endswith("/stale") for c in deleted))
check("先加后删", [c[0] for c in calls if c[0] in ("POST", "DELETE")].count("DELETE") == 1)
check("已存在的IP不重复加", not any(c[2]["content"] in old_ips for c in calls if c[0] == "POST"))

print("== 场景3：用户手工加的记录（无注释）绝不能被删 ==")
existing = [{"id": "manual", "content": "9.9.9.9", "comment": None}]
calls = run_case(existing_records=existing)
check("不删手工记录", not any(c[1].endswith("/manual") for c in calls if c[0] == "DELETE"))
check("同IP已存在不再加", not any(c[0] == "POST" and c[2]["content"] == "9.9.9.9" for c in calls))

print("== 场景4：来源几乎全挂（<2 个IP），应跳过不动 ==")
calls = run_case(existing_records=[], sources=["nosuchfile.txt"])
check("无任何写操作", not any(c[0] in ("POST", "DELETE") for c in calls))

print("== 场景5：dry-run 只打印不写 ==")
existing = [{"id": "stale", "content": "1.2.3.4", "comment": bestdomain.MANAGED_COMMENT}]
calls = run_case(existing_records=existing, dry_run=True)
check("无任何写操作", not any(c[0] in ("POST", "DELETE") for c in calls))

print("== 场景6：反代域名（cf_only=False）保留非官方网段 IP ==")
import tempfile, os
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
    f.write("1.2.3.4\n8.8.8.8\n104.17.53.242\n10.0.0.1\n")  # 1.2.3.4 非CF段，8.8.8.8 非CF段，10.x 内网
    proxyfile = f.name
try:
    official_only = bestdomain.extract_ips(open(proxyfile).read(), cf_only=True)
    proxy_mode = bestdomain.extract_ips(open(proxyfile).read(), cf_only=False)
    check("官方模式只剩CF段IP", official_only == ["104.17.53.242"], str(official_only))
    check("反代模式保留公网非CF IP", proxy_mode == ["1.2.3.4", "8.8.8.8", "104.17.53.242"], str(proxy_mode))
    check("WARP段IP被过滤", bestdomain.extract_ips("162.159.198.1\n104.17.53.242\n") == ["104.17.53.242"])
finally:
    os.unlink(proxyfile)

print("== 场景7：反代子域名 cf_only=False 全流程 ==")
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
    f.write("45.77.254.160\n8.218.36.133\n")  # 两个非CF的公网IP
    proxyfile = f.name
try:
    calls = []
    def fake_cf(method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("/zones?"):
            return [{"id": "z0", "name": "223226.xyz"}]
        return []
    bestdomain.cf = fake_cf
    bestdomain.update_subdomain("z0", "223226.xyz", "proxy", [proxyfile], False, cf_only=False)
    posts = [c for c in calls if c[0] == "POST"]
    check("反代域名添加了非CF IP", len(posts) == 2 and {c[2]["content"] for c in posts} == {"45.77.254.160", "8.218.36.133"})
    calls.clear()
    bestdomain.update_subdomain("z0", "223226.xyz", "proxy", [proxyfile], False, cf_only=True)
    check("同样内容走官方模式被全部过滤（无写入）", not any(c[0] == "POST" for c in calls))
finally:
    os.unlink(proxyfile)

print("== 场景8：范围过滤（official/proxy/all） ==")
check("official→只含官方域名", set(bestdomain.select_subdomains("official")) == {"cf", "cloudflare"})
check("proxy→只含反代域名", set(bestdomain.select_subdomains("proxy")) == {"proxy"})
check("all→三个域名", set(bestdomain.select_subdomains("all")) == {"cf", "cloudflare", "proxy"})
try:
    bestdomain.select_subdomains("bogus")
    check("非法范围报错", False)
except Exception:
    check("非法范围报错", True)

print("== 场景9：static 源的 v4+v6（A + AAAA 记录） ==")
stext = bestdomain.fetch_text("static:104.17.53.242,2606:4700:ff00:262e:715d:8689:5b7a:97b5")
check("static v4 提取", bestdomain.extract_ips(stext) == ["104.17.53.242"], str(bestdomain.extract_ips(stext)))
check("static v6 提取", bestdomain.extract_ips6(stext) == ["2606:4700:ff00:262e:715d:8689:5b7a:97b5"], str(bestdomain.extract_ips6(stext)))
check("v6 非 CF 官方段被过滤", bestdomain.extract_ips6("::1 2001:db8::1 2606:4700::1") == ["2606:4700::1"])
calls = []
def fake_cf9(method, path, body=None):
    calls.append((method, path, body))
    if path.startswith("/zones?"):
        return [{"id": "z0", "name": "223226.xyz"}]
    return []
bestdomain.cf = fake_cf9
bestdomain.update_subdomain("z0", "223226.xyz", "test",
                            ["static:104.17.53.242,2606:4700:ff00:262e:715d:8689:5b7a:97b5"], False)
aposts = [c for c in calls if c[0] == "POST" and c[2]["type"] == "A"]
aaaaposts = [c for c in calls if c[0] == "POST" and c[2]["type"] == "AAAA"]
check("A 记录创建", len(aposts) == 1 and aposts[0][2]["content"] == "104.17.53.242")
check("AAAA 记录创建", len(aaaaposts) == 1 and aaaaposts[0][2]["content"] == "2606:4700:ff00:262e:715d:8689:5b7a:97b5")

print(f"\n结果：{PASS} 通过，{FAIL} 失败")
sys.exit(1 if FAIL else 0)
