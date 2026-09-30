#!/usr/bin/env python3
"""Fetch Google Search Console metrics and send a Telegram Markdown report.

Required environment variables:
  GSC_SERVICE_ACCOUNT_JSON  Full service-account JSON (string) or path to the file
  GSC_SITE_URL              Property URL, e.g. sc-domain:lodi646app.ph
                            or https://lodi646app.ph/
  TELEGRAM_BOT_TOKEN        Bot token from @BotFather
  TELEGRAM_CHAT_ID          Destination chat / group / channel id

Optional:
  REPORT_LANG               Report language: en or zh (default en)
  GSC_LOOKBACK_DAYS         Comparison window (default 28)
  GSC_DATA_LAG_DAYS         GSC reporting lag (default 3)
  GSC_TOP_N                 Rows in each ranking table (default 8)
  GSC_INSPECT_N             Top pages to URL-inspect for index status (default 5)
"""

from __future__ import annotations

import html
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
]
TELEGRAM_MAX_CHARS = 3900
TZ = timezone(timedelta(hours=8))

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "📊 Daily GSC SEO Report",
        "site": "🌐 Site",
        "generated": "🕒 Generated",
        "current": "📅 Current",
        "previous": "🔁 Previous",
        "lag_note": "GSC data is usually delayed 2–3 days. This window already accounts for that lag.",
        "core_metrics": "📈 Core metrics (current vs previous window)",
        "clicks": "🖱️ Clicks",
        "impressions": "👀 Impressions",
        "ctr": "🎯 CTR",
        "avg_position": "📍 Avg position",
        "top_pages": "🏆 Top pages",
        "top_queries": "🔑 Top queries",
        "drop_pages": "📉 Biggest page drops (by clicks)",
        "drop_queries": "⚠️ Biggest query drops (by clicks)",
        "no_data": "No data",
        "delta_clicks": "Δ clicks",
        "clicks_short": "clicks",
        "impr_short": "impr",
        "pos_short": "pos",
        "sitemaps": "🗺️ Sitemaps / index coverage",
        "no_sitemaps": "No submitted sitemaps found. Check the property type and API access.",
        "submitted": "submitted",
        "indexed": "indexed",
        "updated": "updated",
        "total": "📦 Total",
        "coverage": "coverage",
        "index_status": "🔎 Index status (URL Inspection)",
        "no_inspect": "No URLs inspected (none available, or inspection is disabled).",
        "last_crawl": "last crawl",
        "footer": "Sent by GitHub Actions + Search Console API",
        "new": "new",
        "unknown": "Unknown",
        "inspect_failed": "inspect failed",
    },
    "zh": {
        "title": "📊 GSC 每日 SEO 简报",
        "site": "🌐 站点",
        "generated": "🕒 生成",
        "current": "📅 本期",
        "previous": "🔁 对照",
        "lag_note": "GSC 数据通常延迟 2–3 天，本期已按滞后期对齐。",
        "core_metrics": "📈 核心指标（本期 vs 上一窗口）",
        "clicks": "🖱️ 点击",
        "impressions": "👀 展现",
        "ctr": "🎯 CTR",
        "avg_position": "📍 平均排名",
        "top_pages": "🏆 表现最好的页面",
        "top_queries": "🔑 表现最好的关键词",
        "drop_pages": "📉 掉幅最大的页面（按点击下降）",
        "drop_queries": "⚠️ 掉幅最大的关键词（按点击下降）",
        "no_data": "暂无数据",
        "delta_clicks": "Δ点击",
        "clicks_short": "点击",
        "impr_short": "展现",
        "pos_short": "排名",
        "sitemaps": "🗺️ 站点地图 / 收录线索",
        "no_sitemaps": "未读到已提交的 Sitemap（请确认属性类型与权限）。",
        "submitted": "提交",
        "indexed": "索引",
        "updated": "更新",
        "total": "📦 合计",
        "coverage": "覆盖率",
        "index_status": "🔎 重点页面收录状态 (URL Inspection)",
        "no_inspect": "未检查（无可用页面或未开启检查）。",
        "last_crawl": "最近抓取",
        "footer": "自动化来源: GitHub Actions + Search Console API",
        "new": "新",
        "unknown": "未知",
        "inspect_failed": "查询失败",
    },
}


