# youxuanyuming — Cloudflare 优选域名自动维护

Fork 自 [jc-lw/youxuanyuming](https://github.com/jc-lw/youxuanyuming)（上游：[tianshipapa](https://github.com/tianshipapa) / [ymyuuu/BestDomain](https://github.com/ymyuuu/BestDomain)），本 fork 修复了上游的一系列问题（见下方「相对上游的改动」），并改造成零依赖、可长期稳定运行的版本。

## 它做什么

GitHub Actions 定时任务自动维护，**官方池和反代池分节奏**：

- **官方池**（`cf` / `cloudflare`）：每 **3 小时**换新（:17 批次）
- **反代池**（`proxy`）：每 **1.5 小时**换新（:17 批次跑全量 + :47 批次只跑反代）

每次运行：

1. 按 25 个公开数据源抓取 IPv4（坏源自动跳过）：16 个官方 IP 源 + 9 个反代源；其中 1 个官方源通过 DNS 解析优选域名的 A 记录获取（[saas.sin.fan](https://saas.sin.fan)，站长实测维护）
2. 去重、校验、限量后写入 `ip.txt`（官方）和 `proxy.txt`（反代）并提交到仓库；官方列表**只保留 Cloudflare 官方网段的 IP**（并排除 WARP 专用段）；源大面积异常时保留旧文件不写（防池子被砍残）
3. 调 Cloudflare API，把下面三个域名的 A 记录同步成最新优选 IP（**灰云 / DNS-only**）：

| 域名 | IP 来源 | 说明 |
| --- | --- | --- |
| `cf.223226.xyz` | [ip.164746.xyz](https://ip.164746.xyz/ipTop10.html) Top10 + [saas.sin.fan](https://saas.sin.fan) SIN 优选域名（static 锁定多视角全部 7 条已知 IP + dns-multi 自动发现新记录）+ [CloudFlareYes 电信](https://addressesapi.090227.xyz/ct) + [微测网](https://www.wetest.vip/page/cloudflare/address_v4.html) | 官方网段·精选，每 3 小时 |
| `cloudflare.223226.xyz` | 本仓库 `ip.txt`（全部 16 个官方源合并） | 官方网段·全量，上限 50 个，每 3 小时 |
| `proxy.223226.xyz` | 本仓库 `proxy.txt`（[IPDB bestproxy](https://ipdb.api.030101.xyz/?type=bestproxy) + MJZ 联通/电信 + [gaoji.uk](https://ips.gaoji.uk/best_ips.txt) 移动 + LZ 联通 + Xiaobei09 稳定版 + LancelotRar/S5/Laziji 聚合兜底，只取 443 端口） | **第三方反代节点**，上限 50 个，每 1.5 小时 |

> ⚠️ **反代域名的风险须知**：`proxy.223226.xyz` 里的 IP 是第三方架设的中转服务器（非 Cloudflare 官方网段），你的流量会经过这些陌生服务器，理论上可被嗅探/记录。速度可能比官方 IP 快，但请自行权衡风险，不要在上面传输敏感数据。

`ip.txt` 的数据源（16 个，按优先级）：IPDB bestcf、**SIN 优选域名 saas.sin.fan**（站长实测维护）、ip.164746.xyz Top10、addressesapi 电信/三网、cf.090227 三网接口、vvhan 三网、NiREvil 三网、天诚三网、Senflare、Einsitang、Joname 聚合、api.uouin.com、wetest.vip。
`proxy.txt` 的数据源（9 个，按优先级）：IPDB bestproxy（每小时实测）、MJZ 联通/电信（45 分钟实测）、gaoji.uk 移动实测、LZ 联通实测、Xiaobei09 二筛稳定版、LancelotRar 聚合、S5公益、Laziji。

来源参考：[bestcf.pages.dev](https://bestcf.pages.dev/)（EDT 优选导航站）。已排查并排除的伪优选域名：cf.877774.xyz（CNAME 蹭 www.wto.org 橙云记录）、youxuan.cf.090227.xyz（轮换 CNAME 到 Coinbase/Udacity CDN）、cf.3666888.xyz（GeoDNS 分地区，海外视角拿不到国内记录）。

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
nslookup proxy.223226.xyz
```

前两个会解析出一批 104.x / 162.159.x / 172.64.x 的官方网段地址；`proxy.223226.xyz` 解析出的是第三方反代 IP。

## 怎么用

在代理客户端（v2rayN / Clash Meta / Shadowrocket / sing-box 等）里：

- **address / server** 填 `cf.223226.xyz`、`cloudflare.223226.xyz`（官方 IP）或 `proxy.223226.xyz`（反代 IP，需自担风险）
- **SNI / Host / peer** 填你真正走 CF 的域名（例如你自己的 Worker、Pages 或其它橙云域名）

## 手动运行 / 试运行

Actions → **采集优选IP并更新DNS** → **Run workflow**：

- **范围**选 `all`（默认，官方+反代）/ `official`（只官方）/ `proxy`（只反代）
- 直接运行 = 立即采集并更新 DNS
- 勾选 **dry_run** = 只打印将要增删的记录，不实际改动

## 常见问题

- **多久更新一次？** 官方池（cf/cloudflare）每 3 小时（UTC `17 0,3,6,...` 批次）；反代池（proxy）每 1.5 小时（再叠加 `47 1,4,7,...` 批次只跑反代）。想改频率就编辑 `.github/workflows/update.yml` 里的 cron。
- **会动我手工加的 DNS 记录吗？** 不会。脚本只管理自己创建的记录（带 `managed-by:youxuanyuming` 注释），你手工加的同名 A 记录会被保留。
- **数据源挂了怎么办？** 单个源挂了自动跳过；源大面积异常（可用源少于 1/4 或结果少于 10 个）时保留旧文件不动；两个域名各自的有效 IP 少于 2 个时会跳过更新，不会清空。
- **为什么有的来源抓到的 IP 会变少？** 官方域名（cf/cloudflare）会过滤掉不属于 Cloudflare 官方网段的 IP，只保留官方网段；反代域名（proxy）只保留 443 端口的条目（非 443 端口对 DNS 优选域名无意义）。
- **反代 IP 是什么？** 第三方架设的中转服务器，帮你把流量转发到 Cloudflare。速度可能更快，但流量会经过陌生人的服务器，请自行权衡（见上方风险须知）。
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
- 新增反代域名 `proxy.223226.xyz` 与 `proxy.txt`（IPDB bestproxy + gaoji.uk + LancelotRar，只取 443 端口），与官方域名分开维护
- 数据源扩充到 24 个（官方 15 + 反代 9，参考 bestcf.pages.dev 导航站收录），并排除 WARP 专用网段（162.159.192.0/21，bestcf.pages.dev 文件头的 162.159.198.1 是 WARP 端点，不能当优选 IP）
- 新增「优选域名 A 记录采集」源型（dns 型源）：saas.sin.fan（已验证为站长实测维护的灰云记录）
- 新增「多视角 DNS 采集」源型（dns-multi 型源）与「固定 IP 列表」源型（static 型源）
- 同步频率拆分：官方池每 3 小时、反代池每 1.5 小时（同一 workflow 两个 cron 批次，按触发的 cron 区分范围）
- 采集增加「源大面积异常」守卫：可用源少于 1/4 或结果少于 10 个时不写文件，防止网络故障时把池子砍残
- `cf` 域名源定型：ipTop10 + SIN 优选域名（static 锁定全部 7 条多视角已知 IP + dns-multi 自动发现）+ CloudFlareYes 电信 + 微测网

## 开源协议

沿用上游：欢迎使用、修改和传播。免责声明：脚本尽力确保安全，但任何使用问题请自负风险。
