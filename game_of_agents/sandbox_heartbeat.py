from __future__ import annotations

import os
import signal
import sys
import time
from contextlib import contextmanager

from game_of_agents.convex_runtime import ConvexRuntimeClient
from game_of_agents.settings import settings


class HeartbeatTimeout(RuntimeError):
    pass


@contextmanager
def _timeout(seconds: float):
    if seconds <= 0 or not hasattr(signal, "setitimer"):
        yield
        return

    def _handle_timeout(_: int, __) -> None:
        raise HeartbeatTimeout(f"heartbeat call exceeded {seconds:.1f}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    signal.signal(signal.SIGALRM, _handle_timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _parent_alive(parent_pid: int) -> bool:
    try:
        os.kill(parent_pid, 0)
    except OSError:
        return False
    return True


def _runtime() -> ConvexRuntimeClient:
    if not settings.convex_url:
        raise RuntimeError("CONVEX_URL is required")
    return ConvexRuntimeClient(
        settings.convex_url,
        site_url=settings.convex_site_url,
        auth_token=settings.convex_sync_token,
    )


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit("usage: sandbox_heartbeat <run_id> <sandbox_id> <role> <poll_seconds> <ttl_seconds>")
    _, run_id, sandbox_id, _role, poll_seconds_raw, ttl_seconds_raw = sys.argv
    poll_seconds = max(1.0, float(poll_seconds_raw))
    ttl_seconds = max(30, int(float(ttl_seconds_raw)))
    parent_pid = os.getppid()
    while _parent_alive(parent_pid):
        runtime = _runtime()
        try:
            with _timeout(min(5.0, poll_seconds)):
                runtime.heartbeat_sandbox(
                    run_id,
                    sandbox_id=sandbox_id,
                    status="running",
                    heartbeat_ttl_seconds=ttl_seconds,
                )
        except Exception:
            pass
        time.sleep(poll_seconds)
    try:
        runtime = _runtime()
        runtime.finish_sandbox(
            run_id,
            sandbox_id=sandbox_id,
            status="failed",
            error="heartbeat helper observed parent exit before sandbox finalization",
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
