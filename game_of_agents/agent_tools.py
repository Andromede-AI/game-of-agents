from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
from typing import Optional

import typer

from game_of_agents.convex_runtime import ConvexRuntimeClient
from game_of_agents.settings import settings


app = typer.Typer(no_args_is_help=True, add_completion=False)
RUNTIME_CALL_TIMEOUT_SECONDS = float(os.environ.get("GOA_TOOL_TIMEOUT_SECONDS", "20"))


class RuntimeCallTimeout(RuntimeError):
    pass


@contextmanager
def _runtime_timeout(seconds: float):
    if seconds <= 0 or not hasattr(signal, "setitimer"):
        yield
        return

    def _handle_timeout(_: int, __) -> None:
        raise RuntimeCallTimeout(f"tool call exceeded {seconds:.0f}s")

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


def _call_runtime(func, *args, **kwargs):
    try:
        with _runtime_timeout(RUNTIME_CALL_TIMEOUT_SECONDS):
            return func(*args, **kwargs)
    except RuntimeCallTimeout as exc:
        raise typer.BadParameter(str(exc)) from exc


def _runtime() -> ConvexRuntimeClient:
    if not settings.convex_url:
        raise typer.BadParameter("CONVEX_URL is required")
    return ConvexRuntimeClient(
        settings.convex_url,
        site_url=settings.convex_site_url,
        auth_token=settings.convex_sync_token,
    )


def _run_id() -> str:
    import os

    run_id = os.environ.get("GOA_RUN_ID")
    if not run_id:
        raise typer.BadParameter("GOA_RUN_ID is not set")
    return run_id


def _agent_id() -> str:
    import os

    agent_id = os.environ.get("GOA_AGENT_ID")
    if not agent_id:
        raise typer.BadParameter("GOA_AGENT_ID is not set")
    return agent_id


def _run_summary(runtime: ConvexRuntimeClient, run_id: str) -> dict[str, object]:
    summary = _call_runtime(runtime.get_run_summary, run_id)
    if summary is None:
        raise typer.BadParameter("run not found")
    return summary


def _agent_analytics(runtime: ConvexRuntimeClient, run_id: str, agent_id: str) -> dict[str, object]:
    analytics = _call_runtime(runtime.get_agent_analytics, run_id, agent_id)
    if analytics is None:
        raise typer.BadParameter("run not found")
    return analytics


def _run_config(runtime: ConvexRuntimeClient, run_id: str) -> dict[str, object]:
    summary = _run_summary(runtime, run_id)
    config = summary.get("config")
    if not isinstance(config, dict):
        raise typer.BadParameter("run config unavailable")
    return config


def _require_marketplace_enabled(runtime: ConvexRuntimeClient, run_id: str) -> None:
    config = _run_config(runtime, run_id)
    if not bool(config.get("marketplace_enabled", True)):
        raise typer.BadParameter("marketplace is disabled for this run")


def _require_chat_enabled(runtime: ConvexRuntimeClient, run_id: str) -> None:
    config = _run_config(runtime, run_id)
    if not bool(config.get("chat_enabled", True)):
        raise typer.BadParameter("chat is disabled for this run")


def _synthesized_offer_metadata(
    analytics: dict[str, object],
    *,
    agent_id: str,
    title: str,
    file_paths: list[str],
    description: str,
    evidence: str,
) -> tuple[str, str]:
    description_text = description.strip()
    evidence_text = evidence.strip()
    file_list = ", ".join(file_paths)
    if not description_text:
        description_text = f"{title}. Bundle includes: {file_list}."
    if not evidence_text:
        row = analytics.get("self") if isinstance(analytics, dict) else None
        if row is not None:
            rating = row.get("best_rating")
            best_bot_id = row.get("best_bot_id")
            rank = row.get("tournament_rank")
            projected_rank = row.get("projected_rank")
            projected_payout = row.get("projected_payout")
            equity_delta = row.get("equity_delta")
            total_agents = row.get("total_agents") or analytics.get("total_agents")
            rating_text = f"{float(rating):.2f}" if isinstance(rating, (int, float)) else "unknown"
            rank_text = f"#{rank}/{total_agents}" if rank is not None and total_agents is not None else "unknown"
            projected_text = (
                f"projected payout rank #{projected_rank}/{total_agents}, score {float(projected_payout):.2f}"
                if isinstance(projected_payout, (int, float)) and projected_rank is not None and total_agents is not None
                else "projected payout unknown"
            )
            delta_text = f"{float(equity_delta):+.2f}" if isinstance(equity_delta, (int, float)) else "unknown"
            evidence_text = (
                f"Seller tournament rank {rank_text}, best rating {rating_text}, "
                f"{projected_text}, equity delta {delta_text}, "
                f"best bot {best_bot_id or 'unknown'}. Included files: {file_list}."
            )
        else:
            evidence_text = f"Included files: {file_list}."
    return description_text, evidence_text


