import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests
from dateutil import parser as date_parser
from openai import OpenAI

DISCORD_API_BASE = "https://discord.com/api/v10"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
KST = timezone(timedelta(hours=9))
ISSUE_KEYWORDS = [
    "error",
    "bug",
    "issue",
    "fail",
    "failed",
    "connection",
    "connect",
    "login",
    "signin",
    "reward",
    "point",
    "points",
    "credit",
    "token",
    "listing",
    "fix",
    "broken",
    "problem",
    "cannot",
    "can't",
    "unable",
    "네트워크",
    "연결",
    "에러",
    "오류",
    "버그",
    "로그인",
    "접속",
    "복구",
    "보상",
    "포인트",
    "토큰",
    "상장",
    "개선",
    "수정",
    "안됨",
    "문제",
    "불가",
]


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
        ChannelConfig("1447610199585062973", "event-chat-1", "https://discord.com/channels/1374314257096900640/1447610199585062973"),
        ChannelConfig("1445637595995308093", "event-chat-2", "https://discord.com/channels/1374314257096900640/1445637595995308093"),
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


def is_noise_message(content: str) -> bool:
    text = content.strip().lower()
    if not text:
        return True

    # Ignore short greetings and reaction-only chatter.
    greeting_patterns = [
        r"^(hi|hello|hey|gm|gn|good morning|good night|thx|thanks|ok|okay|nice|lol|lfg)[!.~\s]*$",
        r"^(안녕|안녕하세요|좋은 아침|굿모닝|굿밤|감사|고마워|오케이|화이팅|ㅋㅋ+|ㅎㅎ+)[!.~\s]*$",
    ]
    for p in greeting_patterns:
        if re.match(p, text):
            return True

    # Very short non-informative lines are usually not issues.
    if len(text) <= 8:
        return True
    return False


def issue_score(content: str) -> int:
    text = content.lower()
    score = 0
    for kw in ISSUE_KEYWORDS:
        if kw in text:
            score += 2
    if "?" in text:
        score += 1
    if len(text) >= 50:
        score += 1
    return score


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

        if resp.status_code == 403:
            raise PermissionError(
                f"Discord access denied channel={channel_id}: {resp.status_code} {resp.text[:300]}"
            )

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
    max_messages_for_llm_per_channel: int,
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

        filtered = []
        for m in msgs:
            content = (m.get("content") or "").strip()
            if content and is_noise_message(content):
                continue
            filtered.append(m)

        if not filtered:
            chunks.append("- 이슈성 메시지 후보 없음(인사/짧은 잡담 제외)")
            continue

        for m in filtered[-max_messages_for_llm_per_channel:]:
            chunks.append(format_message(m))

    return "\n".join(chunks)


def report_prompts(
    llm_input: str,
    report_time_kst: datetime,
    channels: List[ChannelConfig],
) -> tuple[str, str]:
    system_prompt = f"""당신은 디스코드 커뮤니티 운영 리포트 작성자다.
항상 한국어로 작성한다.
과장 없이 사실 중심으로 작성한다.
아래 형식을 반드시 지킨다.

형식:
[Verse 8 디스코드 핵심 이슈 요약] – YYYY년 M월 D일 10:00 기준

1) 핵심 이슈 제목
- 왜 이슈인지 2~4문장 요약
- 관련 채널: 채널명 1~3개
- 즉시 액션: 한 줄

2) 핵심 이슈 제목
- 왜 이슈인지 2~4문장 요약
- 관련 채널: 채널명 1~3개
- 즉시 액션: 한 줄

3) 핵심 이슈 제목
- 왜 이슈인지 2~4문장 요약
- 관련 채널: 채널명 1~3개
- 즉시 액션: 한 줄

운영 메모
- 2~3줄로 오늘 우선순위만 작성

추가 규칙:
- 인사/잡담/짧은 리액션은 핵심 이슈에서 제외한다.
- 반드시 "이슈성 높은 내용"만 3개 고른다. 중요도가 낮으면 제외.
- 확실하지 않은 사실은 단정하지 말고 '보임', '추정됨', '언급됨' 표현 사용.
- 사용자 불편, 오류, 결제/보상, 운영정책, 이벤트 운영 리스크를 우선한다.
"""

    user_prompt = f"""보고 기준 시각(KST): {report_time_kst.strftime('%Y-%m-%d %H:%M')}

다음 원문 메시지를 바탕으로 보고서를 작성해줘.

{llm_input}
"""
    return system_prompt, user_prompt


def generate_report_openai(
    openai_api_key: str,
    model: str,
    llm_input: str,
    report_time_kst: datetime,
    channels: List[ChannelConfig],
) -> str:
    client = OpenAI(api_key=openai_api_key)
    system_prompt, user_prompt = report_prompts(llm_input, report_time_kst, channels)

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )

    return response.output_text.strip()


