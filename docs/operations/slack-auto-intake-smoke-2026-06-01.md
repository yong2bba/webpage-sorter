# SourceLab Slack Auto Intake 운영 검증 — 2026-06-01

## 결론

SourceLab collector는 Slack `#sourcelab` 채널에서 GitHub URL을 받으면 LLM 자유응답으로 보내지 않고 deterministic hook으로 SourceLab intake를 실행하는 구조까지 검증됐다.

최종 live 검증 대상은 다음 URL이다.

- https://github.com/D4Vinci/Scrapling

## 현재 운영 경로

```text
Slack live message
→ pre_gateway_dispatch hook
→ source_lab_analyze_url
→ PostgreSQL source/intake/artifact/analysis/branch 저장
→ branch decision
→ source report Markdown projection
→ judgment queue Markdown projection
→ OtterWiki public rendering
```

## 핵심 구현

- Plugin: `/path/to/webpage-sorter`
- Collector profile plugin link: `~/.hermes/profiles/collector/plugins/webpage_sorter`
- Hook: `pre_gateway_dispatch`
- Gate env:
  - `SOURCELAB_SLACK_AUTO_INTAKE=true`
  - `SOURCELAB_SLACK_AUTO_CHANNELS=C0123456789`
  - `SOURCELAB_SLACK_CHANNEL_NAME=sourcelab`
- Wiki base: `https://example.com`
- Wiki repo: `/path/to/wiki-or-markdown-repo`

## 검증 로그 요약

Scrapling 투입 후 gateway에서 다음 hook 로그를 확인했다.

```text
pre_gateway_dispatch skip: reason=source_lab_slack_auto_intake platform=slack chat=C0123456789
```

이는 Slack 메시지가 일반 LLM agent turn으로 가지 않고 SourceLab deterministic route로 처리됐다는 뜻이다.

## DB 검증

```text
found True
source_id c9a92260-3116-4de1-af68-ccb1adc445a3
canonical_id github:D4Vinci/Scrapling
canonical_url https://github.com/D4Vinci/Scrapling
latest_branch_state self_close
open_judgment_request_id None
projection_status committed
projection_path sourcelab/sources/github/d4vinci-scrapling.md
projection_commit 30fe5e8
```

Scrapling은 `self_close`로 분류되어 judgment queue에는 들어가지 않았다.

## Wiki 검증

Source report:

- https://example.com/sourcelab/sources/github/d4vinci-scrapling

HTTP header:

```text
HTTP/2 200
x-robots-tag: noindex, nofollow, noarchive
```

Queue page:

- https://example.com/sourcelab/queue/judgmentrequested

Queue page는 재생성됐고, Scrapling은 self-close라 표시되지 않는다. 기존 WeKnora pending 1건은 queue에 남아 있다.

## 관련 wiki commits

```text
30fe5e8 render sourcelab source github:D4Vinci/Scrapling
e2f5ad7 render sourcelab judgment queue
58d165b document sourcelab slack auto intake trigger
```

## 테스트

Hook unit test:

```text
tests/test_slack_auto_intake.py
3 passed
```

Live PostgreSQL 포함 검증 이력:

```text
47 passed
```

## 남은 정리

- SourceLab plugin 코드 자체는 현재 `~/.hermes/plugins/source_lab`에 있고 별도 git repository가 아니다.
- OtterWiki 문서와 projection 결과는 local git repository에 commit되어 있다.
- 외부 GitHub/Forgejo remote에 올리려면 먼저 canonical repository를 정해야 한다.
