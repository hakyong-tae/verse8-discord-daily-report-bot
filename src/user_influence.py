import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from openai import OpenAI

from main import (
    GEMINI_API_BASE,
    KST,
    env_required,
    fetch_channel_messages,
    is_noise_message,
    load_channel_configs,
)


def build_time_window(days: int) -> tuple[datetime, datetime]:
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - timedelta(days=days)
    return start_utc, end_utc


def author_aliases(message: Dict[str, Any]) -> List[str]:
    author = message.get("author") or {}
    aliases = [
        str(author.get("username") or "").strip().lower(),
        str(author.get("global_name") or "").strip().lower(),
    ]
    return [a for a in aliases if a]


def message_matches_target(message: Dict[str, Any], target: str) -> bool:
    target = target.strip().lower()
    if not target:
        return False
    for alias in author_aliases(message):
        if alias == target:
            return True
    return False


def summarize_activity(messages: List[Dict[str, Any]]) -> str:
    if not messages:
        return "메시지 없음"
    days = set()
    for m in messages:
        ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00")).astimezone(KST)
        days.add(ts.date().isoformat())
    return f"총 {len(messages)}건 / 활동일 {len(days)}일"


def extract_keywords(messages: List[Dict[str, Any]]) -> List[str]:
    stopwords = {
        "the", "and", "that", "with", "this", "have", "from", "your", "they", "will",
        "just", "about", "there", "what", "when", "where", "which", "into", "game",
        "verse8", "https", "like", "good", "today", "please", "thanks", "thank",
        "그리고", "그냥", "에서", "하는", "저는", "제가", "이건", "그거", "있어요", "합니다",
        "너무", "관련", "같아요", "정도", "이제", "오늘", "내일", "정말", "위해", "대한",
    }
    counter: Counter[str] = Counter()
    for m in messages:
        content = (m.get("content") or "").lower()
        if not content or is_noise_message(content):
            continue
        for token in re.findall(r"[a-zA-Z0-9가-힣_]{3,}", content):
            if token in stopwords:
                continue
            counter[token] += 1
    return [word for word, _count in counter.most_common(12)]


def build_llm_input(
    target_username: str,
    days: int,
    messages_by_channel: Dict[str, List[Dict[str, Any]]],
    channels_by_id: Dict[str, str],
) -> str:
    lines: List[str] = []
    lines.append(f"대상 유저: {target_username}")
    lines.append(f"분석 기간: 최근 {days}일")
    for channel_id, messages in messages_by_channel.items():
        label = channels_by_id[channel_id]
        lines.append(f"\n[채널: {label}]")
        lines.append(f"- 활동 요약: {summarize_activity(messages)}")
        for m in messages[-80:]:
            author = ((m.get("author") or {}).get("global_name")
                      or (m.get("author") or {}).get("username")
                      or "unknown")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"- {author}: {content}")
    return "\n".join(lines)


def generate_influence_report_with_llm(
    provider: str,
    llm_input: str,
    target_username: str,
    days: int,
) -> str:
    report_time_kst = datetime.now(KST)
    system_prompt = f"""당신은 커뮤니티 운영 분석가다.
항상 한국어로 작성한다.
과장 없이 사실 중심으로 작성한다.
메시지 로그만 근거로 판단한다.

형식:
[Discord 유저 영향력 분석] – {target_username}

1. 활동 개요
- 메시지 빈도, 활동 채널, 활동 지속성

2. 영향력 평가
- 커뮤니티에서 어떤 역할을 하는지
- 분위기 형성, 질문 유도, 정보 공유, 문제 제기, 온보딩 기여 여부

3. 주요 관심사
- 반복적으로 다루는 주제 3~5개

4. 운영 관점 해석
- 이 유저를 왜 주목해야 하는지
- 리스크 또는 기회가 있는지

5. 한 줄 결론
- 운영진이 기억해야 할 핵심만 한 줄

추가 규칙:
- 영향력을 과장하지 않는다.
- 메시지량이 적으면 그 한계를 분명히 적는다.
- 긍정적 영향력과 부정적/리스크 요소가 함께 있으면 둘 다 적는다.
"""
    user_prompt = f"""보고 기준 시각(KST): {report_time_kst.strftime('%Y-%m-%d %H:%M')}

다음 로그를 바탕으로 유저 영향력을 분석해줘.

{llm_input}
"""

    if provider == "openai":
        openai_api_key = env_required("OPENAI_API_KEY")
        client = OpenAI(api_key=openai_api_key)
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1"),
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        return response.output_text.strip()

    gemini_api_key = env_required("GEMINI_API_KEY")
    url = f"{GEMINI_API_BASE}/models/{os.getenv('GEMINI_MODEL', 'gemini-2.5-flash-lite')}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    response = requests.post(url, params={"key": gemini_api_key}, json=payload, timeout=60)
    if response.status_code >= 300:
        raise RuntimeError(f"Gemini API error: {response.status_code} {response.text[:500]}")
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini API returned no candidates: {data}")
    parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise RuntimeError(f"Gemini API returned empty text: {data}")
    return text


