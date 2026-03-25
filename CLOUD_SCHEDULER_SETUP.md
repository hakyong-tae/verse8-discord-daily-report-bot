# Cloud Scheduler Setup

This project now expects an external scheduler to trigger GitHub Actions via `workflow_dispatch`.

## What this gives you
- More reliable `09:30 KST` start time than GitHub `schedule`
- Keep the current GitHub Actions workflow and report logic
- Still effectively free for small usage

## 1) Create a GitHub personal access token

Use a token that can trigger workflows for this repo.

- GitHub -> `Settings` -> `Developer settings` -> `Personal access tokens`
- Create a token with access to this repository and workflow execution
- Store it somewhere safe

You will use it as:

- Header: `Authorization: Bearer <YOUR_GITHUB_TOKEN>`

## 2) Test the GitHub workflow dispatch API

Workflow file:

- `.github/workflows/daily-report.yml`

GitHub API endpoint:

```text
POST https://api.github.com/repos/hakyong-tae/verse8-discord-daily-report-bot/actions/workflows/daily-report.yml/dispatches
```

Example body:

```json
{
  "ref": "main",
  "inputs": {
    "force_send": "false"
  }
}
```

Example curl:

```bash
curl -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer <YOUR_GITHUB_TOKEN>" \
  https://api.github.com/repos/hakyong-tae/verse8-discord-daily-report-bot/actions/workflows/daily-report.yml/dispatches \
  -d '{"ref":"main","inputs":{"force_send":"false"}}'
```

Expected result:

- HTTP `204 No Content`

## 3) Create a Cloud Scheduler job

Suggested settings:

- Frequency: every day
- Time zone: `Asia/Seoul`
- Time: `09:30`
- Target type: `HTTP`
- Method: `POST`
- URL:

```text
https://api.github.com/repos/hakyong-tae/verse8-discord-daily-report-bot/actions/workflows/daily-report.yml/dispatches
```

Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer <YOUR_GITHUB_TOKEN>
Content-Type: application/json
```

Body:

```json
{"ref":"main","inputs":{"force_send":"false"}}
```

## 4) Keep the once-per-day guard on

The workflow still keeps:

- `SEND_ONCE_PER_DAY=true`
- state file `.state/last_sent_date_kst.txt`

So if you manually run the workflow later on the same day, it will skip unless you set:

```text
force_send=true
```

## 5) Recommended cleanup

After Cloud Scheduler is working:

- Keep using `workflow_dispatch`
- Do not re-enable GitHub `schedule`
- Check one morning that Slack delivery lands around `09:30~10:00 KST`
