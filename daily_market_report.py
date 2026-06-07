#!/usr/bin/env python3
"""Send the daily market report to Discord.

This entrypoint is designed for cloud schedulers such as GitHub Actions. It can
run while the local computer is off, as long as the scheduler has the required
secrets.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from market_report_sources import collect


DISCORD_LIMIT = 1900
DEFAULT_MODEL = "gpt-5.4-mini"


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def clean_discord_webhook_url(value: str) -> str:
    value = value.strip().strip("'\"`<>")
    match = re.search(r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/[^\s'\"`<>]+", value)
    if match:
        return match.group(0)
    if not value.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        raise SystemExit(
            "DISCORD_WEBHOOK_URL must be the full Discord webhook URL. "
            "It should start with https://discord.com/api/webhooks/."
        )
    return value


def post_json(url: str, payload: dict[str, object]) -> None:
    url = clean_discord_webhook_url(url)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "daily-market-discord-report/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
    except urllib.error.URLError:
        subprocess.run(
            [
                "curl",
                "-sS",
                "-X",
                "POST",
                "-H",
                "Content-Type: application/json",
                "-d",
                data.decode("utf-8"),
                url,
            ],
            check=True,
        )


def chunk_message(message: str, limit: int = DISCORD_LIMIT) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in message.split("\n\n"):
        addition = paragraph if not current else f"\n\n{paragraph}"
        if len(current) + len(addition) <= limit:
            current += addition
            continue
        if current:
            chunks.append(current)
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def format_source(item: dict[str, object]) -> str:
    published = item.get("published_at") or "unknown"
    return (
        f"• {item['title']}\n"
        f"  Source: {item['source']} | Published UTC: {published}\n"
        f"  URL: {item['url']}"
    )


def fallback_report(payload: dict[str, object]) -> list[str]:
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    equities = payload["equity_sources"][:10]
    bitcoin = payload["bitcoin_sources"][:5]
    references = payload["reference_pages"]

    equity_lines = "\n\n".join(
        f"**[{index}] 🟡 {item['title']}**\n"
        f"• 출처: {item['source']}\n"
        f"• 발행: {item.get('published_at') or 'unknown'}\n"
        f"• 링크: {item['url']}"
        for index, item in enumerate(equities, start=1)
    )
    bitcoin_lines = "\n\n".join(
        f"**[{index}] 🟡 {item['title']}**\n"
        f"• 출처: {item['source']}\n"
        f"• 발행: {item.get('published_at') or 'unknown'}\n"
        f"• 링크: {item['url']}"
        for index, item in enumerate(bitcoin, start=1)
    )
    reference_lines = "\n".join(f"• {page['name']}: {page['url']}" for page in references)

    return [
        f"📌 **① 시장 요약**\n**기준:** {now_kst}\n\n"
        "OpenAI API 키가 없어 AI 선별 요약 대신 수집된 원문 출처 묶음을 보냅니다.\n"
        "아래 링크들은 보고서 생성 전에 RSS/공개 피드에서 직접 수집한 URL입니다.",
        f"📈 **② 미국 증시 Top 10 · 출처 기반**\n\n{equity_lines or '• 수집된 미국 증시 뉴스가 없습니다.'}",
        f"₿ **③ 비트코인 Top 5 · 출처 기반**\n\n{bitcoin_lines or '• 수집된 비트코인 뉴스가 없습니다.'}",
        f"🔗 **④ 교차확인 페이지**\n\n{reference_lines}",
    ]


def build_prompt(payload: dict[str, object]) -> str:
    equity_sources = "\n\n".join(format_source(item) for item in payload["equity_sources"][:40])
    bitcoin_sources = "\n\n".join(format_source(item) for item in payload["bitcoin_sources"][:25])
    reference_pages = "\n".join(f"• {page['name']}: {page['url']}" for page in payload["reference_pages"])
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")

    return textwrap.dedent(
        f"""
        You are writing a Korean Discord morning brief for a US market investor.
        Current report time: {now_kst}.

        Use only the source URLs listed below, or clearly say "확인 필요" when evidence is insufficient.
        Do not invent URLs. Do not use Markdown ordered-list syntax like "1.".
        Use Discord-friendly headers:
        📌 **① 시장 요약**
        📈 **② 미국 증시 Top 10**
        ₿ **③ 비트코인 Top 5**
        🗓️ **④ 오늘 체크할 변수**
        🔗 **⑤ 주요 출처**

        For issue numbering, use **[1]**, **[2]**, etc. For sub-lines, use • bullets.
        Mark impact as 🟢 긍정, 🔴 부정, or 🟡 혼재/중립.
        Keep each issue concise and attach source URLs.
        Return a JSON object shaped exactly like:
        {{"messages": ["Discord message 1", "Discord message 2"]}}
        Each message string must be under 1900 characters.

        EQUITY SOURCES:
        {equity_sources or "No equity sources collected."}

        BITCOIN SOURCES:
        {bitcoin_sources or "No bitcoin sources collected."}

        REFERENCE PAGES:
        {reference_pages}
        """
    ).strip()


def call_openai(prompt: str) -> list[str]:
    api_key = require_env("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    payload = {
        "model": model,
        "input": prompt,
        "text": {"format": {"type": "json_object"}},
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "daily-market-discord-report/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = json.loads(response.read().decode("utf-8"))

    text_parts: list[str] = []
    for output in raw.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text":
                text_parts.append(content.get("text", ""))
    if not text_parts:
        raise RuntimeError("OpenAI response did not contain output_text.")

    parsed = json.loads("\n".join(text_parts))
    if isinstance(parsed, dict):
        messages = parsed.get("messages") or parsed.get("discord_messages") or parsed.get("report")
    else:
        messages = parsed
    if not isinstance(messages, list) or not all(isinstance(item, str) for item in messages):
        raise RuntimeError("OpenAI response JSON must be an array of message strings.")
    return messages


def main() -> int:
    webhook_url = require_env("DISCORD_WEBHOOK_URL")
    payload = collect(hours=int(os.environ.get("REPORT_LOOKBACK_HOURS", "36")), timeout=15)

    if os.environ.get("OPENAI_API_KEY"):
        try:
            messages = call_openai(build_prompt(payload))
        except (RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
            messages = fallback_report(payload)
            messages[0] += f"\n\n⚠️ AI 요약 생성 실패로 출처 기반 보고서를 보냅니다: {exc}"
    else:
        messages = fallback_report(payload)

    for message in messages:
        for chunk in chunk_message(message):
            post_json(webhook_url, {"content": chunk})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