def generate_fallback_report(
    target_username: str,
    days: int,
    messages_by_channel: Dict[str, List[Dict[str, Any]]],
    channels_by_id: Dict[str, str],
) -> str:
    all_messages = [m for msgs in messages_by_channel.values() for m in msgs]
    all_messages.sort(key=lambda m: m["timestamp"])
    keywords = extract_keywords(all_messages)
    channel_activity = sorted(
        ((channels_by_id[cid], len(msgs)) for cid, msgs in messages_by_channel.items()),
        key=lambda item: item[1],
        reverse=True,
    )

    lines: List[str] = []
    lines.append(f"[Discord 유저 영향력 분석] – {target_username}")
    lines.append("")
    lines.append("1. 활동 개요")
    lines.append(f"- 최근 {days}일 기준 총 {len(all_messages)}건의 메시지가 확인되었습니다.")
    if channel_activity:
        top_channels = ", ".join(f"{label}({count})" for label, count in channel_activity[:5])
        lines.append(f"- 주요 활동 채널: {top_channels}")
    else:
        lines.append("- 주요 활동 채널: 확인된 메시지 없음")
    lines.append("")
    lines.append("2. 영향력 평가")
    if len(all_messages) >= 50:
        lines.append("- 공개 채널에서 꾸준히 발언하는 편으로 보이며, 운영 이슈나 제작 관련 대화에 영향력을 가질 가능성이 있습니다.")
    elif len(all_messages) >= 15:
        lines.append("- 간헐적이지만 특정 채널에서 존재감이 있는 편으로 보입니다.")
    else:
        lines.append("- 메시지량이 적어 영향력을 강하게 단정하기는 어렵습니다.")
    lines.append("")
    lines.append("3. 주요 관심사")
    lines.append(f"- {', '.join(keywords[:8]) if keywords else '유의미한 반복 키워드 부족'}")
    lines.append("")
    lines.append("4. 운영 관점 해석")
    lines.append("- 이 유저가 주로 질문자/피드백 제공자/문제 제기자인지 실제 로그 기반 추가 확인이 필요합니다.")
    lines.append("- 정성 평가는 LLM 분석 모드에서 더 정확해집니다.")
    lines.append("")
    lines.append("5. 한 줄 결론")
    lines.append("- 최근 공개 채널 활동을 기준으로 운영 관점에서 관찰 가치가 있는 유저입니다.")
    return "\n".join(lines)


def main() -> int:
    try:
        discord_bot_token = env_required("DISCORD_BOT_TOKEN")
        target_username = env_required("TARGET_USERNAME")
        llm_provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
        analysis_window_days = int(os.getenv("ANALYSIS_WINDOW_DAYS", "60"))
        max_messages_per_channel = int(os.getenv("MAX_MESSAGES_PER_CHANNEL", "1000"))

        channels = load_channel_configs()
        channels_by_id = {channel.channel_id: channel.label for channel in channels}
        start_utc, end_utc = build_time_window(analysis_window_days)

        messages_by_channel: Dict[str, List[Dict[str, Any]]] = {}
        for channel in channels:
            try:
                raw_messages = fetch_channel_messages(
                    token=discord_bot_token,
                    channel_id=channel.channel_id,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    max_messages=max_messages_per_channel,
                )
            except PermissionError as e:
                print(f"WARNING: {e}")
                raw_messages = []
            matched = [m for m in raw_messages if message_matches_target(m, target_username)]
            if matched:
                messages_by_channel[channel.channel_id] = matched
                print(f"Matched {len(matched)} messages in {channel.label}")

        if not messages_by_channel:
            raise RuntimeError(f"No messages found for target user: {target_username}")

        llm_input = build_llm_input(target_username, analysis_window_days, messages_by_channel, channels_by_id)
        try:
            report = generate_influence_report_with_llm(
                provider=llm_provider,
                llm_input=llm_input,
                target_username=target_username,
                days=analysis_window_days,
            )
        except Exception as e:
            print(f"WARNING: LLM analysis failed, falling back to stats-only report: {e}")
            report = generate_fallback_report(
                target_username=target_username,
                days=analysis_window_days,
                messages_by_channel=messages_by_channel,
                channels_by_id=channels_by_id,
            )

        safe_target = re.sub(r"[^a-zA-Z0-9_-]+", "-", target_username).strip("-") or "user"
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / (
            f"user-influence-{safe_target}-{datetime.now(KST).strftime('%Y%m%d-%H%M')}.md"
        )
        output_path.write_text(report + "\n", encoding="utf-8")

        print("\n===== USER INFLUENCE REPORT =====\n")
        print(report)
        print(f"\nSaved report to {output_path.as_posix()}")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
