# youxuanyuming — Cloudflare 优选域名自动维护

Fork 自 [jc-lw/youxuanyuming](https://github.com/jc-lw/youxuanyuming)（上游：[tianshipapa](https://github.com/tianshipapa) / [ymyuuu/BestDomain](https://github.com/ymyuuu/BestDomain)），本 fork 修复了上游的一系列问题（见下方「相对上游的改动」），并改造成零依赖、可长期稳定运行的版本。

## 它做什么

GitHub Actions **每 3 小时**自动：

1. 从 **8 个**公开的 Cloudflare 优选 IP 数据源抓取 IPv4（坏源自动跳过）
2. 去重、校验、限量后写入 `ip.txt` 并提交到仓库；**只保留 Cloudflare 官方网段的 IP**（第三方反代 IP 会把流量导到陌生人的服务器上，一律过滤）
3. 调 Cloudflare API，把下面两个域名的 A 记录同步成最新优选 IP（**灰云 / DNS-only**）：

| 域名 | IP 来源 | 说明 |
| --- | --- | --- |
| `cf.223226.xyz` | [IPDB bestcf](https://ipdb.api.030101.xyz/?type=bestcf) + [ip.164746.xyz](https://ip.164746.xyz/ipTop10.html) Top10 + [CloudFlareYes 电信](https://addressesapi.090227.xyz/ct) + [微测网](https://www.wetest.vip/page/cloudflare/address_v4.html) | 精选，数量少质量高 |
| `cloudflare.223226.xyz` | 本仓库 `ip.txt`（全部 8 个数据源合并） | 全量，上限 50 个 |

`ip.txt` 的全部数据源（按优先级）：IPDB bestcf、ip.164746.xyz Top10、addressesapi 电信、cf.090227 三网接口（电信/移动/联通）、api.uouin.com、wetest.vip。

> 为什么是灰云：优选域名的用法是客户端里「地址」填它（拿到一批好 IP），「SNI/Host」填你真正走 CF 代理的域名（如 Worker/Pages 域名）。如果开橙云，解析出来就又变回 CF 随机分配的 IP，失去优选意义。

## 一次性配置（3 步）

### 1. 启用 Actions

Fork 的 Actions 默认禁用。打开仓库 **Actions** 标签页，点绿色按钮 **"I understand my workflows, go ahead and enable them"**。

### 2. 创建 Cloudflare API 令牌

1. 打开 https://dash.cloudflare.com/profile/api-tokens → **Create Token**
2. 选 **Edit zone DNS** 模板
3. Zone Resources 里**只勾 `223226.xyz`** 这一个域名（不要给所有域名）
4. 创建后复制令牌（只显示一次）

### 3. 添加仓库 Secret

仓库 **Settings → Secrets and variables → Actions → New repository secret**：

- Name: `CF_API_TOKEN`
- Secret: 上一步复制的令牌

没配令牌之前，采集和 `ip.txt` 提交照常运行，只是 DNS 不更新；配置好后的下一个周期自动生效。

## 验证

```bash
nslookup cf.223226.xyz
nslookup cloudflare.223226.xyz
```

能解析出一批 104.x / 162.159.x / 172.64.x 的地址就是正常的。

## 怎么用

在代理客户端（v2rayN / Clash Meta / Shadowrocket / sing-box 等）里：

- **address / server** 填 `cf.223226.xyz` 或 `cloudflare.223226.xyz`
- **SNI / Host / peer** 填你真正走 CF 的域名（例如你自己的 Worker、Pages 或其它橙云域名）

## 手动运行 / 试运行

Actions → **采集优选IP并更新DNS** → **Run workflow**：

- 直接运行 = 立即采集并更新 DNS
- 勾选 **dry_run** = 只打印将要增删的记录，不实际改动

## 常见问题

- **多久更新一次？** 每 3 小时（UTC `17 */3 * * *`）。想改频率就编辑 `.github/workflows/update.yml` 里的 cron。
- **会动我手工加的 DNS 记录吗？** 不会。脚本只管理自己创建的记录（带 `managed-by:youxuanyuming` 注释），你手工加的同名 A 记录会被保留。
- **数据源挂了怎么办？** 单个源挂了自动跳过；两个域名各自的有效 IP 少于 2 个时会跳过更新、保留现有记录，不会清空。
- **为什么有的来源抓到的 IP 会变少？** 脚本会过滤掉不属于 Cloudflare 官方网段的 IP（部分来源混有第三方反代节点，会把流量导到陌生服务器，不安全），只保留官方网段。
- **想换域名/加子域名？** 改 `bestdomain.py` 顶部的 `SUBDOMAIN_IP_SOURCES`，以及 workflow 里的 `CF_ZONE_NAME`。
- **ip.txt 是什么？** 全量采集结果（上限 50 个），也作为 `cloudflare` 域名的数据源，可以通过
  `https://raw.githubusercontent.com/lll33lll/youxuanyuming/main/ip.txt` 直接引用。

## 相对上游的改动

- 上游 `bestcf` 的数据源 `ipdb.030101.xyz/api/bestcf.txt` 已失效（返回 HTML 页面），`stock.hostmonit.com/CloudFlareYes` 改版抓不到 IP —— 已换成当前可用数据源
- 上游取 `zones[0]` 会更新到账号里第一个域名 —— 改为按 `CF_ZONE_NAME` 精确匹配
- 上游先删后建且无保护，数据源返回空时会清空 DNS —— 改为先建后删、IP 数量 <2 跳过、只管理带注释的自己的记录
- 增加每域名记录数上限（50）与 `ip.txt` 上限（50），防止异常源撑爆 zone
- 去掉 `requests` / `beautifulsoup4` 依赖，只用标准库，CI 更快更稳
- 两个 workflow（采集/DNS）合并为一个，消除时序依赖；频率从每 30 分钟放宽到每 3 小时
- 采集脚本对无效/保留 IP 做了 `ipaddress` 校验，避免把网页里的版本号等杂质抓进来
- 数据源扩充到 8 个（新增 IPDB 新版 API、090227 三网接口），并增加 Cloudflare 官方网段硬过滤，杜绝第三方反代 IP 混入

## 开源协议

沿用上游：欢迎使用、修改和传播。免责声明：脚本尽力确保安全，但任何使用问题请自负风险。