def normalize_lang(raw: str) -> str:
    key = (raw or "en").strip().lower().replace("_", "-")
    aliases = {
        "en": "en",
        "eng": "en",
        "english": "en",
        "zh": "zh",
        "cn": "zh",
        "zh-cn": "zh",
        "zh-hans": "zh",
        "chinese": "zh",
    }
    lang = aliases.get(key)
    if not lang:
        raise SystemExit(f"Unsupported REPORT_LANG={raw!r}. Use en or zh.")
    return lang


def load_strings() -> tuple[str, dict[str, str]]:
    lang = normalize_lang(env("REPORT_LANG", "en"))
    return lang, STRINGS[lang]


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value or ""


def load_credentials():
    raw = env("GSC_SERVICE_ACCOUNT_JSON", required=True).strip()
    if os.path.isfile(raw):
        return service_account.Credentials.from_service_account_file(raw, scopes=SCOPES)
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "GSC_SERVICE_ACCOUNT_JSON must be a JSON string or a file path"
        ) from exc
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def gsc_clients(creds):
    webmasters = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    return webmasters


def date_windows(lookback: int, lag: int) -> dict[str, date]:
    end = date.today() - timedelta(days=lag)
    current_start = end - timedelta(days=lookback - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=lookback - 1)
    return {
        "current_start": current_start,
        "current_end": end,
        "previous_start": previous_start,
        "previous_end": previous_end,
    }


def query_analytics(service, site_url: str, start: date, end: date, dimensions: list[str] | None = None, row_limit: int = 250) -> dict[str, Any]:
    body: dict[str, Any] = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "rowLimit": row_limit,
        "dataState": "final",
    }
    if dimensions:
        body["dimensions"] = dimensions
    request = service.searchanalytics().query(siteUrl=site_url, body=body)
    return request.execute() or {}


def totals_from_response(payload: dict[str, Any]) -> dict[str, float]:
    rows = payload.get("rows") or []
    if not rows:
        return {"clicks": 0.0, "impressions": 0.0, "ctr": 0.0, "position": 0.0}
    row = rows[0]
    clicks = float(row.get("clicks") or 0)
    impressions = float(row.get("impressions") or 0)
    ctr = float(row.get("ctr") or 0)
    position = float(row.get("position") or 0)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr,
        "position": position,
    }


def rows_by_key(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for row in payload.get("rows") or []:
        keys = row.get("keys") or [""]
        key = str(keys[0])
        out[key] = {
            "clicks": float(row.get("clicks") or 0),
            "impressions": float(row.get("impressions") or 0),
            "ctr": float(row.get("ctr") or 0),
            "position": float(row.get("position") or 0),
        }
    return out


def delta(current: float, previous: float) -> float:
    return current - previous


def pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None if current == 0 else None
    return (current - previous) / previous * 100


def fmt_int(n: float) -> str:
    return f"{int(round(n)):,}"


def fmt_pct(ratio: float) -> str:
    return f"{ratio * 100:.2f}%"


def fmt_pos(pos: float) -> str:
    return f"{pos:.1f}"


def fmt_delta(n: float, kind: str = "int") -> str:
    if kind == "pct_points":
        sign = "+" if n >= 0 else ""
        return f"{sign}{n * 100:.2f}pp"
    if kind == "pos":
        # Lower position is better; still show signed numeric change
        sign = "+" if n >= 0 else ""
        return f"{sign}{n:.1f}"
    sign = "+" if n >= 0 else ""
    return f"{sign}{int(round(n)):,}"


def fmt_pct_change(current: float, previous: float, new_label: str) -> str:
    change = pct_change(current, previous)
    if change is None:
        return "—" if current == 0 else new_label
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.1f}%"


def arrow(n: float, invert: bool = False) -> str:
    value = -n if invert else n
    if abs(value) < 1e-9:
        return "➡️"
    return "📈" if value > 0 else "📉"


def shorten(text: str, limit: int = 48) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def page_label(url: str) -> str:
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix) :]
            break
    return shorten(url, 52)


