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


@dataclass(frozen=True)
class ResumeResult:
    ok: bool
    #: 이어가기(--resume)로 됐는지, 새로 시작(fallback)했는지
    fallback: bool
    output: str
    error: str
    elapsed_sec: float

    @property
    def hit_limit_again(self) -> bool:
        """재개했는데 또 한도에 걸렸다 — 아직 안 풀린 것이다."""
        blob = f"{self.output}\n{self.error}".lower()
        return any(p in blob for p in LIMIT_PATTERNS)


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

    # 또 한도에 걸렸거나 정상 응답이면 그대로 둔다. 구조적 실패일 때만 새로 시작한다.
    structural_fail = rc != 0 and not out.strip() and not result.hit_limit_again
    if not structural_fail:
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
    return ResumeResult(rc == 0, True, out, err, elapsed + elapsed2)
