"""멈춘 세션을 실제로 이어서 실행한다.

`claude --resume <세션id>` 로 원래 맥락(진행 중이던 할 일 목록·파일 상태)을 그대로 이어간다.
세션을 못 찾는 등 구조적으로 실패했을 때만 마지막 대화를 요약해 새로 시작한다.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from afterlimit.config import Config
from afterlimit.limits import LIMIT_PATTERNS
from afterlimit.sessions import BlockedSession

__all__ = ["ResumeResult", "resume"]

#: 이 정도 글자를 냈으면 뭔가 한 것으로 본다. 한도 안내 문구 자체는 150자 안팎이라
#: 그보다 넉넉히 위에 둔다.
MIN_WORK_CHARS = 200


@dataclass(frozen=True)
class ResumeResult:
    ok: bool
    #: 이어가기(--resume)로 됐는지, 새로 시작(fallback)했는지
    fallback: bool
    output: str
    error: str
    elapsed_sec: float

    @property
    def limit_mentioned(self) -> bool:
        """출력 어딘가에 한도 문구가 있다. 이것만으론 실패인지 알 수 없다."""
        blob = f"{self.output}\n{self.error}".lower()
        return any(p in blob for p in LIMIT_PATTERNS)

    @property
    def work_chars(self) -> int:
        """한도 안내 줄을 뺀 실제 산출 분량. 진척 여부는 이걸로 잰다."""
        keep = [
            ln for ln in self.output.splitlines()
            if not any(p in ln.lower() for p in LIMIT_PATTERNS)
        ]
        return len("\n".join(keep).strip())

    @property
    def hit_limit_again(self) -> bool:
        """재개했는데 **아무것도 못 하고** 한도에 걸렸나.

        ⚠️ 한도 문구가 있다고 곧바로 실패가 아니다. 한참 일하다 마지막에 걸린 것도
        같은 문구를 남긴다 — 그건 진척이지 실패가 아니다.

        2026-08-08 실측: news-auto 세션 하나가 559초 동안 블로그 초안 5,576자를
        만들고 커밋(1e22b4c3)까지 했는데 '한도 걸림'으로 기록됐다. 그렇게 쌓인
        가짜 실패로 세션 145개가 최대 6시간 백오프에 갇혀 실제로 재개가 멎었다.
        """
        return self.limit_mentioned and self.work_chars < MIN_WORK_CHARS


def _run(cmd: list[str], cwd: str, timeout: int) -> tuple[int, str, str, float]:
    started = time.monotonic()
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return p.returncode, p.stdout, p.stderr, time.monotonic() - started
    except subprocess.TimeoutExpired:
        return 124, "", f"{timeout}초 안에 끝나지 않았습니다", time.monotonic() - started
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} 를 찾을 수 없습니다", time.monotonic() - started


def resume(session: BlockedSession, cfg: Config) -> ResumeResult:
    """세션 하나를 이어서 실행한다. dry_run 이면 아무것도 하지 않는다."""
    if cfg.dry_run:
        return ResumeResult(True, False, f"[dry-run] {session.session_id} 재개 예정", "", 0.0)

    flags = ["--output-format", "text", "--max-turns", "60", "--dangerously-skip-permissions"]

    rc, out, err, elapsed = _run(
        [cfg.claude_bin, "--resume", session.session_id, "-p", cfg.resume_prompt, *flags],
        session.cwd,
        cfg.invoke_timeout_sec,
    )
    result = ResumeResult(rc == 0, False, out, err, elapsed)

    # 새로 시작해볼 두 경우.
    #  ① 구조적 실패 — 세션을 못 찾는 등. 이어갈 방법이 아예 없다.
    #  ② 아무것도 못 하고 즉시 한도에 튕김 — 세션이 너무 커서 맥락을 통째로 싣는
    #     것만으로 한도를 넘는 경우다. 2026-08-08 실측: 이어가기에 성공한 세션은
    #     14~278줄인데(11건), 9,499줄짜리는 매번 7초 만에 115자(한도 메시지)만
    #     내고 튕겼다. 278줄보다 큰 세션이 성공한 적은 한 번도 없다.
    #     이럴 땐 마지막 대화만 요약해 새로 시작하는 쪽이 훨씬 싸고, 실제로 이어진다.
    structural_fail = rc != 0 and not out.strip() and not result.hit_limit_again
    bounced_instantly = result.hit_limit_again and result.work_chars < MIN_WORK_CHARS
    if not (structural_fail or bounced_instantly):
        return result

    context = ""
    if session.last_user or session.last_assistant:
        context = (
            "[Context from the interrupted session]\n"
            f"Last user request: {session.last_user}\n\n"
            f"Last assistant reply: {session.last_assistant}\n\n"
            "[Instruction]\n"
        )
    rc, out, err, elapsed2 = _run(
        [cfg.claude_bin, "-p", context + cfg.resume_prompt, *flags],
        session.cwd,
        cfg.invoke_timeout_sec,
    )
    retry = ResumeResult(rc == 0, True, out, err, elapsed + elapsed2)
    # 새로 시작해도 즉시 튕기면 백오프가 걸리게 실패로 돌려준다.
    # ⚠️ 원래 결과로 통째로 덮으면 "폴백을 시도했다"는 사실이 로그에서 사라진다.
    #    2026-08-08 그 때문에 폴백이 도는지 안 도는지 눈으로 확인할 수 없었다.
    #    fallback=True 를 남겨 시도 사실을 보존한다.
    if bounced_instantly and retry.hit_limit_again and retry.work_chars < MIN_WORK_CHARS:
        return ResumeResult(False, True, result.output, err or result.error, elapsed + elapsed2)
    return retry
