import { useEffect, useState } from "react";

type CountdownResult = {
  minutes: number;
  seconds: number;
  label: string;
  isWarning: boolean;
};

export function useCountdown(
  startedAt: number | null | undefined,
  durationMinutes: number,
  status: string,
  finishedAt?: number | null,
): CountdownResult {
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    if (status === "finished" || finishedAt || !startedAt) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [finishedAt, status, startedAt]);

  if (status === "finished" || finishedAt) {
    return { minutes: 0, seconds: 0, label: "Finished", isWarning: false };
  }
  if (!startedAt || durationMinutes <= 0) {
    return { minutes: 0, seconds: 0, label: "Not started", isWarning: false };
  }

  const deadline = startedAt + durationMinutes * 60_000;
  const remainingMs = Math.max(0, deadline - now);
  const minutes = Math.floor(remainingMs / 60_000);
  const seconds = Math.floor((remainingMs % 60_000) / 1000);
  const label = remainingMs === 0 ? "Time up" : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  const isWarning = remainingMs > 0 && remainingMs < 5 * 60_000;

  return { minutes, seconds, label, isWarning };
}