def compare_dimension(
    current_map: dict[str, dict[str, float]],
    previous_map: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    keys = set(current_map) | set(previous_map)
    items = []
    for key in keys:
        cur = current_map.get(key, {"clicks": 0.0, "impressions": 0.0, "ctr": 0.0, "position": 0.0})
        prev = previous_map.get(key, {"clicks": 0.0, "impressions": 0.0, "ctr": 0.0, "position": 0.0})
        items.append(
            {
                "key": key,
                "clicks": cur["clicks"],
                "impressions": cur["impressions"],
                "ctr": cur["ctr"],
                "position": cur["position"],
                "prev_clicks": prev["clicks"],
                "prev_position": prev["position"],
                "click_delta": delta(cur["clicks"], prev["clicks"]),
                "position_delta": delta(cur["position"], prev["position"]),
            }
        )
    return items


def top_by(items: list[dict[str, Any]], field: str, n: int, reverse: bool = True) -> list[dict[str, Any]]:
    return sorted(items, key=lambda x: x[field], reverse=reverse)[:n]


def biggest_drops(items: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    dropped = [x for x in items if x["click_delta"] < 0]
    dropped.sort(key=lambda x: (x["click_delta"], -x["prev_clicks"]))
    return dropped[:n]


def list_sitemaps(service, site_url: str) -> list[dict[str, Any]]:
    try:
        payload = service.sitemaps().list(siteUrl=site_url).execute() or {}
    except HttpError as exc:
        print(f"Sitemap list failed: {exc}", file=sys.stderr)
        return []
    return payload.get("sitemap") or []


def inspect_url(service, site_url: str, inspection_url: str, t: dict[str, str]) -> dict[str, str]:
    try:
        payload = (
            service.urlInspection()
            .index()
            .inspect(body={"inspectionUrl": inspection_url, "siteUrl": site_url})
            .execute()
        )
    except HttpError as exc:
        return {"url": inspection_url, "status": f"{t['inspect_failed']} ({exc.resp.status})"}
    result = (payload or {}).get("inspectionResult") or {}
    index_status = (result.get("indexStatusResult") or {})
    coverage = index_status.get("coverageState") or t["unknown"]
    verdict = index_status.get("verdict") or ""
    last = index_status.get("lastCrawlTime") or ""
    crawled = last[:10] if last else "—"
    label = coverage
    if verdict:
        label = f"{coverage} / {verdict}"
    return {"url": inspection_url, "status": label, "crawled": crawled}


def inspect_top_pages(service, site_url: str, pages: list[str], limit: int, t: dict[str, str]) -> list[dict[str, str]]:
    out = []
    for url in pages[:limit]:
        if not url.startswith("http"):
            continue
        out.append(inspect_url(service, site_url, url, t))
    return out


def render_metric_block(t: dict[str, str], current: dict[str, float], previous: dict[str, float]) -> list[str]:
    clicks_d = delta(current["clicks"], previous["clicks"])
    impr_d = delta(current["impressions"], previous["impressions"])
    ctr_d = delta(current["ctr"], previous["ctr"])
    pos_d = delta(current["position"], previous["position"])
    new_label = t["new"]
    return [
        f"*{t['core_metrics']}*",
        f"{t['clicks']}: `{fmt_int(current['clicks'])}` ({fmt_delta(clicks_d)} / {fmt_pct_change(current['clicks'], previous['clicks'], new_label)}) {arrow(clicks_d)}",
        f"{t['impressions']}: `{fmt_int(current['impressions'])}` ({fmt_delta(impr_d)} / {fmt_pct_change(current['impressions'], previous['impressions'], new_label)}) {arrow(impr_d)}",
        f"{t['ctr']}: `{fmt_pct(current['ctr'])}` ({fmt_delta(ctr_d, 'pct_points')}) {arrow(ctr_d)}",
        f"{t['avg_position']}: `{fmt_pos(current['position'])}` ({fmt_delta(pos_d, 'pos')}) {arrow(pos_d, invert=True)}",
        "",
    ]


def render_table(title: str, items: list[dict[str, Any]], kind: str, t: dict[str, str]) -> list[str]:
    lines = [f"*{title}*"]
    if not items:
        lines.append(f"_{t['no_data']}_")
        lines.append("")
        return lines
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, item in enumerate(items, 1):
        label = page_label(item["key"]) if kind == "page" else shorten(item["key"], 42)
        extra = ""
        if "click_delta" in item:
            extra = f"  {t['delta_clicks']} {fmt_delta(item['click_delta'])}"
        rank = medals.get(i, f"{i}.")
        lines.append(
            f"{rank} `{label}`  {t['clicks_short']} {fmt_int(item['clicks'])} · {t['impr_short']} {fmt_int(item['impressions'])} · {t['pos_short']} {fmt_pos(item['position'])}{extra}"
        )
    lines.append("")
    return lines


def render_sitemaps(sitemaps: list[dict[str, Any]], t: dict[str, str]) -> list[str]:
    lines = [f"*{t['sitemaps']}*"]
    if not sitemaps:
        lines.append(f"_{t['no_sitemaps']}_")
        lines.append("")
        return lines
    submitted_total = 0
    indexed_total = 0
    for sm in sitemaps:
        path = sm.get("path") or "sitemap"
        contents = sm.get("contents") or []
        submitted = 0
        indexed = 0
        for block in contents:
            submitted += int(block.get("submitted") or 0)
            indexed += int(block.get("indexed") or 0)
        submitted_total += submitted
        indexed_total += indexed
        last = (sm.get("lastSubmitted") or sm.get("lastDownloaded") or "")[:10] or "—"
        errors = sm.get("errors") or 0
        warnings = sm.get("warnings") or 0
        lines.append(
            f"• `{shorten(path, 56)}`  {t['submitted']} {fmt_int(submitted)} / {t['indexed']} {fmt_int(indexed)} · {t['updated']} {last} · err {errors} warn {warnings}"
        )
    coverage = (indexed_total / submitted_total * 100) if submitted_total else 0
    lines.append(
        f"{t['total']}: {t['submitted']} `{fmt_int(submitted_total)}` · {t['indexed']} `{fmt_int(indexed_total)}` · {t['coverage']} `{coverage:.1f}%`"
    )
    lines.append("")
    return lines


def render_index_status(rows: list[dict[str, str]], t: dict[str, str]) -> list[str]:
    lines = [f"*{t['index_status']}*"]
    if not rows:
        lines.append(f"_{t['no_inspect']}_")
        lines.append("")
        return lines
    for i, row in enumerate(rows, 1):
        crawled = row.get("crawled") or "—"
        lines.append(f"{i}. `{page_label(row['url'])}`  {row['status']} · {t['last_crawl']} {crawled}")
    lines.append("")
    return lines


def inline_md_to_html(line: str) -> str:
    parts = line.split("`")
    out = []
    for idx, part in enumerate(parts):
        escaped = html.escape(part)
        out.append(escaped if idx % 2 == 0 else f"<code>{escaped}</code>")
    return "".join(out)


def markdown_to_telegram_html(text: str) -> str:
    """Convert a small Markdown subset to Telegram HTML (more reliable than MarkdownV2)."""
    lines_out = []
    for line in text.split("\n"):
        if line.startswith("*") and line.endswith("*") and line.count("*") == 2:
            lines_out.append(f"<b>{html.escape(line[1:-1])}</b>")
        elif line.startswith("_") and line.endswith("_") and len(line) > 2 and line.count("_") == 2:
            lines_out.append(f"<i>{html.escape(line[1:-1])}</i>")
        else:
            lines_out.append(inline_md_to_html(line))
    return "\n".join(lines_out)


def chunk_text(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        extra = len(line) + (1 if current else 0)
        if size + extra > limit and current:
            chunks.append("\n".join(current))
            current = [line]
            size = len(line)
        else:
            current.append(line)
            size += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_telegram(token: str, chat_id: str, markdown_body: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    html_body = markdown_to_telegram_html(markdown_body)
    for i, chunk in enumerate(chunk_text(html_body), 1):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"Telegram API HTTP {exc.code}: {detail}") from exc
        if not body.get("ok"):
            raise SystemExit(f"Telegram API error: {body}")
        print(f"Sent Telegram message part {i}")


def build_report(
    t: dict[str, str],
    site_url: str,
    windows: dict[str, date],
    current_tot: dict[str, float],
    previous_tot: dict[str, float],
    pages: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    sitemaps: list[dict[str, Any]],
    inspections: list[dict[str, str]],
    top_n: int,
) -> str:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    cur_range = f"{windows['current_start']} ~ {windows['current_end']}"
    prev_range = f"{windows['previous_start']} ~ {windows['previous_end']}"
    best_pages = [p for p in top_by(pages, "clicks", top_n) if p["clicks"] > 0]
    best_queries = [q for q in top_by(queries, "clicks", top_n) if q["clicks"] > 0]
    drop_pages = biggest_drops(pages, top_n)
    drop_queries = biggest_drops(queries, top_n)

    lines = [
        f"*{t['title']}*",
        f"{t['site']}: `{site_url}`",
        f"{t['generated']}: `{now}` (UTC+8)",
        f"{t['current']}: `{cur_range}`",
        f"{t['previous']}: `{prev_range}`",
        f"_{t['lag_note']}_",
        "",
    ]
    lines += render_metric_block(t, current_tot, previous_tot)
    lines += render_table(t["top_pages"], best_pages, "page", t)
    lines += render_table(t["top_queries"], best_queries, "query", t)
    lines += render_table(t["drop_pages"], drop_pages, "page", t)
    lines += render_table(t["drop_queries"], drop_queries, "query", t)
    lines += render_sitemaps(sitemaps, t)
    lines += render_index_status(inspections, t)
    lines.append(f"_{t['footer']}_")
    return "\n".join(lines)


def write_temp_discovery_cache() -> None:
    # google-api-python-client may try to write discovery cache; keep CI clean
    os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")
    tempfile.tempdir = tempfile.gettempdir()


def main() -> int:
    write_temp_discovery_cache()
    lang, t = load_strings()
    print(f"Report language: {lang}")
    site_url = env("GSC_SITE_URL", required=True).strip()
    token = env("TELEGRAM_BOT_TOKEN", required=True).strip()
    chat_id = env("TELEGRAM_CHAT_ID", required=True).strip()
    lookback = int(env("GSC_LOOKBACK_DAYS", "28"))
    lag = int(env("GSC_DATA_LAG_DAYS", "3"))
    top_n = int(env("GSC_TOP_N", "8"))
    inspect_n = int(env("GSC_INSPECT_N", "5"))

    creds = load_credentials()
    service = gsc_clients(creds)
    windows = date_windows(lookback, lag)

    try:
        current_tot_raw = query_analytics(service, site_url, windows["current_start"], windows["current_end"])
        previous_tot_raw = query_analytics(service, site_url, windows["previous_start"], windows["previous_end"])
        current_pages = query_analytics(service, site_url, windows["current_start"], windows["current_end"], ["page"], 250)
        previous_pages = query_analytics(service, site_url, windows["previous_start"], windows["previous_end"], ["page"], 250)
        current_queries = query_analytics(service, site_url, windows["current_start"], windows["current_end"], ["query"], 250)
        previous_queries = query_analytics(service, site_url, windows["previous_start"], windows["previous_end"], ["query"], 250)
    except HttpError as exc:
        raise SystemExit(f"Search Console API error: {exc}") from exc

    current_tot = totals_from_response(current_tot_raw)
    previous_tot = totals_from_response(previous_tot_raw)
    pages = compare_dimension(rows_by_key(current_pages), rows_by_key(previous_pages))
    queries = compare_dimension(rows_by_key(current_queries), rows_by_key(previous_queries))
    sitemaps = list_sitemaps(service, site_url)

    top_page_urls = [p["key"] for p in top_by(pages, "impressions", inspect_n)]
    inspections = inspect_top_pages(service, site_url, top_page_urls, inspect_n, t) if inspect_n > 0 else []

    report = build_report(
        t=t,
        site_url=site_url,
        windows=windows,
        current_tot=current_tot,
        previous_tot=previous_tot,
        pages=pages,
        queries=queries,
        sitemaps=sitemaps,
        inspections=inspections,
        top_n=top_n,
    )
    send_telegram(token, chat_id, report)
    print("Report sent successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
