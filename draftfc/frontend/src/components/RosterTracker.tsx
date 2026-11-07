import React, { useState } from "react";
import { PitchSector, RoomStateSnapshot } from "../types/draft";

interface RosterTrackerProps {
  room: RoomStateSnapshot;
  selectedParticipant: string;
  followTurn: boolean;
  onSelectParticipant: (uid: string) => void;
  onToggleFollowTurn: (val: boolean) => void;
  onToggleAutoPick: (uid: string, enabled: boolean) => void;
  onRemoveQueue: (uid: string, playerId: number) => void;
  onMoveQueue: (uid: string, index: number, direction: -1 | 1) => void;
}

const SECTOR_ORDER: PitchSector[] = ["GK", "DEF", "MID", "FWD"];

export const RosterTracker: React.FC<RosterTrackerProps> = ({
  room,
  selectedParticipant,
  followTurn,
  onSelectParticipant,
  onToggleFollowTurn,
  onToggleAutoPick,
  onRemoveQueue,
  onMoveQueue,
}) => {
  const [tab, setTab] = useState<"roster" | "queue">("roster");
  const summary = room.rosters[selectedParticipant];
  if (!summary) return null;

  return (
    <aside className="panel roster-panel">
      <div className="roster-header">
        <select
          className="participant-select"
          value={selectedParticipant}
          onChange={(e) => {
            onToggleFollowTurn(false);
            onSelectParticipant(e.target.value);
          }}
        >
          {room.ordered_participants.map((uid) => (
            <option key={uid} value={uid}>
              {uid} ({room.rosters[uid]?.players.length ?? 0}/{room.rules.roster_size})
            </option>
          ))}
        </select>

        <label className="checkbox-inline">
          <input
            type="checkbox"
            checked={followTurn}
            onChange={(e) => onToggleFollowTurn(e.target.checked)}
          />
          Seguir turno
        </label>
      </div>

      <div className="afk-bar">
        <span>Modo Auto-Pick (AFK) para <strong>{selectedParticipant}</strong>:</span>
        <input
          type="checkbox"
          checked={summary.auto_pick_enabled}
          onChange={(e) => onToggleAutoPick(selectedParticipant, e.target.checked)}
        />
      </div>

      <div className="sector-quotas">
        {SECTOR_ORDER.map((sec) => {
          const cur = summary.sector_counts[sec] ?? 0;
          const req = room.rules.min_by_sector[sec] ?? 0;
          const ok = cur >= req;
          return (
            <div key={sec} className={`quota-chip ${ok ? "quota-ok" : "quota-pending"}`}>
              <strong>{sec}</strong> {cur}/{req} mín
            </div>
          );
        })}
      </div>

      <div className="panel-tabs">
        <button className={tab === "roster" ? "tab-active" : ""} onClick={() => setTab("roster")}>
          Elenco ({summary.players.length}/{room.rules.roster_size})
        </button>
        <button className={tab === "queue" ? "tab-active" : ""} onClick={() => setTab("queue")}>
          Fila de Prioridade ({summary.queue.length})
        </button>
      </div>

      {tab === "roster" ? (
        <div className="roster-list">
          {summary.players.length === 0 ? (
            <div className="empty-hint">Nenhum atleta escolhido ainda.</div>
          ) : (
            summary.players.map((p, idx) => (
              <div key={p.player_id} className="mini-card">
                <span className={`pos-tag sector-${p.sector.toLowerCase()}`}>{p.primary_position}</span>
                <div className="mini-info">
                  <strong>#{idx + 1} {p.short_name}</strong>
                  <span>{p.club_name} • {p.league_name}</span>
                </div>
                <strong className="mini-ovr">{p.overall}</strong>
              </div>
            ))
          )}
        </div>
      ) : (
        <div className="roster-list">
          {summary.queue.length === 0 ? (
            <div className="empty-hint">Use "+ Fila" na lista ao lado para preparar escolhas.</div>
          ) : (
            summary.queue.map((qItem, idx) => (
              <div key={qItem.player_id} className={`mini-card ${!qItem.eligible_now ? "row-blocked" : ""}`}>
                <span className="queue-rank">{qItem.priority}º</span>
                <div className="mini-info">
                  <strong>{qItem.short_name} ({qItem.overall})</strong>
                  <span>{qItem.primary_position} • {qItem.club_name}</span>
                  {!qItem.eligible_now && qItem.violations[0] && (
                    <small className="text-danger">⚠ {qItem.violations[0].message}</small>
                  )}
                </div>
                <div className="queue-btns">
                  <button onClick={() => onMoveQueue(selectedParticipant, idx, -1)} disabled={idx === 0}>↑</button>
                  <button onClick={() => onMoveQueue(selectedParticipant, idx, 1)} disabled={idx === summary.queue.length - 1}>↓</button>
                  <button onClick={() => onRemoveQueue(selectedParticipant, qItem.player_id)}>✕</button>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </aside>
  );
};