def generate_report_gemini(
    gemini_api_key: str,
    model: str,
    llm_input: str,
    report_time_kst: datetime,
    channels: List[ChannelConfig],
) -> str:
    system_prompt, user_prompt = report_prompts(llm_input, report_time_kst, channels)
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent"

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    resp = requests.post(
        url,
        params={"key": gemini_api_key},
        json=payload,
        timeout=60,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Gemini API error: {resp.status_code} {resp.text[:500]}")

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini API returned no candidates: {data}")

    parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise RuntimeError(f"Gemini API returned empty text: {data}")
    return text


def post_to_slack(webhook_url: str, text: str) -> None:
    resp = requests.post(webhook_url, json={"text": text}, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Slack webhook error: {resp.status_code} {resp.text[:300]}")


def generate_fallback_report(
    channels: List[ChannelConfig],
    messages_by_channel: Dict[str, List[Dict[str, Any]]],
    report_time_kst: datetime,
    reason: str,
) -> str:
    issue_candidates: List[Dict[str, Any]] = []
    issue_count_by_channel: Dict[str, int] = {}
    channel_by_id = {c.channel_id: c for c in channels}
    for ch in channels:
        msgs = messages_by_channel.get(ch.channel_id, [])
        issue_count = 0
        for m in msgs:
            content = (m.get("content") or "").strip()
            if not content or is_noise_message(content):
                continue
            score = issue_score(content)
            if score <= 0:
                continue
            issue_count += 1
            issue_candidates.append(
                {
                    "channel_id": ch.channel_id,
                    "label": ch.label,
                    "author": (m.get("author") or {}).get("username", "unknown"),
                    "content": content,
                    "score": score,
                    "ts": m.get("timestamp", ""),
                }
            )
        issue_count_by_channel[ch.channel_id] = issue_count

    issue_candidates.sort(key=lambda x: (x["score"], x["ts"]), reverse=True)
    top_candidates = issue_candidates[:3]

    lines: List[str] = []
    lines.append(
        f"[Verse 8 디스코드 핵심 이슈 요약] – {report_time_kst.year}년 {report_time_kst.month}월 {report_time_kst.day}일 10:00 기준"
    )
    lines.append("")
    lines.append("[안내] API 한도 이슈로 규칙 기반 핵심 이슈 추출 모드로 생성되었습니다.")
    lines.append("")
    if not top_candidates:
        lines.append("1) 유의미한 이슈 후보가 부족합니다.")
        lines.append("- 인사/잡담을 제외한 메시지에서 명확한 오류/보상/운영 이슈 키워드가 많지 않았습니다.")
        lines.append("- 즉시 액션: 이벤트/오류 관련 질문 템플릿을 고정 공지로 안내")
        lines.append("")
    else:
        for idx, c in enumerate(top_candidates, start=1):
            ch = channel_by_id[c["channel_id"]]
            total_msgs = len(messages_by_channel.get(ch.channel_id, []))
            content = c["content"]
            if len(content) > 190:
                content = content[:187] + "..."
            lines.append(f"{idx}) {ch.label} 이슈")
            lines.append(
                f"- '{content}' 관련 대화가 이슈 후보로 분류되었습니다(작성자: {c['author']})."
            )
            lines.append(
                f"- 관련 채널: {ch.label} (전체 {total_msgs}건, 이슈 후보 {issue_count_by_channel.get(ch.channel_id, 0)}건)"
            )
            lines.append("- 즉시 액션: 운영진 확인 후 공지/가이드/답변 상태를 채널에 명시")
            lines.append("")

    lines.append("운영 메모")
    lines.append("- LLM 쿼터 복구 시 문맥 기반 요약 품질이 개선됩니다.")
    lines.append("- 현재 모드는 키워드 기반이므로 맥락 누락 가능성이 있습니다.")
    lines.append("- 접근 불가 채널이 있으면 채널 권한(View/History)을 점검하세요.")
    return "\n".join(lines)


def main() -> int:
    try:
        discord_bot_token = env_required("DISCORD_BOT_TOKEN")
        slack_webhook_url = env_required("SLACK_WEBHOOK_URL")

        llm_provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1")
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        max_messages_per_channel = int(os.getenv("MAX_MESSAGES_PER_CHANNEL", "180"))
        max_messages_for_llm_per_channel = int(
            os.getenv("MAX_MESSAGES_FOR_LLM_PER_CHANNEL", "60")
        )

        channels = load_channel_configs()
        start_utc, end_utc = get_time_window()
        report_time_kst = end_utc.astimezone(KST)
        run_until_date = os.getenv("RUN_UNTIL_DATE", "").strip()
        if run_until_date:
            until = datetime.strptime(run_until_date, "%Y-%m-%d").date()
            if report_time_kst.date() > until:
                print(
                    f"Run skipped: report date {report_time_kst.date()} is after RUN_UNTIL_DATE={run_until_date}"
                )
                return 0

        messages_by_channel: Dict[str, List[Dict[str, Any]]] = {}
        accessible_channels = 0
        for ch in channels:
            try:
                messages = fetch_channel_messages(
                    token=discord_bot_token,
                    channel_id=ch.channel_id,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    max_messages=max_messages_per_channel,
                )
                messages_by_channel[ch.channel_id] = messages
                accessible_channels += 1
                print(f"Fetched {len(messages)} messages from {ch.label}")
            except PermissionError as e:
                messages_by_channel[ch.channel_id] = []
                print(f"WARNING: {e}")

        if accessible_channels == 0:
            raise RuntimeError(
                "Bot cannot access any configured channels. Check channel-level permissions in Discord."
            )

        llm_input = build_llm_input(
            channels,
            messages_by_channel,
            start_utc,
            end_utc,
            max_messages_for_llm_per_channel,
        )
        try:
            if llm_provider == "openai":
                openai_api_key = env_required("OPENAI_API_KEY")
                report = generate_report_openai(
                    openai_api_key, openai_model, llm_input, report_time_kst, channels
                )
            elif llm_provider == "gemini":
                gemini_api_key = env_required("GEMINI_API_KEY")
                report = generate_report_gemini(
                    gemini_api_key, gemini_model, llm_input, report_time_kst, channels
                )
            else:
                raise ValueError("LLM_PROVIDER must be one of: openai, gemini")
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "quota" in err or "insufficient_quota" in err:
                print(f"WARNING: LLM quota/rate issue, switching to fallback summary: {e}")
                report = generate_fallback_report(channels, messages_by_channel, report_time_kst, str(e))
            else:
                raise

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
