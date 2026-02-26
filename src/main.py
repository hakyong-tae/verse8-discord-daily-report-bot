import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests
from dateutil import parser as date_parser
from openai import OpenAI

DISCORD_API_BASE = "https://discord.com/api/v10"
KST = timezone(timedelta(hours=9))


@dataclass
class ChannelConfig:
    channel_id: str
    label: str
    url: str


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def load_channel_configs() -> List[ChannelConfig]:
    raw = os.getenv("DISCORD_CHANNELS_JSON", "").strip()
    if raw:
        parsed = json.loads(raw)
        channels = [
            ChannelConfig(
                channel_id=str(item["id"]),
                label=str(item["label"]),
                url=str(item.get("url", "")),
            )
            for item in parsed
        ]
        if channels:
            return channels

    # Default: user-provided Verse8 channels
    return [
        ChannelConfig("1447614842394509452", "general-chat", "https://discord.com/channels/1374314257096900640/1447614842394509452"),
        ChannelConfig("1451451863156265115", "korean-chat", "https://discord.com/channels/1374314257096900640/1451451863156265115"),
        ChannelConfig("1465948768846348431", "cpp-elites", "https://discord.com/channels/1374314257096900640/1465948768846348431"),
        ChannelConfig("1390227183196176384", "creator-chat", "https://discord.com/channels/1374314257096900640/1390227183196176384"),
        ChannelConfig("1401835443657510953", "korean-creator", "https://discord.com/channels/1374314257096900640/1401835443657510953"),
    ]


def get_time_window() -> tuple[datetime, datetime]:
    now_utc = datetime.now(timezone.utc)

    window_hours = int(os.getenv("WINDOW_HOURS", "24"))
    end_utc = now_utc
    start_utc = end_utc - timedelta(hours=window_hours)

    return start_utc, end_utc


def discord_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "verse8-discord-daily-report-bot/1.0",
    }


def format_message(m: Dict[str, Any]) -> str:
    author = (m.get("author") or {}).get("username", "unknown")
    content = (m.get("content") or "").strip()
    if not content:
        # Fallback for embeds/attachments-only posts
        parts = []
        if m.get("attachments"):
            parts.append(f"[attachments:{len(m['attachments'])}]")
        if m.get("embeds"):
            parts.append(f"[embeds:{len(m['embeds'])}]")
        content = " ".join(parts) if parts else "[non-text message]"

    return f"- {author}: {content}"


def fetch_channel_messages(
    token: str,
    channel_id: str,
    start_utc: datetime,
    end_utc: datetime,
    max_messages: int,
) -> List[Dict[str, Any]]:
    headers = discord_headers(token)
    results: List[Dict[str, Any]] = []
    before: str | None = None

    while len(results) < max_messages:
        params: Dict[str, Any] = {"limit": 100}
        if before:
            params["before"] = before

        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Discord API error channel={channel_id}: {resp.status_code} {resp.text[:300]}"
            )

        batch = resp.json()
        if not batch:
            break

        reached_older_than_start = False
        for msg in batch:
            ts = date_parser.isoparse(msg["timestamp"]).astimezone(timezone.utc)

            if ts > end_utc:
                continue
            if ts < start_utc:
                reached_older_than_start = True
                continue

            if msg.get("type") not in (0, 19):
                # 0: default, 19: reply
                continue

            results.append(msg)
            if len(results) >= max_messages:
                break

        before = batch[-1]["id"]
        if reached_older_than_start:
            break

    results.sort(key=lambda x: x["timestamp"])
    return results


def build_llm_input(
    channels: List[ChannelConfig],
    messages_by_channel: Dict[str, List[Dict[str, Any]]],
    start_utc: datetime,
    end_utc: datetime,
) -> str:
    start_kst = start_utc.astimezone(KST)
    end_kst = end_utc.astimezone(KST)

    chunks: List[str] = []
    chunks.append(
        f"분석 기간(KST): {start_kst.strftime('%Y-%m-%d %H:%M')} ~ {end_kst.strftime('%Y-%m-%d %H:%M')}"
    )

    for ch in channels:
        msgs = messages_by_channel.get(ch.channel_id, [])
        chunks.append(f"\n[채널: {ch.label}] ({ch.url})")
        if not msgs:
            chunks.append("- 최근 기간 내 메시지 없음")
            continue

        for m in msgs:
            chunks.append(format_message(m))

    return "\n".join(chunks)


def generate_report(
    openai_api_key: str,
    model: str,
    llm_input: str,
    report_time_kst: datetime,
) -> str:
    client = OpenAI(api_key=openai_api_key)

    system_prompt = """당신은 디스코드 커뮤니티 운영 리포트 작성자다.
항상 한국어로 작성한다.
과장 없이 사실 중심으로 작성한다.
아래 형식을 반드시 지킨다.

형식:
[Verse 8 디스코드 현황 보고] – YYYY년 M월 D일 10:00 기준

1) general-chat
- 문단형 서술로 핵심 이슈를 요약

2) korean-chat
- 문단형 서술

3) cpp-elites
- 문단형 서술

4) creator-chat
- 문단형 서술

5) korean-creator
- 문단형 서술

추가 규칙:
- 채널별로 긍정 분위기, 주요 질문/이슈, 운영진 대응, 제안/피드백이 있으면 반영.
- 확실하지 않은 사실은 단정하지 말고 '보임', '추정됨', '언급됨' 표현 사용.
- 메시지가 거의 없으면 그 사실을 짧게 명시.
- 마지막에 '운영 메모' 2~4줄 추가: 오늘 바로 확인할 액션만 간결히 작성.
"""

    user_prompt = f"""보고 기준 시각(KST): {report_time_kst.strftime('%Y-%m-%d %H:%M')}

다음 원문 메시지를 바탕으로 보고서를 작성해줘.

{llm_input}
"""

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )

    return response.output_text.strip()


def post_to_slack(webhook_url: str, text: str) -> None:
    resp = requests.post(webhook_url, json={"text": text}, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Slack webhook error: {resp.status_code} {resp.text[:300]}")


def main() -> int:
    try:
        discord_bot_token = env_required("DISCORD_BOT_TOKEN")
        openai_api_key = env_required("OPENAI_API_KEY")
        slack_webhook_url = env_required("SLACK_WEBHOOK_URL")

        model = os.getenv("OPENAI_MODEL", "gpt-4.1")
        max_messages_per_channel = int(os.getenv("MAX_MESSAGES_PER_CHANNEL", "400"))

        channels = load_channel_configs()
        start_utc, end_utc = get_time_window()
        report_time_kst = end_utc.astimezone(KST)

        messages_by_channel: Dict[str, List[Dict[str, Any]]] = {}
        for ch in channels:
            messages = fetch_channel_messages(
                token=discord_bot_token,
                channel_id=ch.channel_id,
                start_utc=start_utc,
                end_utc=end_utc,
                max_messages=max_messages_per_channel,
            )
            messages_by_channel[ch.channel_id] = messages
            print(f"Fetched {len(messages)} messages from {ch.label}")

        llm_input = build_llm_input(channels, messages_by_channel, start_utc, end_utc)
        report = generate_report(openai_api_key, model, llm_input, report_time_kst)

        print("\n===== GENERATED REPORT =====\n")
        print(report)

        post_to_slack(slack_webhook_url, report)
        print("\nPosted report to Slack successfully.")
        return 0

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
