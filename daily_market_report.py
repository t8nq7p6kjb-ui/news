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

SOURCE_NAME_KO = {
    "Yahoo Finance": "야후 파이낸스",
    "CNBC Markets": "CNBC 마켓",
    "CNBC Economy": "CNBC 경제",
    "CNN Business": "CNN 비즈니스",
    "AP Business": "AP 비즈니스",
    "Investing.com Stock Market News": "인베스팅닷컴 증시 뉴스",
    "Seeking Alpha Market News": "시킹알파 마켓 뉴스",
    "ZDNet": "ZDNet",
    "CoinDesk": "코인데스크",
    "Cointelegraph": "코인텔레그래프",
    "Bitcoin Magazine": "비트코인 매거진",
    "Finviz Market Map": "핀비즈 마켓맵",
    "Finviz": "핀비즈",
    "CNBC US Markets": "CNBC 미국 시장",
    "CNN Fear & Greed": "CNN 공포와 탐욕 지수",
    "Hankyung Global Market": "한경 글로벌마켓",
}


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
            body = response.read().decode("utf-8", errors="replace")
            print(f"Discord POST succeeded with HTTP {response.status}. Response length: {len(body)}")
    except urllib.error.URLError as exc:
        print(f"urllib Discord POST failed, retrying with curl: {exc}")
        completed = subprocess.run(
            [
                "curl",
                "-sS",
                "-w",
                "\nHTTP_STATUS:%{http_code}\n",
                "-X",
                "POST",
                "-H",
                "Content-Type: application/json",
                "-d",
                data.decode("utf-8"),
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print(completed.stdout)


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
    published = item.get("published_at") or "확인 필요"
    source_name = SOURCE_NAME_KO.get(str(item["source"]), str(item["source"]))
    return (
        f"• {item['title']}\n"
        f"  출처: {source_name} | 발행 시각(UTC): {published}\n"
        f"  링크: {item['url']}"
    )


def fallback_report(payload: dict[str, object]) -> list[str]:
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    equities = payload["equity_sources"][:10]
    bitcoin = payload["bitcoin_sources"][:5]
    references = payload["reference_pages"]

    equity_lines = "\n\n".join(
        f"**[{index}] 🟡 원문 뉴스 링크**\n"
        f"• 출처: {SOURCE_NAME_KO.get(str(item['source']), str(item['source']))}\n"
        f"• 발행: {item.get('published_at') or '확인 필요'}\n"
        f"• 링크: {item['url']}"
        for index, item in enumerate(equities, start=1)
    )
    bitcoin_lines = "\n\n".join(
        f"**[{index}] 🟡 원문 뉴스 링크**\n"
        f"• 출처: {SOURCE_NAME_KO.get(str(item['source']), str(item['source']))}\n"
        f"• 발행: {item.get('published_at') or '확인 필요'}\n"
        f"• 링크: {item['url']}"
        for index, item in enumerate(bitcoin, start=1)
    )
    reference_lines = "\n".join(
        f"• {SOURCE_NAME_KO.get(str(page['name']), str(page['name']))}: {page['url']}"
        for page in references
    )

    return [
        f"📌 **① 시장 요약**\n**기준:** {now_kst}\n\n"
        "OpenAI API 키가 없어 AI 선별 요약 대신 수집된 원문 출처 묶음을 보냅니다.\n"
        "영문 원문 제목은 번역 품질 보장을 위해 표시하지 않고, 한국어 라벨과 원문 링크만 제공합니다.",
        f"📈 **② 미국 증시 상위 10개 · 출처 기반**\n\n{equity_lines or '• 수집된 미국 증시 뉴스가 없습니다.'}",
        f"₿ **③ 비트코인 상위 5개 · 출처 기반**\n\n{bitcoin_lines or '• 수집된 비트코인 뉴스가 없습니다.'}",
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

        Critical language rule:
        - Every Discord message must be written in Korean.
        - Translate English headlines and summaries into natural Korean.
        - Do not leave English section labels such as "Top 10", "Source", "Published", or "Summary".
        - Ticker symbols, company names, product names, URLs, and source brand names may remain in their standard form when translating them would be awkward.
        - If a headline cannot be translated confidently, summarize its meaning in Korean and attach the original URL.

        Use only the source URLs listed below, or clearly say "확인 필요" when evidence is insufficient.
        Do not invent URLs. Do not use Markdown ordered-list syntax like "1.".
        Use Discord-friendly headers:
        📌 **① 시장 요약**
        📈 **② 미국 증시 상위 10개**
        ₿ **③ 비트코인 상위 5개**
        🗓️ **④ 오늘 체크할 변수**
        🔗 **⑤ 주요 출처**

        For issue numbering, use **[1]**, **[2]**, etc. For sub-lines, use • bullets.
        Mark impact as 🟢 긍정, 🔴 부정, or 🟡 혼재/중립.
        Keep each issue concise and attach source URLs.
        Return a JSON object shaped exactly like:
        {{"messages": ["한국어 Discord 메시지 1", "한국어 Discord 메시지 2"]}}
        Each message string must be under 1900 characters.

        미국 증시 원문 출처:
        {equity_sources or "수집된 미국 증시 원문 출처가 없습니다."}

        비트코인 원문 출처:
        {bitcoin_sources or "수집된 비트코인 원문 출처가 없습니다."}

        교차확인 페이지:
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
            print(f"AI summary failed, using Korean fallback report: {exc}")
            messages = fallback_report(payload)
            messages[0] += "\n\n⚠️ AI 요약 생성에 실패해 출처 기반 한국어 보고서로 대체 발송합니다."
    else:
        messages = fallback_report(payload)

    print(f"Prepared {len(messages)} Discord message(s).")
    for message in messages:
        chunks = chunk_message(message)
        print(f"Sending message split into {len(chunks)} chunk(s).")
        for chunk in chunks:
            post_json(webhook_url, {"content": chunk})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
