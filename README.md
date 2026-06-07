# Daily US Market Discord Report

매일 오전 6시 25분 KST에 미국 증시와 비트코인 주요 뉴스를 Discord로 보내는 자동화입니다.

## Local Files

- `market_report_sources.py`: RSS와 공개 출처에서 뉴스 제목, URL, 발행시각을 수집합니다.
- `daily_market_report.py`: 수집된 출처를 바탕으로 Discord 보고서를 전송합니다.
- `.github/workflows/daily-market-report.yml`: GitHub Actions에서 매일 자동 실행합니다.

## GitHub Actions Setup

Mac이 꺼져 있어도 실행하려면 이 폴더를 GitHub repository에 push한 뒤 아래 값을 repository secrets에 넣어야 합니다.

### Required Secret

- `DISCORD_WEBHOOK_URL`: 보고서를 받을 Discord Webhook URL

### Optional Secret

- `OPENAI_API_KEY`: AI 요약 보고서를 만들 때 사용합니다.

`OPENAI_API_KEY`가 없으면 AI 요약 대신 수집된 출처 기반 요약을 Discord로 보냅니다.

### Optional Variable

- `OPENAI_MODEL`: 기본값은 `gpt-5.4-mini`

## Schedule

GitHub Actions는 UTC 기준으로 실행되므로 한국시간 오전 6시 25분은 아래 cron으로 설정되어 있습니다.

```yaml
cron: "25 21 * * *"
```

## Manual Test

GitHub repository에서 다음 경로로 이동해 수동 실행할 수 있습니다.

```text
Actions > Daily market Discord report > Run workflow
```

## Local Test

```bash
DISCORD_WEBHOOK_URL="..." python3 daily_market_report.py
```

