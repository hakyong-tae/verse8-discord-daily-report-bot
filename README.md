# Verse8 Discord Daily Report Bot

Discord 주요 채널을 매일 읽어서, 기존 보고 포맷에 맞춰 요약한 뒤 Slack으로 자동 전송합니다.

## 1) 이 프로젝트가 하는 일
- 매일 **오전 10시(KST)** GitHub Actions가 실행됨
- 최근 24시간 Discord 메시지 수집
- OpenAI로 한국어 운영 리포트 생성
- Slack 채널로 자동 전송

## 2) 포함된 채널 (기본값)
- `general-chat` (1447614842394509452)
- `korean-chat` (1451451863156265115)
- `cpp-elites` (1465948768846348431)
- `creator-chat` (1390227183196176384)
- `korean-creator` (1401835443657510953)

기본값은 `src/main.py`에 이미 들어있고, 필요 시 `DISCORD_CHANNELS_JSON`으로 덮어쓸 수 있습니다.

## 3) Discord Bot 준비
1. Discord Developer Portal에서 Bot 생성
2. Bot Token 발급
3. Bot 설정에서 아래 활성화
- `SERVER MEMBERS INTENT`
- `MESSAGE CONTENT INTENT` (중요)
4. 서버(Verse8)에 Bot 초대
5. 위 5개 채널에서 Bot에 최소 권한 부여
- View Channel
- Read Message History

## 4) Slack Webhook 준비
1. Slack App 생성 후 Incoming Webhooks 활성화
2. 전송 대상 채널: `#v8-dogfooding`
3. Webhook URL 발급

참고: 공유 초대 링크(`join.slack.com/...`)는 전송 API URL이 아닙니다.
반드시 `https://hooks.slack.com/services/...` 형태의 Webhook URL이 필요합니다.

## 5) GitHub 저장소에 업로드
```bash
git init
git add .
git commit -m "Add daily Discord->Slack report bot"
git branch -M main
git remote add origin <YOUR_REPO_URL>
git push -u origin main
```

## 6) GitHub Secrets 설정
Repository > Settings > Secrets and variables > Actions > Secrets

아래 3개를 등록하세요.
- `DISCORD_BOT_TOKEN`
- `OPENAI_API_KEY`
- `SLACK_WEBHOOK_URL`

## 7) 선택 옵션 (Variables)
Repository > Settings > Secrets and variables > Actions > Variables

- `OPENAI_MODEL` (기본 `gpt-4.1`)
- `WINDOW_HOURS` (기본 `24`)
- `MAX_MESSAGES_PER_CHANNEL` (기본 `400`)
- `DISCORD_CHANNELS_JSON` (선택)

## 8) 실행 방식
- 자동: 매일 10:00 KST
- 수동: GitHub Actions 탭 > `Verse8 Discord Daily Report` > `Run workflow`

## 9) 현재 보고서 포맷 유지
리포트는 아래 머리말 형식을 고정해서 생성됩니다.

`[Verse 8 디스코드 현황 보고] – YYYY년 M월 D일 10:00 기준`

그리고 채널별 문단 + 마지막 `운영 메모`를 포함합니다.

## 10) 로컬 테스트(선택)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env 값 채우기 후
export $(grep -v '^#' .env | xargs)
python src/main.py
```
