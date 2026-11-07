import React, { useEffect, useState } from "react";
import { localAdapter } from "../adapters/localAdapter";
import {
  DraftFormat,
  DraftRulesConfig,
  EditionSummary,
  FallbackStrategy,
  RoomStateSnapshot,
  RoomSummaryItem,
  ViabilityReport,
} from "../types/draft";

interface LeagueSetupProps {
  onEnterRoom: (room: RoomStateSnapshot) => void;
}

export const LeagueSetup: React.FC<LeagueSetupProps> = ({ onEnterRoom }) => {
  const [editions, setEditions] = useState<EditionSummary[]>([]);
  const [savedRooms, setSavedRooms] = useState<RoomSummaryItem[]>([]);

  const [roomName, setRoomName] = useState("Campeonato EA FC entre Amigos");
  const [edition, setEdition] = useState("fc27");
  const [pickClock, setPickClock] = useState(60);
  const [draftFormat, setDraftFormat] = useState<DraftFormat>("snake");
  const [strategy, setStrategy] = useState<FallbackStrategy>("smart_balanced");
  const [participantsText, setParticipantsText] = useState("Pedro\nMateus\nLucas\nGabriel");
  const [useManualOrder, setUseManualOrder] = useState(false);
  const [lotterySeed, setLotterySeed] = useState("");

  const [rosterSize, setRosterSize] = useState(11);
  const [maxPerClub, setMaxPerClub] = useState<string>("1");
  const [maxPerLeague, setMaxPerLeague] = useState<string>("");
  const [maxPerNation, setMaxPerNation] = useState<string>("");
  const [minGK, setMinGK] = useState(1);
  const [minDEF, setMinDEF] = useState(3);
  const [minMID, setMinMID] = useState(3);
  const [minFWD, setMinFWD] = useState(2);

  const [viability, setViability] = useState<ViabilityReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    localAdapter.listEditions().then((list) => {
      setEditions(list);
      if (list.length > 0) setEdition(list[0].edition);
    });
    localAdapter.listRooms().then(setSavedRooms);
  }, []);

  const parseParticipants = () =>
    participantsText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);

  const buildRules = (): DraftRulesConfig => ({
    roster_size: rosterSize,
    draft_format: draftFormat,
    max_per_club: maxPerClub ? Number(maxPerClub) : null,
    max_per_league: maxPerLeague ? Number(maxPerLeague) : null,
    max_per_nationality: maxPerNation ? Number(maxPerNation) : null,
    ignore_free_agents_in_group_limits: true,
    min_overall: 1,
    max_overall: 99,
    max_budget_eur: null,
    ovr_tier_caps: [],
    min_by_sector: { GK: minGK, DEF: minDEF, MID: minMID, FWD: minFWD },
    max_by_sector: {},
    max_by_position: {},
  });

  const applyPreset = (type: "full18" | "fast11" | "smoke4") => {
    if (type === "full18") {
      setRosterSize(18); setMinGK(1); setMinDEF(4); setMinMID(3); setMinFWD(2);
    } else if (type === "fast11") {
      setRosterSize(11); setMinGK(1); setMinDEF(3); setMinMID(3); setMinFWD(2);
    } else {
      setRosterSize(4); setMinGK(1); setMinDEF(1); setMinMID(1); setMinFWD(1);
    }
    setViability(null);
  };

  const handleCheckViability = async () => {
    setError(null);
    setLoading(true);
    try {
      const rep = await localAdapter.simulateViability({
        edition,
        gender: "male",
        participants: parseParticipants(),
        rules: buildRules(),
        fallback_strategy: strategy,
      });
      setViability(rep);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    setError(null);
    setLoading(true);
    try {
      const snap = await localAdapter.createRoom({
        room_name: roomName,
        edition,
        gender: "male",
        pick_clock_seconds: pickClock,
        participants: parseParticipants(),
        use_manual_order: useManualOrder,
        lottery_seed: lotterySeed || undefined,
        rules: buildRules(),
        fallback_strategy: strategy,
      });
      onEnterRoom(snap);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="setup-container">
      <div className="setup-hero">
        <h1>⚽ DraftFC — Modo Comissário Local</h1>
        <p>Configure as regras estilo Sleeper, valide o pool no SQLite e transmita a sala para os amigos.</p>
      </div>

      {savedRooms.length > 0 && (
        <div className="setup-card">
          <h3>Salas Salvas no SQLite (Retomar Draft)</h3>
          <div className="saved-rooms-grid">
            {savedRooms.map((rm) => (
              <button
                key={rm.room_id}
                className="saved-room-btn"
                onClick={() => localAdapter.getRoom(rm.room_id).then(onEnterRoom)}
              >
                <strong>{rm.room_name}</strong>
                <span>{rm.edition.toUpperCase()} • {rm.picks_made}/{rm.total_picks} picks • {rm.status}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="setup-grid">
        <div className="setup-card">
          <h3>1. Liga, Banco e Relógio</h3>
          <label>Nome do Campeonato</label>
          <input value={roomName} onChange={(e) => setRoomName(e.target.value)} />

          <label>Banco de Jogadores (SQLite Local)</label>
          <select value={edition} onChange={(e) => setEdition(e.target.value)}>
            {editions.map((ed) => (
              <option key={`${ed.edition}-${ed.gender}`} value={ed.edition}>
                {ed.edition.toUpperCase()} ({ed.players_count} jogadores / {ed.clubs_count} clubes)
              </option>
            ))}
          </select>

          <div className="row-2">
            <div>
              <label>Pick Clock (segundos, 0 = sem tempo)</label>
              <input type="number" min={0} max={600} value={pickClock} onChange={(e) => setPickClock(Number(e.target.value))} />
            </div>
            <div>
              <label>Formato de Ordem</label>
              <select value={draftFormat} onChange={(e) => setDraftFormat(e.target.value as DraftFormat)}>
                <option value="snake">Snake (1-N, N-1)</option>
                <option value="third_round_reversal">Snake 3RR (Inversão 3ª Rodada)</option>
                <option value="linear">Linear Fixo</option>
              </select>
            </div>
          </div>

          <label>Participantes (1 amigo por linha)</label>
          <textarea rows={5} value={participantsText} onChange={(e) => setParticipantsText(e.target.value)} />

          <label className="checkbox-inline">
            <input type="checkbox" checked={useManualOrder} onChange={(e) => setUseManualOrder(e.target.checked)} />
            Manter ordem exata digitada acima (sem sorteio)
          </label>
          {!useManualOrder && (
            <input
              placeholder="Semente opcional p/ sorteio auditável (ex: sexta-fc27)"
              value={lotterySeed}
              onChange={(e) => setLotterySeed(e.target.value)}
            />
          )}
        </div>

        <div className="setup-card">
          <h3>2. Travas de Elenco e Auto-Draft</h3>
          <div className="preset-bar">
            <button type="button" className="pill" onClick={() => applyPreset("full18")}>Elenco 18</button>
            <button type="button" className="pill" onClick={() => applyPreset("fast11")}>Titulares 11</button>
            <button type="button" className="pill" onClick={() => applyPreset("smoke4")}>Teste Rápido 4</button>
          </div>

          <div className="row-2">
            <div>
              <label>Tamanho do Elenco</label>
              <input type="number" min={1} max={30} value={rosterSize} onChange={(e) => setRosterSize(Number(e.target.value))} />
            </div>
            <div>
              <label>Estratégia de Auto-Draft</label>
              <select value={strategy} onChange={(e) => setStrategy(e.target.value as FallbackStrategy)}>
                <option value="smart_balanced">Equilibrado por Setor (Recomendado)</option>
                <option value="strict_overall">Maior Overall Puro</option>
              </select>
            </div>
          </div>

          <div className="row-3">
            <div>
              <label>Máx. / Clube</label>
              <input type="number" min={1} placeholder="Livre" value={maxPerClub} onChange={(e) => setMaxPerClub(e.target.value)} />
            </div>
            <div>
              <label>Máx. / Liga</label>
              <input type="number" min={1} placeholder="Livre" value={maxPerLeague} onChange={(e) => setMaxPerLeague(e.target.value)} />
            </div>
            <div>
              <label>Máx. / País</label>
              <input type="number" min={1} placeholder="Livre" value={maxPerNation} onChange={(e) => setMaxPerNation(e.target.value)} />
            </div>
          </div>

          <label>Mínimo Obrigatório por Setor</label>
          <div className="row-4">
            <div><small>GK</small><input type="number" min={0} value={minGK} onChange={(e) => setMinGK(Number(e.target.value))} /></div>
            <div><small>DEF</small><input type="number" min={0} value={minDEF} onChange={(e) => setMinDEF(Number(e.target.value))} /></div>
            <div><small>MID</small><input type="number" min={0} value={minMID} onChange={(e) => setMinMID(Number(e.target.value))} /></div>
            <div><small>FWD</small><input type="number" min={0} value={minFWD} onChange={(e) => setMinFWD(Number(e.target.value))} /></div>
          </div>

          {error && <div className="alert-error">{error}</div>}
          {viability && (
            <div className={viability.viable ? "alert-ok" : "alert-error"}>
              {viability.viable
                ? `✅ Regras 100% viáveis! ${viability.total_picks_simulated} escolhas simuladas em ${viability.elapsed_ms} ms.`
                : `❌ Deadlock detectado no pick #${viability.total_picks_simulated + 1}: ${viability.error_message}`}
            </div>
          )}

          <div className="setup-actions">
            <button className="btn btn-secondary" disabled={loading} onClick={handleCheckViability}>
              🧪 Simular Viabilidade
            </button>
            <button className="btn btn-primary" disabled={loading} onClick={handleCreate}>
              🚀 Criar e Abrir Sala de Draft
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};