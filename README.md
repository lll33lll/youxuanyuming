# 223226.xyz 优选 IP / DNS 自动更新

这是基于 [jc-lw/youxuanyuming](https://github.com/jc-lw/youxuanyuming) 整理的个人版本：

1. 从公开页面收集 Cloudflare 公网 IPv4；
2. 保存到 [`ip.txt`](./ip.txt)；
3. 自动维护下面两个 DNS-only（灰云）记录：

- **bestcf.223226.xyz**：优先使用 `ip.164746.xyz` 的小列表；来源不可用时回退到本仓库的 `ip.txt`。
- **api.223226.xyz**：使用本次收集到的 `ip.txt`。

> 优选 IP 只是候选地址，不保证长期可用、速度或连通性；请遵守目标网络、服务商和 Cloudflare 的相关条款。本项目不会替代正常的源站安全配置。

## 首次配置

### 1. 添加 GitHub Actions Secret

在本仓库打开 **Settings → Secrets and variables → Actions → New repository secret**，添加：

- 名称：`CF_API_TOKEN`
- 值：Cloudflare API Token（不要使用 Global API Key）

建议 Token 权限最小化为：

- **Zone → DNS → Edit**
- Zone Resources 只选择 `223226.xyz`

如果仓库的 Actions 尚未启用，请先在 **Actions** 页面点击启用。

### 2. 手动运行

进入 **Actions → 更新优选 IP 和 Cloudflare DNS → Run workflow**。首次可以勾选 `dry_run` 检查抓取结果；确认无误后再正常运行。

工作流也会按 UTC 每 3 小时运行一次。GitHub Actions 的定时任务可能有延迟，不能当作精确计时器。

## 配置

默认配置在 [`config.json`](./config.json)：

- `zone_name`：Cloudflare Zone，当前为 `223226.xyz`；
- `max_records`：每个主机名最多保留的 A 记录数，当前为 20；
- `ip_sources`：收集 IP 的来源列表。

也可以通过 Actions 环境变量覆盖：

- `IP_SOURCES`：逗号分隔的来源 URL；
- `MAX_RECORDS`：写入 `ip.txt` 的最大地址数；
- `BESTCF_SOURCE_URL`：`bestcf` 的优先来源；
- `BESTCF_HOST` / `API_HOST`：两个子域名单标签；
- `MAX_DNS_RECORDS`：每个主机名写入 Cloudflare 的最大 A 记录数。

## 安全行为

- 只接受经过 `ipaddress` 校验的公网 IPv4，自动去重；
- 所有来源都失败或没有有效 IP 时，保留旧的 `ip.txt`，不继续更新 DNS；
- DNS 记录带有 `managed-by=youxuanyuming` 标记；未标记的现有 A 记录不会被脚本删除，会直接报错等待人工确认；
- 写 DNS 后会重新读取 Cloudflare API 校验最终记录集合；
- 自动提交 `ip.txt` 不会再次触发本工作流，避免循环。

## 本地运行

本项目只使用 Python 标准库，不需要安装第三方包：

```bash
python collect_ips.py
CF_API_TOKEN='你的令牌' python bestdomain.py
```

本地运行前请确认令牌只保存在环境变量或密码管理器中，绝不要提交到 Git。

## 文件说明

- `collect_ips.py`：抓取并生成 IP 列表；
- `bestdomain.py`：安全同步 Cloudflare DNS；
- `ip_utils.py`：IP 校验与提取共用函数；
- `.github/workflows/update.yml`：定时/手动工作流；
- `config.json`：非敏感项目配置。
