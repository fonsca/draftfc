import React, { useEffect, useState } from "react";
import { localAdapter } from "../adapters/localAdapter";
import {
  FilterOptionsResponse,
  PitchSector,
  PlayerPoolItem,
  RoomStateSnapshot,
} from "../types/draft";

interface PlayerPoolProps {
  room: RoomStateSnapshot;
  queueTargetUser: string;
  busy: boolean;
  onDraftPlayer: (playerId: number) => void;
  onQueuePlayer: (participantId: string, playerId: number) => void;
}

const SECTORS: Array<{ label: string; value: PitchSector | "" }> = [
  { label: "TODOS", value: "" },
  { label: "GK", value: "GK" },
  { label: "DEF", value: "DEF" },
  { label: "MID", value: "MID" },
  { label: "FWD", value: "FWD" },
];

export const PlayerPool: React.FC<PlayerPoolProps> = ({
  room,
  queueTargetUser,
  busy,
  onDraftPlayer,
  onQueuePlayer,
}) => {
  const [q, setQ] = useState("");
  const [sector, setSector] = useState<PitchSector | "">("");
  const [league, setLeague] = useState("");
  const [club, setClub] = useState("");
  const [onlyEligible, setOnlyEligible] = useState(false);
  const [options, setOptions] = useState<FilterOptionsResponse | null>(null);
  const [items, setItems] = useState<PlayerPoolItem[]>([]);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    localAdapter.getFilterOptions(room.room_id).then(setOptions).catch(() => null);
  }, [room.room_id]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      localAdapter
        .queryPlayers(room.room_id, {
          q,
          sector,
          league_name: league || undefined,
          club_name: club || undefined,
          only_eligible: onlyEligible,
          hide_picked: true,
          limit: 60,
        })
        .then((res) => {
          setItems(res.items);
          setTotal(res.total_matching);
        })
        .catch(() => null);
    }, 120);
    return () => window.clearTimeout(timer);
  }, [room.room_id, room.state_version, q, sector, league, club, onlyEligible]);

  const activeUser = room.current_slot?.participant_id;
  const canDraftNow = room.status === "active" && Boolean(activeUser) && !busy;

  return (
    <div className="panel player-pool-panel">
      <div className="pool-toolbar">
        <input
          type="text"
          className="input-search"
          placeholder="Buscar jogador, clube ou país (ex: Mbappe, Real Madrid, Brasil)..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="sector-pills">
          {SECTORS.map((s) => (
            <button
              key={s.label}
              className={`pill ${sector === s.value ? "pill-active" : ""}`}
              onClick={() => setSector(s.value)}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="pool-subfilters">
        <select value={league} onChange={(e) => { setLeague(e.target.value); setClub(""); }}>
          <option value="">Todas as Ligas</option>
          {options?.leagues.map((lg) => <option key={lg} value={lg}>{lg}</option>)}
        </select>

        <select value={club} onChange={(e) => setClub(e.target.value)}>
          <option value="">Todos os Clubes</option>
          {options?.clubs.map((cl) => <option key={cl} value={cl}>{cl}</option>)}
        </select>

        <label className="checkbox-inline">
          <input
            type="checkbox"
            checked={onlyEligible}
            onChange={(e) => setOnlyEligible(e.target.checked)}
          />
          Apenas elegíveis p/ {activeUser ?? "turno"}
        </label>
        <span className="pool-count">{total} disponíveis</span>
      </div>

      <div className="pool-list">
        {items.map(({ player, eligible_for_active, violations }) => (
          <div key={player.player_id} className={`player-row ${!eligible_for_active ? "row-blocked" : ""}`}>
            <div className={`ovr-badge sector-${player.sector.toLowerCase()}`}>{player.overall}</div>
            <div className="player-info">
              <div className="player-name-line">
                <strong>{player.short_name}</strong>
                <span className={`pos-tag sector-${player.sector.toLowerCase()}`}>
                  {player.positions.join("/")}
                </span>
                <span className="player-sub">{player.age} anos • {player.nationality_name}</span>
              </div>
              <div className="player-club-line">
                {player.club_name} • <span>{player.league_name}</span>
                {player.value_eur > 0 && ` • €${(player.value_eur / 1_000_000).toFixed(1)}M`}
              </div>
              {!eligible_for_active && violations.length > 0 && (
                <div className="violation-list">
                  {violations.map((v, i) => (
                    <span key={i} className="violation-chip">🔒 {v.message}</span>
                  ))}
                </div>
              )}
            </div>

            <div className="player-actions">
              <button
                className="btn btn-ghost btn-sm"
                disabled={busy}
                title={`Adicionar à fila de ${queueTargetUser}`}
                onClick={() => onQueuePlayer(queueTargetUser, player.player_id)}
              >
                + Fila ({queueTargetUser})
              </button>
              <button
                className="btn btn-primary btn-sm"
                disabled={!canDraftNow || !eligible_for_active}
                onClick={() => onDraftPlayer(player.player_id)}
              >
                Draftar
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};