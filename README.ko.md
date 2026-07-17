# AfterLimit

**AI 코딩 에이전트가 사용량 한도로 멈췄습니다. AfterLimit은 한도가 풀리는 그 순간 작업을 이어받습니다 — 당신이 자는 동안에도, 점심을 먹으러 나간 사이에도, 주말 내내 자리를 비워도.**

[English](README.md) · **한국어** · [中文](README.zh.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

[![CI](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml/badge.svg)](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-black)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

```
You've hit your usage limit · resets 11pm
```

익숙한 화면입니다. 오후 2시에 작업이 멈추고, 한도는 저녁 7시에 풀리고, 그 다섯 시간은 그냥 사라집니다 — 마침 7시에 터미널 앞에 앉아 "continue"를 칠 게 아니라면.

AfterLimit은 그 공백을 메웁니다. 작은 백그라운드 작업이 한도 해제를 감지해 멈춰 있던 세션을 자동으로 이어갑니다. 돌아오면 멈춘 프롬프트가 아니라 끝난 작업이 기다립니다.

```console
$ afterlimit scan
멈춘 세션 2개  (현재 14:32)

  [재개 가능]  my-api/8147d7ca   usage   해제 14:00   ← 한도가 이미 풀림
  [대기]       docs-site/32d57b  usage   해제 19:50   ← 5시간 18분 남음
```

## 무엇이 다른가

**감시에는 토큰이 들지 않습니다.** AfterLimit은 로컬 세션 로그를 읽을 뿐, 당신을 확인하려고 모델을 호출하지 않습니다. 토큰은 실제로 작업이 재개되는 순간에만 씁니다. 5분마다 들여다보는 것 자체는 공짜입니다.

**한도를 편법으로 뚫지 않습니다.** AfterLimit은 API가 알려준 *진짜* 해제 시각을 기다렸다가 그 뒤에 재개합니다. 우회하거나, 속이거나, 엔드포인트를 두드려대지 않습니다. 아직 안 풀렸으면 물러났다가 다시 봅니다.

**새 프롬프트가 아니라 맥락을 이어갑니다.** `claude --resume <세션>`으로 실행하므로, 진행 중이던 할 일 목록과 파일 상태를 그대로 유지한 채 이어갑니다 — 뭘 하던 중이었는지 잊은 콜드 스타트가 아닙니다.

**어디서든 시간대가 정확합니다.** Claude가 보여주는 해제 시각("resets 11pm")에는 시간대가 없습니다 — 당신의 로컬 벽시계 시각일 뿐입니다. AfterLimit은 이 값을 기기의 시간대에 맞춰 해석하므로, 서울에서든 뉴욕에서든 베를린에서든 해제 시각이 올바르게 읽힙니다. (순진한 구현에서 실제로 나는 버그입니다: 시간대를 하나로 박아두면 그 지역 밖 사용자는 전부 틀린 시각에 재개됩니다.)

## 설치

```bash
git clone https://github.com/dawith-ai/afterlimit
cd afterlimit
./install.sh
```

설치 스크립트가 OS를 감지해 5분마다 도는 백그라운드 작업을 등록합니다:

- **macOS** → `launchd` LaunchAgent
- **Linux** → `systemd --user` 타이머 (systemd가 없으면 `cron` 한 줄로 폴백)

그다음 세션이 잘 잡히는지 확인하세요:

```bash
afterlimit scan     # 무엇이 막혀 있고 언제 풀리는지 — 아무것도 실행하지 않음
afterlimit config   # 어디를 보는지, 시간대, 알림 설정
```

요구 사항: Python 3.11 이상, `claude` CLI가 `PATH`에 있을 것. **런타임 의존성 0** — 표준 라이브러리만 씁니다.

## 동작 방식

```
5분마다 ──► ~/.claude/projects/*.jsonl 스캔
              │
              ├─ 마지막 메시지가 한도 에러인가?          ── 아니오 ─► 건너뜀
              ├─ 해제 시각을 파싱했고 이미 지났는가?     ── 아니오 ─► 대기
              ├─ 쿨다운 안에 이미 재개했는가?            ── 예 ─► 건너뜀
              │
              └─► claude --resume <세션>  ──►  알림 (웹훅, 선택)
```

모든 안전장치는 심사위원이 반드시 물을 질문 하나에 답하기 위해 있습니다 — *"이거 그냥 모델을 두드려대는 거 아니냐?"* 아닙니다:

| 안전장치 | 막는 것 |
|---|---|
| 마지막 메시지가 API 에러여야 함 | 이미 진행된 세션을 다시 재개하는 것 |
| 해제 시각이 지나야 함 | 한도가 풀리기도 전에 두드리는 것 |
| 사이클당 1회 재개 (설정 가능) | 막힌 세션들이 한꺼번에 몰려 실행되는 것 |
| 세션당 5시간 쿨다운 | 같은 세션을 반복해서 재개하는 것 |
| 단일 인스턴스 잠금 | 스케줄러가 겹쳐 실행돼 이중 재개하는 것 |
| 세션 나이 제한 (3일) | 죽은 지 오래된 백로그를 되살려 토큰을 태우는 것 |

## 설정

기본값만으로 동작합니다. 바꾸고 싶으면 `~/.config/afterlimit/config.json`에 넣으세요:

```json
{
  "max_resume_per_cycle": 1,
  "resume_cooldown_hours": 5,
  "max_session_age_days": 3,
  "resume_prompt": "진행 중이던 작업을 이어서 하세요...",
  "webhook_url": "https://hooks.slack.com/services/..."
}
```

알림은 JSON을 받는 웹훅이면 어디로든 갑니다 — Slack, Discord, 또는 직접 만든 엔드포인트(페이로드 형식은 URL을 보고 고릅니다). 웹훅이 없으면 알림도 없고, 그 외에는 아무것도 바뀌지 않습니다. 환경변수 `AFTERLIMIT_WEBHOOK_URL`로도 설정할 수 있습니다.

실행 전에 미리 보기:

```bash
afterlimit --dry-run run    # 무엇을 재개할지만 보여주고 아무것도 실행하지 않음
```

## 제거

```bash
./install.sh --uninstall
```

백그라운드 작업과 CLI를 제거합니다. 상태 파일은 지울 때까지 `~/.local/state/afterlimit`에 남습니다.

## 범위와 로드맵

AfterLimit은 **헤드리스** 세션을 재개합니다 — 에이전트가 켜져 있을 필요가 없습니다. 로그를 읽어 작업을 이어갑니다. 그래서 에디터·터미널을 가리지 않습니다: 일반 터미널에서 Claude Code를 쓰든, VS Code에서 쓰든, 어디서든 동작합니다.

아직 다루지 않는 것, 정직하게 밝힙니다:

- **대화형 TUI 재개** — *실행 중인* tmux 창 안에서 대화 도중 멈췄을 때 "continue"를 눌러주는 것. 이전 프로토타입이 이걸 했지만 tmux 전용이고 취약해서, 반쪽짜리로 내보내는 대신 향후 선택형 모드로 남겨두었습니다.
- **다른 에이전트** — 지금은 세션 로그 형식이 Claude Code 기준입니다. 한도 파싱 코어 자체는 에이전트에 종속되지 않으므로, 다른 CLI용 어댑터 기여를 환영합니다.
- **Windows** — 스케줄러 배선은 macOS/Linux용입니다. Python 코어는 이식 가능합니다.

## 설계 노트

- **런타임 의존성 0.** 표준 라이브러리만 씁니다 — 검증할 의존성도, 설치 시 깨질 것도, 라이선스 충돌도 없습니다.
- **순수 코어, 테스트됨.** 한도 파싱과 세션 스캔은 I/O 없는 순수 함수이며, 서울·뉴욕·UTC·베를린을 교차 검증해 시간대 로직이 조용히 회귀하지 못하게 합니다. `pytest -q`.
- **코어는 한 가지만 합니다.** 해제를 감지하고, 한 번 재개하고, 비켜섭니다.

## 라이선스

[MIT](LICENSE). Anthropic과 제휴하거나 승인받지 않았습니다.
