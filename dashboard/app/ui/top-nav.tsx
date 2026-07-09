"use client";

import { useCountdown } from "./use-countdown";

export function TopNav({
  runName,
  status,
  startedAt,
  finishedAt,
  durationMinutes,
  onNewRun,
  onToggleChat,
  chatVisible,
  onCopyLink,
  copyLinkStatus,
}: {
  runName?: string;
  status?: string;
  startedAt?: number | null;
  finishedAt?: number | null;
  durationMinutes?: number;
  onNewRun: () => void;
  onToggleChat?: () => void;
  chatVisible?: boolean;
  onCopyLink?: () => void;
  copyLinkStatus?: "idle" | "copied" | "error";
}) {
  const countdown = useCountdown(
    startedAt ?? null,
    durationMinutes ?? 0,
    status ?? "pending",
    finishedAt ?? null,
  );

  const badgeClass =
    status === "running"
      ? "live-badge live-badge--running"
      : status === "finished"
        ? "live-badge live-badge--finished"
        : status === "stopping"
          ? "live-badge live-badge--pending"
          : status === "failed"
            ? "live-badge live-badge--pending"
        : "live-badge live-badge--pending";

  const badgeLabel =
    status === "running"
      ? "LIVE"
      : status === "stopping"
        ? "STOPPING"
        : status === "finished"
          ? "ENDED"
          : status === "failed"
            ? "FAILED"
            : "IDLE";

  return (
    <nav className="top-nav">
      <div className="top-nav__left">
        <span className="top-nav__brand">Game of Agents</span>
        {runName && <span className="top-nav__run-name">{runName}</span>}
      </div>
      <div className="top-nav__right">
        {status && (
          <>
            <span className={badgeClass}>
              <span className="live-badge__dot" />
              {badgeLabel}
            </span>
            <span
              className="top-nav__timer"
              data-warning={String(countdown.isWarning)}
            >
              {countdown.label}
            </span>
          </>
        )}
        {onToggleChat && (
          <button
            type="button"
            className="top-nav__chat-toggle"
            onClick={onToggleChat}
          >
            {chatVisible ? "Hide Chat" : "Show Chat"}
          </button>
        )}
        {onCopyLink && (
          <button type="button" className="btn btn--sm" onClick={onCopyLink}>
            {copyLinkStatus === "copied"
              ? "Link Copied"
              : copyLinkStatus === "error"
                ? "Copy Failed"
                : "Copy Link"}
          </button>
        )}
        <button type="button" className="btn btn--accent btn--sm" onClick={onNewRun}>
          + New Run
        </button>
      </div>
    </nav>
  );
}
