import React from "react";
import { RoomStateSnapshot } from "../types/draft";
import { PickClock } from "./PickClock";

interface CommissionerBarProps {
  room: RoomStateSnapshot;
  busy: boolean;
  showBoard: boolean;
  onToggleBoard: () => void;
  onPauseResume: () => void;
  onResetClock: () => void;
  onAutoPickNow: () => void;
  onUndo: () => void;
  onExitRoom: () => void;
  onClockExpired: (expectedOverallPick: number) => void;
}

export const CommissionerBar: React.FC<CommissionerBarProps> = ({
  room,
  busy,
  showBoard,
  onToggleBoard,
  onPauseResume,
  onResetClock,
  onAutoPickNow,
  onUndo,
  onExitRoom,
  onClockExpired,
}) => {
  const slot = room.current_slot;

  return (
    <header className="comm-bar">
      <div className="comm-left">
        <button className="btn btn-ghost" onClick={onExitRoom}>← Salas</button>
        <div>
          <div className="room-title">{room.room_name} <span className="tag-edition">{room.edition.toUpperCase()}</span></div>
          <div className="room-meta">
            Pick {room.picks_history.length}/{room.total_picks} • Hash Sorteio: {room.lottery_receipt.verification_hash.slice(0, 10)}
          </div>
        </div>
      </div>

      <div className="comm-center">
        {slot ? (
          <>
            <PickClock
              pickClockSeconds={room.pick_clock_seconds}
              turnDurationSeconds={room.turn_duration_seconds}
              pausedRemainingSeconds={room.paused_remaining_seconds}
              turnStartedAtIso={room.turn_started_at_iso}
              status={room.status}
              currentOverallPick={slot.overall_pick}
              onClockExpired={onClockExpired}
            />
            <div className="on-the-clock-banner">
              <span className="otc-label">VEZ DE ESCOLHER (R{slot.round_number}.{String(slot.pick_in_round).padStart(2, "0")} • #{slot.overall_pick})</span>
              <strong className="otc-user">{slot.participant_id}</strong>
            </div>
            <div className="upcoming-strip">
              {room.upcoming_slots.slice(1, 5).map((up) => (
                <div key={up.overall_pick} className="upcoming-chip">
                  <span>#{up.overall_pick}</span> {up.participant_id}
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="draft-done-pill">🏆 DRAFT CONCLUÍDO — PRONTO PARA EXPORTAR (FASE 5)</div>
        )}
      </div>

      <div className="comm-actions">
        <button className="btn btn-secondary" onClick={onToggleBoard}>
          {showBoard ? "Ocultar Quadro" : "Mostrar Quadro"}
        </button>
        {room.status !== "completed" && (
          <>
            <button className="btn btn-warning" disabled={busy} onClick={onPauseResume}>
              {room.status === "paused" ? "▶ Retomar" : "⏸ Pausar"}
            </button>
            {room.pick_clock_seconds > 0 && (
              <button className="btn btn-ghost" disabled={busy} onClick={onResetClock}>
                ↺ Relógio
              </button>
            )}
            <button className="btn btn-accent" disabled={busy || room.status !== "active"} onClick={onAutoPickNow}>
              ⚡ Auto-Pick
            </button>
          </>
        )}
        <button className="btn btn-danger" disabled={busy || room.picks_history.length === 0} onClick={onUndo}>
          ↶ Desfazer
        </button>
      </div>
    </header>
  );
};