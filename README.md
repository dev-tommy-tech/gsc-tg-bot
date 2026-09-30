# GSC 每日 SEO 简报 → Telegram

每天用 Google Search Console API 拉取指定站点的点击、展现、CTR、平均排名，对比上一窗口找出表现最好和掉幅最大的页面/关键词，并读取 Sitemap 提交/索引量、对重点 URL 做收录检查，再通过 Telegram Bot 发出可读报告。

GitHub Actions 工作流：`.github/workflows/gsc_report.yml`（每天 01:00 UTC / 北京时间 09:00，也可手动 Run workflow）。

## 1. Google Cloud + Search Console

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)，新建或选用项目。
2. 启用 **Google Search Console API**。
3. 创建 **服务账号**，下载 JSON 密钥（整份 JSON 后面会放到 GitHub Secret）。
4. 打开 [Search Console](https://search.google.com/search-console)，在对应资源（域名资源 `sc-domain:lodi646app.ph` 或网址前缀 `https://lodi646app.ph/`）里，把该服务账号邮箱加为 **完全用户**。

`GSC_SITE_URL` 必须与 Search Console 里的资源标识完全一致。

## 2. Telegram

1. 用 [@BotFather](https://t.me/BotFather) 创建 Bot，拿到 token。
2. 把 Bot 拉进目标群/频道，或先私聊 Bot。
3. 获取 Chat ID（可用 `@userinfobot`，或把 Bot 拉进群后看 `getUpdates`）。

## 3. GitHub Secrets

仓库 **Settings → Secrets and variables → Actions** 添加：

| Secret | 说明 |
| --- | --- |
| `GSC_SERVICE_ACCOUNT_JSON` | 服务账号 JSON 全文 |
| `GSC_SITE_URL` | 例如 `sc-domain:lodi646app.ph` |
| `TELEGRAM_BOT_TOKEN` | Bot token |
| `TELEGRAM_CHAT_ID` | 数字 ID，频道可能是 `-100...` |

## 4. 本地试跑

```bash
cd gsc_report
pip install -r requirements.txt
set GSC_SERVICE_ACCOUNT_JSON=C:\path\to\service-account.json
set GSC_SITE_URL=sc-domain:lodi646app.ph
set TELEGRAM_BOT_TOKEN=123:abc
set TELEGRAM_CHAT_ID=123456789
python gsc_telegram_report.py
```

可选环境变量：`GSC_LOOKBACK_DAYS`（默认 28）、`GSC_DATA_LAG_DAYS`（默认 3）、`GSC_TOP_N`（默认 8）、`GSC_INSPECT_N`（默认 5，设为 0 可跳过 URL Inspection）。
