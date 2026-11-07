import React, { useEffect, useRef, useState } from "react";
import { DraftStatus } from "../types/draft";

interface PickClockProps {
  pickClockSeconds: number;
  turnDurationSeconds: number;
  pausedRemainingSeconds: number | null;
  turnStartedAtIso: string;
  status: DraftStatus;
  currentOverallPick: number | null;
  onClockExpired: (expectedOverallPick: number) => void;
}

export const PickClock: React.FC<PickClockProps> = ({
  pickClockSeconds,
  turnDurationSeconds,
  pausedRemainingSeconds,
  turnStartedAtIso,
  status,
  currentOverallPick,
  onClockExpired,
}) => {
  const firedPickRef = useRef<number | null>(null);
  const [remaining, setRemaining] = useState<number>(turnDurationSeconds);

  useEffect(() => {
    if (pickClockSeconds <= 0 || currentOverallPick === null) return;

    if (status === "paused") {
      setRemaining(pausedRemainingSeconds ?? turnDurationSeconds);
      return;
    }
    if (status !== "active") return;

    const computeLeft = () => {
      const startedMs = Date.parse(turnStartedAtIso);
      const elapsedSec = Math.max(0, (Date.now() - startedMs) / 1000);
      return Math.max(0, turnDurationSeconds - elapsedSec);
    };

    setRemaining(computeLeft());
    const id = window.setInterval(() => {
      const left = computeLeft();
      setRemaining(left);
      if (left <= 0 && firedPickRef.current !== currentOverallPick) {
        firedPickRef.current = currentOverallPick;
        onClockExpired(currentOverallPick);
      }
    }, 200);

    return () => window.clearInterval(id);
  }, [
    pickClockSeconds,
    turnDurationSeconds,
    pausedRemainingSeconds,
    turnStartedAtIso,
    status,
    currentOverallPick,
    onClockExpired,
  ]);

  if (pickClockSeconds <= 0) {
    return <div className="clock-box clock-unlimited">∞ SEM RELÓGIO</div>;
  }

  const totalSec = Math.ceil(remaining);
  const mins = String(Math.floor(totalSec / 60)).padStart(2, "0");
  const secs = String(totalSec % 60).padStart(2, "0");
  const isDanger = status === "active" && totalSec <= 10;

  return (
    <div className={`clock-box ${isDanger ? "clock-danger" : ""} ${status === "paused" ? "clock-paused" : ""}`}>
      <span className="clock-time">{mins}:{secs}</span>
      <span className="clock-sub">{status === "paused" ? "PAUSADO" : "NO RELÓGIO"}</span>
    </div>
  );
};