@app.command("submit-bot")
def submit_bot(
    name: str,
    entrypoint: str,
    module_path: str,
    description: str = "",
    path: list[str] = typer.Argument(default_factory=list),
) -> None:
    runtime = _runtime()
    run_id = _run_id()
    agent_id = _agent_id()
    root = Path.cwd().resolve()
    files = [root / item for item in (path or [module_path])]
    summary = _run_summary(runtime, run_id)
    config = summary["config"]
    bundle = _call_runtime(
        runtime.upload_bundle,
        files=files,
        root=root,
        max_total_bytes=int(config["artifact_bundle_max_bytes"]),
    )
    payload = _call_runtime(
        runtime.register_bot_submission,
        run_id,
        agent_id=agent_id,
        name=name,
        description=description,
        entrypoint=entrypoint,
        module_path=module_path,
        bundle_storage_id=bundle["storageId"],
        bundle_bytes=int(bundle["totalFileBytes"]),
        file_paths=list(bundle["filePaths"]),
    )
    typer.echo(json.dumps(payload))


@app.command("stats")
def stats() -> None:
    runtime = _runtime()
    payload = _agent_analytics(runtime, _run_id(), _agent_id())
    typer.echo(json.dumps(payload))


@app.command("leaderboard")
def leaderboard(limit: int = 12) -> None:
    runtime = _runtime()
    payload = _agent_analytics(runtime, _run_id(), _agent_id())
    leaderboard_rows = list(payload.get("leaderboard") or [])
    typer.echo(json.dumps(leaderboard_rows[: max(1, limit)]))


@app.command("games")
def games(limit: int = 20) -> None:
    runtime = _runtime()
    payload = _call_runtime(runtime.query_games, _run_id(), limit=limit)
    typer.echo(json.dumps(payload))


@app.command("query-games")
def query_games(
    limit: int = typer.Option(25, "--limit", min=1, max=200),
    scan_limit: int = typer.Option(128, "--scan-limit", min=1, max=1000),
    order: str = typer.Option("desc", "--order"),
    agent_id: Optional[str] = typer.Option(None, "--agent-id"),
    bot_id: Optional[str] = typer.Option(None, "--bot-id"),
    winner_bot_id: Optional[str] = typer.Option(None, "--winner-bot-id"),
    status: Optional[str] = typer.Option(None, "--status"),
    reason_contains: Optional[str] = typer.Option(None, "--reason-contains"),
) -> None:
    runtime = _runtime()
    normalized_order = order.strip().lower()
    if normalized_order not in {"asc", "desc"}:
        raise typer.BadParameter("order must be 'asc' or 'desc'")
    payload = _call_runtime(
        runtime.query_games,
        _run_id(),
        limit=limit,
        scan_limit=scan_limit,
        order=normalized_order,
        agent_id=agent_id,
        bot_id=bot_id,
        winner_bot_id=winner_bot_id,
        status=status,
        reason_contains=reason_contains,
    )
    typer.echo(json.dumps(payload))


@app.command("create-offer")
def create_offer(
    title: str,
    price_pct: float,
    description: str = typer.Option(
        "",
        "--description",
        help="What this bundle does and why a buyer should want it.",
    ),
    evidence: str = typer.Option(
        "",
        "--evidence",
        help="Concrete support for the sale, such as current rank, rating, or observed improvement.",
    ),
    path: list[str] = typer.Argument(default_factory=list),
) -> None:
    runtime = _runtime()
    run_id = _run_id()
    agent_id = _agent_id()
    _require_marketplace_enabled(runtime, run_id)
    if not path:
        raise typer.BadParameter("at least one file path is required")
    summary = _run_summary(runtime, run_id)
    analytics = _agent_analytics(runtime, run_id, agent_id)
    config = summary["config"]
    root = Path.cwd().resolve()
    normalized_paths = [str(Path(item)) for item in path]
    description_text, evidence_text = _synthesized_offer_metadata(
        analytics,
        agent_id=agent_id,
        title=title,
        file_paths=normalized_paths,
        description=description,
        evidence=evidence,
    )
    bundle = _call_runtime(
        runtime.upload_bundle,
        files=[root / item for item in path],
        root=root,
        max_total_bytes=int(config["artifact_bundle_max_bytes"]),
    )
    payload = _call_runtime(
        runtime.create_offer,
        run_id,
        seller_agent_id=agent_id,
        title=title,
        description=description_text,
        evidence=evidence_text,
        price_pct=price_pct,
        bundle_storage_id=bundle["storageId"],
        bundle_bytes=int(bundle["totalFileBytes"]),
        file_paths=list(bundle["filePaths"]),
    )
    typer.echo(json.dumps(payload))


@app.command("update-offer")
def update_offer(offer_id: str, price_pct: float) -> None:
    runtime = _runtime()
    run_id = _run_id()
    _require_marketplace_enabled(runtime, run_id)
    payload = _call_runtime(runtime.update_offer, run_id, offer_id=offer_id, price_pct=price_pct)
    typer.echo(json.dumps(payload))


@app.command("list-offers")
def list_offers(limit: int = 50) -> None:
    runtime = _runtime()
    run_id = _run_id()
    _require_marketplace_enabled(runtime, run_id)
    offers = _call_runtime(runtime.list_marketplace_offers, run_id, limit=limit)
    typer.echo(json.dumps(offers))


@app.command("show-offer")
def show_offer(offer_id: str) -> None:
    runtime = _runtime()
    run_id = _run_id()
    _require_marketplace_enabled(runtime, run_id)
    payload = _call_runtime(runtime.get_offer_details, run_id, offer_id)
    if payload is None:
        raise typer.BadParameter("offer not found")
    typer.echo(json.dumps(payload))


@app.command("buy-offer")
def buy_offer(offer_id: str) -> None:
    runtime = _runtime()
    run_id = _run_id()
    agent_id = _agent_id()
    _require_marketplace_enabled(runtime, run_id)
    payload = _call_runtime(runtime.purchase_offer, run_id, offer_id=offer_id, buyer_agent_id=agent_id)
    storage_id = payload.get("bundleStorageId") or payload.get("bundle_storage_id")
    if storage_id:
        destination = Path.cwd() / "marketplace" / offer_id
        _call_runtime(runtime.download_bundle, storage_id, destination)
    typer.echo(json.dumps(payload))


@app.command("review-offer")
def review_offer(offer_id: str, text: str) -> None:
    runtime = _runtime()
    run_id = _run_id()
    _require_marketplace_enabled(runtime, run_id)
    payload = _call_runtime(runtime.add_review, run_id, offer_id=offer_id, buyer_agent_id=_agent_id(), text=text)
    typer.echo(json.dumps(payload))


@app.command("read-chat")
def read_chat(limit: int = 40) -> None:
    runtime = _runtime()
    run_id = _run_id()
    _require_chat_enabled(runtime, run_id)
    payload = _call_runtime(runtime.list_recent_messages, run_id, limit=limit)
    typer.echo(json.dumps(payload))


@app.command("post-chat")
def post_chat(text: str, parent_message_id: Optional[str] = None) -> None:
    runtime = _runtime()
    run_id = _run_id()
    _require_chat_enabled(runtime, run_id)
    payload = _call_runtime(
        runtime.post_comment,
        run_id,
        author_agent_id=_agent_id(),
        commentator_id=f"{_agent_id()}-commentator",
        text=text,
        parent_message_id=parent_message_id,
    )
    typer.echo(json.dumps(payload))


@app.command("react-chat")
def react_chat(message_id: str, emoji: str) -> None:
    runtime = _runtime()
    run_id = _run_id()
    _require_chat_enabled(runtime, run_id)
    payload = _call_runtime(
        runtime.react_comment,
        run_id,
        author_agent_id=_agent_id(),
        message_id=message_id,
        emoji=emoji,
    )
    typer.echo(json.dumps(payload))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
