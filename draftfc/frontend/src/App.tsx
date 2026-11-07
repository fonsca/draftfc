import React, { useEffect, useMemo, useRef, useState } from "react";
import "./styles.css";

type SectorType = "GK" | "DEF" | "MID" | "ATT";

interface RoomPlayer {
  player_id: number;
  short_name: string;
  overall: number;
  primary_position: string;
  sector?: SectorType;
  club_id: number;
  club_name: string;
  league_name: string;
  nationality: string;
  value_eur: number;
}

interface DraftPickRecord {
  overall_pick: number;
  round_number: number;
  pick_in_round: number;
  seat_number: number;
  participant_name: string;
  player: RoomPlayer;
  source: "manual" | "commissioner" | "autodraft_queue" | "autodraft_best";
  timestamp_ms: number;
}

interface ParticipantState {
  seat_number: number;
  display_name: string;
  claimed: boolean;
  is_online: boolean;
  roster: RoomPlayer[];
  queue: number[];
}

interface RoomSummary {
  room_code: string;
  room_name: string;
  edition: string;
  status: "lobby" | "active" | "paused" | "completed";
  num_teams: number;
  claimed_seats: number;
  online_seats: number;
  rounds: number;
  current_round: number;
  current_pick_index: number;
  total_picks: number;
  has_password: boolean;
}

interface PublicRoomState {
  room_code: string;
  room_name: string;
  edition: string;
  status: "lobby" | "active" | "paused" | "completed";
  num_teams: number;
  rounds: number;
  pick_clock_seconds: number;
  remaining_seconds: number;
  current_pick_index: number;
  total_picks: number;
  current_round: number;
  current_seat: number | null;
  snake_order_preview: number[];
  participants: ParticipantState[];
  picks_history: DraftPickRecord[];
  drafted_player_ids: number[];
  available_players_count: number;
}

const GK_SET = new Set(["GK", "GL", "GOL", "GR", "POR", "G", "GOLEIRO", "TW"]);
const DEF_SET = new Set([
  "CB", "LCB", "RCB", "ZAG", "DC", "DFC",
  "LB", "RB", "LE", "LD", "DE", "DD", "LI",
  "LWB", "RWB", "ADE", "ADD", "ALA", "AL", "SW", "DEF",
]);
const MID_SET = new Set([
  "CDM", "LDM", "RDM", "VOL", "MDC", "MCD",
  "CM", "LCM", "RCM", "MC",
  "CAM", "LAM", "RAM", "MEI", "MO", "MCO",
  "LM", "RM", "ME", "MD", "MID", "WM",
]);
const ATT_SET = new Set([
  "ST", "LS", "RS", "ATA", "CA", "PL",
  "CF", "LF", "RF", "SA", "MP", "SD",
  "LW", "RW", "PE", "PD", "MAE", "MAD", "PTE", "PTD", "EXI", "EXD", "ATT", "FWD",
]);

function getPlayerSector(player: RoomPlayer | { primary_position: string; sector?: SectorType }): SectorType {
  const tokens = (player.primary_position || "")
    .toUpperCase()
    .replace(/[,/;|-]/g, " ")
    .split(/\s+/)
    .filter(Boolean);

  for (const t of tokens) {
    if (GK_SET.has(t)) return "GK";
    if (DEF_SET.has(t)) return "DEF";
    if (MID_SET.has(t)) return "MID";
    if (ATT_SET.has(t)) return "ATT";
  }
  if (player.sector && ["GK", "DEF", "MID", "ATT"].includes(player.sector)) {
    return player.sector;
  }
  return "MID";
}

function formatEuro(val: number): string {
  if (!val || val <= 0) return "€0";
  if (val >= 1_000_000) return `€${(val / 1_000_000).toFixed(1)}M`;
  if (val >= 1_000) return `€${(val / 1_000).toFixed(0)}K`;
  return `€${val}`;
}

export function App() {
  const [roomCode, setRoomCode] = useState<string>(() => sessionStorage.getItem("dfc_room_code") || "");
  const [seatToken, setSeatToken] = useState<string>(() => sessionStorage.getItem("dfc_seat_token") || "");
  const [commToken, setCommToken] = useState<string>(() => sessionStorage.getItem("dfc_comm_token") || "");
  const [mySeat, setMySeat] = useState<number>(() => Number(sessionStorage.getItem("dfc_my_seat") || 0));

  const [activeRooms, setActiveRooms] = useState<RoomSummary[]>([]);
  const [roomState, setRoomState] = useState<PublicRoomState | null>(null);
  const [playersPool, setPlayersPool] = useState<RoomPlayer[]>([]);
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [simulatedOffline, setSimulatedOffline] = useState<boolean>(false);

  // Poder de Comissário: permitir escolher jogador pelo manager que está na vez
  const [commPickForOthers, setCommPickForOthers] = useState<boolean>(true);

  // Formulário Criar Sala
  const [createName, setCreateName] = useState("Superliga EA FC");
  const [hostName, setHostName] = useState("Comissário");
  const [edition, setEdition] = useState("fc26");
  const [numTeams, setNumTeams] = useState(16);
  const [rounds, setRounds] = useState(16);
  const [clockSec, setClockSec] = useState(60);
  const [roomPassword, setRoomPassword] = useState("");
  const [maxPerClub, setMaxPerClub] = useState<number>(2);

  // Formulário Entrar em Sala
  const [joinCode, setJoinCode] = useState("");
  const [joinName, setJoinName] = useState("");
  const [joinPassword, setJoinPassword] = useState("");
  const [joinSeat, setJoinSeat] = useState<string>("");

  // Abas e Filtros
  const [activeTab, setActiveTab] = useState<"pool" | "board" | "history">("pool");
  const [searchQuery, setSearchQuery] = useState("");
  const [sectorFilter, setSectorFilter] = useState<string>("ALL");
  const [hideDrafted, setHideDrafted] = useState<boolean>(true);
  const [inspectedSeat, setInspectedSeat] = useState<number>(1);
  const [banner, setBanner] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState<boolean>(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);

  const showToast = (type: "success" | "error", text: string) => {
    setBanner({ type, text });
  };

  const saveSession = (code: string, token: string, seat: number, cToken = "") => {
    sessionStorage.setItem("dfc_room_code", code);
    sessionStorage.setItem("dfc_seat_token", token);
    sessionStorage.setItem("dfc_my_seat", String(seat));
    if (cToken) {
      sessionStorage.setItem("dfc_comm_token", cToken);
    } else {
      sessionStorage.removeItem("dfc_comm_token");
    }
    setRoomCode(code);
    setSeatToken(token);
    setMySeat(seat);
    setCommToken(cToken);
    setInspectedSeat(seat || 1);
  };

  const leaveRoom = () => {
    if (wsRef.current) wsRef.current.close();
    sessionStorage.clear();
    setRoomCode("");
    setSeatToken("");
    setCommToken("");
    setMySeat(0);
    setRoomState(null);
    setPlayersPool([]);
    fetchActiveRooms();
  };

  const fetchActiveRooms = async () => {
    try {
      const resp = await fetch("/api/multiplayer/rooms");
      if (resp.ok) {
        const data = await resp.json();
        setActiveRooms(data.rooms || []);
      }
    } catch {
      // ignora erro transitório
    }
  };

  useEffect(() => {
    if (!roomCode) {
      fetchActiveRooms();
      const id = window.setInterval(fetchActiveRooms, 4000);
      return () => clearInterval(id);
    }
  }, [roomCode]);

  const fetchRoomPlayers = async (code: string) => {
    try {
      const resp = await fetch(`/api/multiplayer/rooms/${code}/players`);
      if (resp.status === 404) {
        leaveRoom();
        return;
      }
      if (resp.ok) {
        const data = await resp.json();
        setPlayersPool(data.players || []);
      }
    } catch {
      // ignora erro transitório
    }
  };

  useEffect(() => {
    if (!roomCode || simulatedOffline) {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setWsConnected(false);
      return;
    }

    fetchRoomPlayers(roomCode);

    const connectWs = () => {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${proto}//${window.location.host}/ws/rooms/${roomCode}?seat_token=${encodeURIComponent(
        seatToken
      )}`;
      const socket = new WebSocket(wsUrl);
      wsRef.current = socket;

      socket.onopen = () => {
        setWsConnected(true);
      };

      socket.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.event === "room_state" && msg.state) {
            setRoomState(msg.state);
            if (msg.seat_number) {
              setMySeat(msg.seat_number);
              sessionStorage.setItem("dfc_my_seat", String(msg.seat_number));
            }
          } else if (msg.event === "order_randomized" && msg.state) {
            setRoomState(msg.state);
            if (msg.seat_number) {
              setMySeat(msg.seat_number);
              sessionStorage.setItem("dfc_my_seat", String(msg.seat_number));
            }
            const firstTeam = msg.state.participants?.[0]?.display_name || "Time 1";
            showToast("success", `🎲 Ordem do Draft sorteada! 1ª Escolha: ${firstTeam}`);
          } else if (msg.event === "participant_joined" && msg.state) {
            setRoomState(msg.state);
            showToast("success", `${msg.display_name} conectou no Assento #${msg.seat_number}!`);
          } else if (msg.event === "clock_tick") {
            setRoomState((prev) =>
              prev ? { ...prev, remaining_seconds: msg.remaining_seconds } : prev
            );
          } else if (msg.event === "player_drafted") {
            const p = msg.pick;
            if (msg.state) setRoomState(msg.state);
            const via = p.source === "commissioner" ? " (via Comissário)" : "";
            showToast(
              "success",
              `Pick #${p.overall_pick}: ${p.participant_name} escolheu ${p.player.short_name} (${p.player.primary_position} • ${p.player.overall} OVR)${via}!`
            );
          } else if (msg.event === "pick_undone" && msg.state) {
            setRoomState(msg.state);
            const u = msg.undone_pick;
            showToast(
              "success",
              `↩ Escolha #${u.overall_pick} (${u.player.short_name}) desfeita pelo Comissário!`
            );
          } else if (msg.event === "room_deleted") {
            showToast("error", "A sala foi encerrada e excluída pelo Comissário.");
            leaveRoom();
          } else if (msg.event === "participant_presence") {
            setRoomState((prev) => {
              if (!prev) return prev;
              return {
                ...prev,
                participants: prev.participants.map((pt) =>
                  pt.seat_number === msg.seat_number ? { ...pt, is_online: msg.is_online } : pt
                ),
              };
            });
          } else if (msg.event === "queue_updated") {
            setRoomState((prev) => {
              if (!prev) return prev;
              return {
                ...prev,
                participants: prev.participants.map((pt) =>
                  pt.seat_number === msg.seat_number ? { ...pt, queue: msg.queue } : pt
                ),
              };
            });
          } else if (msg.event === "pick_error") {
            showToast("error", `[${msg.code}] ${msg.message}`);
          }
        } catch {
          // ignore
        }
      };

      socket.onclose = () => {
        setWsConnected(false);
        if (!simulatedOffline && roomCode) {
          reconnectTimerRef.current = window.setTimeout(connectWs, 1800);
        }
      };
    };

    connectWs();

    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [roomCode, seatToken, simulatedOffline]);

  const handleCreateRoom = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const resp = await fetch("/api/multiplayer/rooms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          commissioner_name: hostName.trim() || "Comissário",
          config: {
            room_name: createName.trim() || "Liga DraftFC",
            password: roomPassword,
            edition,
            num_teams: Number(numTeams),
            rounds: Number(rounds),
            pick_clock_seconds: Number(clockSec),
            max_per_club: maxPerClub > 0 ? Number(maxPerClub) : null,
          },
        }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "Falha ao criar sala.");
      }
      const data = await resp.json();
      setRoomState(data.state);
      saveSession(data.room_code, data.seat_token, data.seat_number, data.commissioner_token);
      showToast("success", `Sala ${data.room_code} criada com sucesso!`);
    } catch (err: any) {
      showToast("error", err.message || "Erro ao conectar ao servidor.");
    } finally {
      setBusy(false);
    }
  };

  const handleJoinRoom = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!joinCode.trim() || !joinName.trim()) {
      showToast("error", "Informe o código da sala e seu nome.");
      return;
    }
    setBusy(true);
    try {
      const code = joinCode.trim().toUpperCase();
      const resp = await fetch(`/api/multiplayer/rooms/${code}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: joinName.trim(),
          password: joinPassword,
          seat_number: joinSeat ? Number(joinSeat) : null,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "Não foi possível entrar na sala.");
      }
      const data = await resp.json();
      setRoomState(data.state);
      saveSession(
        data.room_code,
        data.seat_token,
        data.seat_number,
        data.commissioner_token || ""
      );
      showToast("success", `Conectado ao Assento #${data.seat_number}!`);
    } catch (err: any) {
      showToast("error", err.message || "Falha ao entrar na sala.");
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteRoom = async (codeToDelete: string) => {
    if (!window.confirm(`Deseja realmente excluir a sala ${codeToDelete}?`)) return;
    try {
      const resp = await fetch(`/api/multiplayer/rooms/${codeToDelete}`, { method: "DELETE" });
      if (resp.ok) {
        if (roomCode === codeToDelete) {
          leaveRoom();
        } else {
          fetchActiveRooms();
        }
        showToast("success", `Sala ${codeToDelete} excluída.`);
      }
    } catch {
      showToast("error", "Erro ao excluir a sala.");
    }
  };

  const handleExportCsv = (codeToExport: string) => {
    window.open(`/api/multiplayer/rooms/${codeToExport}/export/csv`, "_blank");
  };

  const handleStartDraft = async () => {
    if (!roomState || !commToken) return;
    try {
      const resp = await fetch(
        `/api/multiplayer/rooms/${roomState.room_code}/start?commissioner_token=${encodeURIComponent(
          commToken
        )}&auto_clock=true`,
        { method: "POST" }
      );
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "Erro ao iniciar o draft.");
      }
      const data = await resp.json();
      setRoomState(data.state);
      showToast("success", "O Draft começou!");
    } catch (err: any) {
      showToast("error", err.message);
    }
  };

  const handleRandomizeOrder = async () => {
    if (!roomState || !commToken) return;
    try {
      const resp = await fetch(
        `/api/multiplayer/rooms/${roomState.room_code}/randomize-order?commissioner_token=${encodeURIComponent(
          commToken
        )}`,
        { method: "POST" }
      );
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Não foi possível sortear a ordem.");
    } catch (err: any) {
      showToast("error", err.message);
    }
  };

  const handleUndoPick = async () => {
    if (!roomState || !commToken) return;
    try {
      const resp = await fetch(
        `/api/multiplayer/rooms/${roomState.room_code}/undo-pick?commissioner_token=${encodeURIComponent(
          commToken
        )}`,
        { method: "POST" }
      );
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Não foi possível voltar a escolha.");
    } catch (err: any) {
      showToast("error", err.message);
    }
  };

  const handleTogglePause = async () => {
    if (!roomState || !commToken) return;
    try {
      const resp = await fetch(
        `/api/multiplayer/rooms/${roomState.room_code}/toggle-pause?commissioner_token=${encodeURIComponent(
          commToken
        )}`,
        { method: "POST" }
      );
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Erro ao pausar/retomar.");
    } catch (err: any) {
      showToast("error", err.message);
    }
  };

  const handleMakePick = (playerId: number) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      showToast("error", "WebSocket desconectado. Reconecte antes de escolher.");
      return;
    }
    const tokenToUse = commToken && commPickForOthers ? commToken : seatToken;
    wsRef.current.send(
      JSON.stringify({
        action: "make_pick",
        seat_token: tokenToUse,
        player_id: playerId,
        request_id: `ui-${Date.now()}`,
      })
    );
  };

  const handleToggleQueue = (playerId: number) => {
    if (!roomState || !mySeat) return;
    const me = roomState.participants.find((p) => p.seat_number === mySeat);
    if (!me) return;
    const exists = me.queue.includes(playerId);
    const updated = exists ? me.queue.filter((id) => id !== playerId) : [...me.queue, playerId];

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          action: "set_queue",
          seat_token: seatToken,
          player_ids: updated,
        })
      );
    }
  };

  const draftedSet = useMemo(
    () => new Set(roomState?.drafted_player_ids || []),
    [roomState?.drafted_player_ids]
  );

  const picksByRoundAndSeat = useMemo(() => {
    const map = new Map<string, DraftPickRecord>();
    if (!roomState) return map;
    for (const rec of roomState.picks_history) {
      map.set(`${rec.round_number}-${rec.seat_number}`, rec);
    }
    return map;
  }, [roomState?.picks_history]);

  const filteredPlayers = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return playersPool
      .filter((p) => {
        if (hideDrafted && draftedSet.has(p.player_id)) return false;
        const sec = getPlayerSector(p);
        if (sectorFilter !== "ALL" && sec !== sectorFilter) return false;
        if (q) {
          const hay = `${p.short_name} ${p.club_name} ${p.league_name} ${p.nationality} ${p.primary_position}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      })
      .slice(0, 500);
  }, [playersPool, hideDrafted, draftedSet, sectorFilter, searchQuery]);

  const inspectedParticipant = useMemo(() => {
    if (!roomState) return null;
    return (
      roomState.participants.find((p) => p.seat_number === inspectedSeat) ||
      roomState.participants[0] ||
      null
    );
  }, [roomState, inspectedSeat]);

  const myParticipant = useMemo(() => {
    if (!roomState || !mySeat) return null;
    return roomState.participants.find((p) => p.seat_number === mySeat) || null;
  }, [roomState, mySeat]);

  const isStrictlyMyTurn = Boolean(
    roomState && roomState.status === "active" && roomState.current_seat === mySeat
  );

  const canCommissionerForcePick = Boolean(
    roomState &&
      (roomState.status === "active" || roomState.status === "paused") &&
      commToken &&
      commPickForOthers
  );

  const canPickNow = isStrictlyMyTurn || canCommissionerForcePick;

  // =========================================================================
  // RENDER: LOBBY (CRIAR, ENTRAR E VISUALIZAR SALAS ATIVAS)
  // =========================================================================
  if (!roomCode || !roomState) {
    return (
      <div>
        <header className="topbar">
          <div className="brand-wrap">
            <div className="brand-logo">DFC</div>
            <div>
              <div className="brand-title">DRAFTFC // ARENA</div>
              <div className="brand-sub">EA SPORTS FC 26 & 27 • MULTIPLAYER REAL-TIME</div>
            </div>
          </div>
          <div className="topbar-controls">
            <span className="pill-badge">
              <span className="status-dot online" />
              Salas Ativas: {activeRooms.length}
            </span>
          </div>
        </header>

        <main className="lobby-container">
          {banner && (
            <div className={`toast-banner ${banner.type}`}>
              <span>{banner.text}</span>
              <button className="btn btn-sm btn-secondary" onClick={() => setBanner(null)}>
                Fechar
              </button>
            </div>
          )}

          <div className="hero-banner">
            <h1>Draft Multiplayer em Tempo Real</h1>
            <p>
              Crie uma sala estilo Sleeper com sorteio de ordem Snake (1–16, 16–1), poderes de Comissário,
              Draft Board colorido por posição e exportação direta para o EA FC.
            </p>
          </div>

          <div className="lobby-grid">
            {/* CRIAR SALA */}
            <section className="glass-panel">
              <div className="panel-header">
                <div className="panel-title">⚡ Criar Nova Sala (Comissário)</div>
                <span className="pill-badge">Host</span>
              </div>

              <form onSubmit={handleCreateRoom}>
                <div className="form-grid">
                  <div className="form-group">
                    <label>Nome da Liga / Sala</label>
                    <input
                      className="input-field"
                      value={createName}
                      onChange={(e) => setCreateName(e.target.value)}
                      required
                    />
                  </div>
                  <div className="form-group">
                    <label>Seu Nome (Comissário)</label>
                    <input
                      className="input-field"
                      value={hostName}
                      onChange={(e) => setHostName(e.target.value)}
                      required
                    />
                  </div>
                </div>

                <div className="form-grid">
                  <div className="form-group">
                    <label>Edição do Banco</label>
                    <select
                      className="input-field"
                      value={edition}
                      onChange={(e) => setEdition(e.target.value)}
                    >
                      <option value="fc26">EA Sports FC 26</option>
                      <option value="fc27">EA Sports FC 27</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label>Senha da Sala (Opcional)</label>
                    <input
                      className="input-field"
                      type="password"
                      placeholder="Ex: fc26"
                      value={roomPassword}
                      onChange={(e) => setRoomPassword(e.target.value)}
                    />
                  </div>
                </div>

                <div className="form-grid">
                  <div className="form-group">
                    <label>Quantidade de Times (2–32)</label>
                    <input
                      className="input-field"
                      type="number"
                      min={2}
                      max={32}
                      value={numTeams}
                      onChange={(e) => setNumTeams(Number(e.target.value))}
                    />
                  </div>
                  <div className="form-group">
                    <label>Rodadas por Time</label>
                    <input
                      className="input-field"
                      type="number"
                      min={1}
                      max={30}
                      value={rounds}
                      onChange={(e) => setRounds(Number(e.target.value))}
                    />
                  </div>
                </div>

                <div className="form-grid">
                  <div className="form-group">
                    <label>Pick Clock (Segundos)</label>
                    <input
                      className="input-field"
                      type="number"
                      min={5}
                      max={600}
                      value={clockSec}
                      onChange={(e) => setClockSec(Number(e.target.value))}
                    />
                  </div>
                  <div className="form-group">
                    <label>Máx. Jogadores por Clube</label>
                    <input
                      className="input-field"
                      type="number"
                      min={0}
                      max={18}
                      value={maxPerClub}
                      onChange={(e) => setMaxPerClub(Number(e.target.value))}
                    />
                  </div>
                </div>

                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ width: "100%", marginTop: 8 }}
                  disabled={busy}
                >
                  {busy ? "Criando Sala..." : "Criar Sala Multiplayer & Abrir Lobby"}
                </button>
              </form>
            </section>

            {/* ENTRAR EM SALA EXISTENTE */}
            <section className="glass-panel">
              <div className="panel-header">
                <div className="panel-title">🎮 Entrar / Reconectar na Sala</div>
                <span className="pill-badge">Participante</span>
              </div>

              <form onSubmit={handleJoinRoom}>
                <div className="form-group">
                  <label>Código da Sala</label>
                  <input
                    className="input-field"
                    placeholder="Ex: A1B2C3"
                    value={joinCode}
                    onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
                    required
                  />
                </div>

                <div className="form-group">
                  <label>Nome do Seu Time / Manager</label>
                  <input
                    className="input-field"
                    placeholder="Informe o mesmo nome para reconectar no seu time"
                    value={joinName}
                    onChange={(e) => setJoinName(e.target.value)}
                    required
                  />
                </div>

                <div className="form-grid">
                  <div className="form-group">
                    <label>Senha da Sala</label>
                    <input
                      className="input-field"
                      type="password"
                      placeholder="Se houver"
                      value={joinPassword}
                      onChange={(e) => setJoinPassword(e.target.value)}
                    />
                  </div>
                  <div className="form-group">
                    <label>Assento Preferido (Opcional)</label>
                    <input
                      className="input-field"
                      type="number"
                      min={1}
                      max={32}
                      placeholder="Auto"
                      value={joinSeat}
                      onChange={(e) => setJoinSeat(e.target.value)}
                    />
                  </div>
                </div>

                <button
                  type="submit"
                  className="btn btn-secondary"
                  style={{
                    width: "100%",
                    marginTop: 14,
                    borderColor: "var(--accent-cyan)",
                    color: "#67e8f9",
                  }}
                  disabled={busy}
                >
                  {busy ? "Conectando..." : "Conectar à Sala de Draft"}
                </button>
              </form>
            </section>
          </div>

          {/* LISTA DE SALAS ATIVAS */}
          <section className="glass-panel" style={{ marginTop: 24 }}>
            <div className="panel-header">
              <div className="panel-title">📡 Salas Ativas no Servidor ({activeRooms.length})</div>
              <button className="btn btn-sm btn-secondary" onClick={fetchActiveRooms}>
                🔄 Atualizar Lista
              </button>
            </div>

            {activeRooms.length === 0 ? (
              <p style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}>
                Nenhuma sala ativa no momento. Crie uma sala acima para iniciar!
              </p>
            ) : (
              <div style={{ display: "grid", gap: 10 }}>
                {activeRooms.map((r) => (
                  <div
                    key={r.room_code}
                    className="seat-item"
                    style={{ cursor: "default", padding: "14px 18px" }}
                  >
                    <div>
                      <div style={{ fontWeight: 800, fontSize: "1rem", display: "flex", gap: 10, alignItems: "center" }}>
                        <span style={{ color: "var(--accent-neon)" }}>#{r.room_code}</span>
                        <span>{r.room_name}</span>
                        <span className="pill-badge">{r.edition.toUpperCase()}</span>
                        <span className="pill-badge">{r.status.toUpperCase()}</span>
                      </div>
                      <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: 4 }}>
                        Managers: {r.claimed_seats}/{r.num_teams} ({r.online_seats} online) • Rodada{" "}
                        {r.current_round}/{r.rounds} • Escolhas: {r.current_pick_index}/{r.total_picks}
                      </div>
                    </div>

                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        className="btn btn-sm btn-primary"
                        onClick={() => {
                          setJoinCode(r.room_code);
                          window.scrollTo({ top: 0, behavior: "smooth" });
                        }}
                      >
                        Selecionar Sala
                      </button>
                      <button
                        className="btn btn-sm btn-secondary"
                        onClick={() => handleExportCsv(r.room_code)}
                      >
                        📥 CSV
                      </button>
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={() => handleDeleteRoom(r.room_code)}
                      >
                        🗑️ Excluir
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </main>
      </div>
    );
  }

  // =========================================================================
  // RENDER: ARENA DE DRAFT MULTIPLAYER ATIVA
  // =========================================================================
  const clockPct = Math.max(
    0,
    Math.min(100, (roomState.remaining_seconds / Math.max(1, roomState.pick_clock_seconds)) * 100)
  );
  const clockColor =
    roomState.remaining_seconds <= 10
      ? "var(--accent-danger)"
      : roomState.remaining_seconds <= 20
      ? "var(--accent-gold)"
      : "var(--accent-neon)";

  const activeParticipant = roomState.participants.find(
    (p) => p.seat_number === roomState.current_seat
  );

  return (
    <div>
      {/* TOPBAR */}
      <header className="topbar">
        <div className="brand-wrap">
          <div className="brand-logo">DFC</div>
          <div>
            <div className="brand-title">{roomState.room_name}</div>
            <div className="brand-sub">
              EDIÇÃO {roomState.edition.toUpperCase()} • RODADA {roomState.current_round}/{roomState.rounds} •{" "}
              PICK {Math.min(roomState.current_pick_index + 1, roomState.total_picks)}/{roomState.total_picks}
            </div>
          </div>
        </div>

        <div className="topbar-controls">
          <span
            className="pill-badge room-code"
            title="Clique para copiar o código da sala"
            onClick={() => {
              navigator.clipboard?.writeText(roomState.room_code);
              showToast("success", `Código ${roomState.room_code} copiado!`);
            }}
          >
            SALA: <strong>{roomState.room_code}</strong> 📋
          </span>

          <span className="pill-badge">
            Meu Assento: <strong>#{mySeat || "Espectador"}</strong>
            {commToken ? " 👑" : ""}
          </span>

          <span className="pill-badge">
            <span className={`status-dot ${wsConnected ? "online" : "offline"}`} />
            {wsConnected ? "WS Ao Vivo" : "Offline"}
          </span>

          <button
            className="btn btn-sm btn-secondary"
            onClick={() => handleExportCsv(roomState.room_code)}
            title="Baixar planilha CSV com todas as escolhas do draft"
          >
            📥 Exportar CSV
          </button>

          <button
            className={`btn btn-sm ${simulatedOffline ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setSimulatedOffline((prev) => !prev)}
          >
            {simulatedOffline ? "🔌 Reconectar WS" : "⚡ Simular Queda WS"}
          </button>

          {commToken && (
            <button
              className="btn btn-sm btn-danger"
              onClick={() => handleDeleteRoom(roomState.room_code)}
            >
              🗑️ Excluir Sala
            </button>
          )}

          <button className="btn btn-sm btn-secondary" onClick={leaveRoom}>
            Voltar ao Lobby
          </button>
        </div>
      </header>

      <main className="draft-shell">
        {banner && (
          <div className={`toast-banner ${banner.type}`}>
            <span>{banner.text}</span>
            <button className="btn btn-sm btn-secondary" onClick={() => setBanner(null)}>
              ✕
            </button>
          </div>
        )}

        {/* BARRA EXCLUSIVA DE PODERES DO COMISSÁRIO */}
        {commToken && (
          <section className="commissioner-bar">
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: "1.1rem" }}>👑</span>
              <div>
                <div style={{ fontWeight: 800, fontSize: "0.92rem" }}>Painel de Poderes do Comissário</div>
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                  Sorteie a ordem dos times, volte escolhas equivocadas ou selecione atletas para outros managers.
                </div>
              </div>
            </div>

            <div className="commissioner-actions">
              {roomState.status === "lobby" && (
                <button className="btn btn-sm btn-primary" onClick={handleStartDraft}>
                  ▶ Iniciar Draft Agora
                </button>
              )}

              <button
                className="btn btn-sm btn-purple"
                onClick={handleRandomizeOrder}
                disabled={roomState.picks_history.length > 0}
                title="Embaralha aleatoriamente a ordem dos times de 1 a N"
              >
                🎲 Sortear Ordem do Draft
              </button>

              {(roomState.status === "active" || roomState.status === "paused") && (
                <button className="btn btn-sm btn-secondary" onClick={handleTogglePause}>
                  {roomState.status === "paused" ? "▶ Retomar Relógio" : "⏸ Pausar Relógio"}
                </button>
              )}

              <button
                className="btn btn-sm btn-warning"
                onClick={handleUndoPick}
                disabled={roomState.picks_history.length === 0}
                title="Desfaz a última escolha e devolve o jogador para a lista"
              >
                ↩ Voltar Última Escolha ({roomState.picks_history.length})
              </button>

              <label
                className="pill-badge"
                style={{ cursor: "pointer", borderColor: commPickForOthers ? "var(--accent-neon)" : undefined }}
              >
                <input
                  type="checkbox"
                  checked={commPickForOthers}
                  onChange={(e) => setCommPickForOthers(e.target.checked)}
                />
                Escolher p/ Outros Managers
              </label>
            </div>
          </section>
        )}

        {/* FAIXA SUPERIOR: RELÓGIO + FITA SNAKE DRAFT */}
        <section className="clock-strip">
          <div
            className={`clock-card ${roomState.current_seat === mySeat ? "my-turn" : ""} ${
              roomState.status === "active" && roomState.remaining_seconds <= 10 ? "urgent" : ""
            }`}
          >
            <div className="clock-top">
              <div>
                <div className="brand-sub">
                  {roomState.status === "lobby"
                    ? "AGUARDANDO INÍCIO"
                    : roomState.status === "paused"
                    ? "⏸ DRAFT PAUSADO"
                    : roomState.status === "completed"
                    ? "DRAFT CONCLUÍDO"
                    : `NA VEZ: ASSENTO #${roomState.current_seat}`}
                </div>
                <div style={{ fontSize: "1.08rem", fontWeight: 800, marginTop: 4 }}>
                  {activeParticipant ? activeParticipant.display_name : "Finalizado"}
                </div>
              </div>
              <div className="clock-digits" style={{ color: clockColor }}>
                {roomState.remaining_seconds}s
              </div>
            </div>

            <div className="clock-progress-track">
              <div
                className="clock-progress-bar"
                style={{ width: `${clockPct}%`, backgroundColor: clockColor }}
              />
            </div>
          </div>

          {/* FITA SNAKE DRAFT */}
          <div className="snake-ribbon">
            {roomState.snake_order_preview.slice(0, roomState.num_teams * 2).map((seatNum, idx) => {
              const pickNumber = idx + 1;
              const donePick = roomState.picks_history.find((p) => p.overall_pick === pickNumber);
              const isCurrent = roomState.current_pick_index === idx && roomState.status === "active";
              const part = roomState.participants.find((p) => p.seat_number === seatNum);
              const rnd = Math.floor(idx / roomState.num_teams) + 1;
              const inRnd = (idx % roomState.num_teams) + 1;

              return (
                <div
                  key={pickNumber}
                  className={`snake-chip ${isCurrent ? "active" : ""} ${donePick ? "done" : ""}`}
                >
                  <div className="snake-chip-top">
                    <span>
                      R{rnd}.{String(inRnd).padStart(2, "0")} (#{pickNumber})
                    </span>
                    <span>Assento {seatNum}</span>
                  </div>
                  <div className="snake-chip-team">{part?.display_name || `Time ${seatNum}`}</div>
                  <div className="snake-chip-player">
                    {donePick
                      ? `${donePick.player.short_name} (${donePick.player.primary_position})`
                      : isCurrent
                      ? "⏱ Escolhendo..."
                      : "Aguardando"}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* WORKSPACE 3 COLUNAS */}
        <section className="arena-grid">
          {/* COLUNA 1: PARTICIPANTES & ORDEM DO DRAFT */}
          <aside className="glass-panel">
            <div className="panel-header">
              <div className="panel-title">👥 Ordem dos Times ({roomState.num_teams})</div>
            </div>

            <div className="seat-list">
              {roomState.participants.map((pt) => {
                const isTurn = roomState.current_seat === pt.seat_number && roomState.status === "active";
                const isInspected = inspectedSeat === pt.seat_number;
                return (
                  <div
                    key={pt.seat_number}
                    className={`seat-item ${isTurn ? "active-turn" : ""} ${
                      isInspected ? "selected-view" : ""
                    }`}
                    onClick={() => setInspectedSeat(pt.seat_number)}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <span className={`status-dot ${pt.is_online ? "online" : ""}`} />
                      <div>
                        <div style={{ fontWeight: 700, fontSize: "0.88rem" }}>
                          #{pt.seat_number} {pt.display_name}
                          {pt.seat_number === mySeat ? " (Você)" : ""}
                        </div>
                        <div style={{ fontSize: "0.73rem", color: "var(--text-muted)" }}>
                          {isTurn
                            ? "⏱ Na vez de escolher"
                            : pt.is_online
                            ? "Online"
                            : pt.claimed
                            ? "Reconectável"
                            : "Vago / Auto"}
                        </div>
                      </div>
                    </div>
                    <span className="pill-badge">
                      {pt.roster.length}/{roomState.rounds}
                    </span>
                  </div>
                );
              })}
            </div>
          </aside>

          {/* COLUNA 2: POOL DE JOGADORES & SLEEPER DRAFT BOARD */}
          <section className="glass-panel" style={{ overflow: "hidden" }}>
            <div className="panel-header">
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button
                  className={`btn btn-sm ${activeTab === "pool" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setActiveTab("pool")}
                >
                  ⚽ Jogadores Disponíveis ({roomState.available_players_count})
                </button>
                <button
                  className={`btn btn-sm ${activeTab === "board" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setActiveTab("board")}
                >
                  🟩 Draft Board Sleeper ({roomState.picks_history.length}/{roomState.total_picks})
                </button>
                <button
                  className={`btn btn-sm ${activeTab === "history" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setActiveTab("history")}
                >
                  📋 Lista Cronológica
                </button>
              </div>
            </div>

            {activeTab === "pool" && (
              <>
                <div className="filter-bar">
                  <input
                    className="input-field"
                    style={{ flex: 1, minWidth: 220 }}
                    placeholder="Buscar por nome, posição (GOL, ZAG, MC, ATA, ST...), clube ou país..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                  <div className="pos-pills">
                    {[
                      { id: "ALL", label: "TODOS" },
                      { id: "GK", label: "GK / GOL" },
                      { id: "DEF", label: "DEF / ZAG" },
                      { id: "MID", label: "MID / MEI" },
                      { id: "ATT", label: "ATT / ATA" },
                    ].map((sec) => (
                      <button
                        key={sec.id}
                        className={`pos-pill ${sectorFilter === sec.id ? "active" : ""}`}
                        onClick={() => setSectorFilter(sec.id)}
                      >
                        {sec.label}
                      </button>
                    ))}
                  </div>
                  <label style={{ fontSize: "0.78rem", color: "var(--text-muted)", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={hideDrafted}
                      onChange={(e) => setHideDrafted(e.target.checked)}
                      style={{ marginRight: 6 }}
                    />
                    Ocultar escolhidos
                  </label>
                </div>

                <div className="players-table-wrap">
                  {filteredPlayers.map((player) => {
                    const sec = getPlayerSector(player);
                    const isDrafted = draftedSet.has(player.player_id);
                    const inMyQueue = myParticipant?.queue.includes(player.player_id) || false;
                    const ovrClass =
                      player.overall >= 88
                        ? "ovr-walkout"
                        : player.overall >= 80
                        ? "ovr-gold"
                        : "ovr-silver";

                    const pickBtnLabel = isDrafted
                      ? "Escolhido"
                      : isStrictlyMyTurn
                      ? "DRAFTAR"
                      : canCommissionerForcePick && roomState.current_seat
                      ? `👑 P/ #${roomState.current_seat}`
                      : "Aguarde";

                    return (
                      <div key={player.player_id} className="player-row">
                        <div className={`ovr-badge ${ovrClass}`}>{player.overall}</div>

                        <div>
                          <div style={{ fontWeight: 800, fontSize: "0.95rem" }}>{player.short_name}</div>
                          <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginTop: 2 }}>
                            <span className={`pos-tag pos-${sec}`}>
                              {player.primary_position} ({sec})
                            </span>
                            {player.nationality}
                          </div>
                        </div>

                        <div>
                          <div style={{ fontWeight: 600, fontSize: "0.84rem" }}>{player.club_name}</div>
                          <div style={{ fontSize: "0.73rem", color: "var(--text-muted)" }}>
                            {player.league_name}
                          </div>
                        </div>

                        <div style={{ fontWeight: 700, fontSize: "0.84rem", color: "#cbd5e1" }}>
                          {formatEuro(player.value_eur)}
                        </div>

                        <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                          <button
                            className="btn btn-sm btn-secondary"
                            onClick={() => handleToggleQueue(player.player_id)}
                            disabled={isDrafted || !mySeat}
                            title="Adicionar/Remover da sua fila de Auto-Draft"
                          >
                            {inMyQueue ? "★ Fila" : "+ Fila"}
                          </button>
                          <button
                            className="btn btn-sm btn-primary"
                            disabled={isDrafted || !canPickNow}
                            onClick={() => handleMakePick(player.player_id)}
                            title={
                              canCommissionerForcePick && !isStrictlyMyTurn
                                ? `Escolher como Comissário para o Assento #${roomState.current_seat}`
                                : undefined
                            }
                          >
                            {pickBtnLabel}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            )}

            {/* ABA 2: DRAFT BOARD MATRICIAL ESTILO SLEEPER COLORIDO POR POSIÇÃO */}
            {activeTab === "board" && (
              <>
                <div className="sleeper-legend">
                  <span style={{ color: "var(--text-muted)" }}>Cores por Posição (Padrão Sleeper):</span>
                  <span className="legend-item">
                    <span className="legend-swatch" style={{ background: "#f59e0b" }} />
                    GK / GOL (Goleiro)
                  </span>
                  <span className="legend-item">
                    <span className="legend-swatch" style={{ background: "#3b82f6" }} />
                    DEF / ZAG / LE / LD (Defesa)
                  </span>
                  <span className="legend-item">
                    <span className="legend-swatch" style={{ background: "#10b981" }} />
                    MID / VOL / MC / MEI (Meio-Campo)
                  </span>
                  <span className="legend-item">
                    <span className="legend-swatch" style={{ background: "#f43f5e" }} />
                    ATT / PE / PD / ATA (Ataque)
                  </span>
                </div>

                <div className="sleeper-board-wrapper">
                  <div
                    className="sleeper-grid"
                    style={{
                      gridTemplateColumns: `repeat(${roomState.num_teams}, minmax(148px, 1fr))`,
                    }}
                  >
                    {/* CABEÇALHOS DAS COLUNAS (ASSENTOS 1..N) */}
                    {roomState.participants.map((pt) => {
                      const isTurn =
                        roomState.current_seat === pt.seat_number && roomState.status === "active";
                      return (
                        <div
                          key={`header-${pt.seat_number}`}
                          className={`sleeper-col-header ${isTurn ? "active-col" : ""}`}
                        >
                          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                            ASSENTO #{pt.seat_number}
                          </div>
                          <div
                            style={{
                              fontWeight: 800,
                              fontSize: "0.86rem",
                              whiteSpace: "nowrap",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              marginTop: 2,
                            }}
                          >
                            {pt.display_name}
                          </div>
                        </div>
                      );
                    })}

                    {/* CÉLULAS DE CADA RODADA (1..ROUNDS) POR COLUNA DE TIME (1..NUM_TEAMS) */}
                    {Array.from({ length: roomState.rounds }, (_, rIdx) => {
                      const roundNum = rIdx + 1;
                      const isOddRound = roundNum % 2 === 1;

                      return roomState.participants.map((pt) => {
                        const seatNum = pt.seat_number;
                        const pickInRound = isOddRound
                          ? seatNum
                          : roomState.num_teams - seatNum + 1;
                        const overallPick = (roundNum - 1) * roomState.num_teams + pickInRound;
                        const pickRec = picksByRoundAndSeat.get(`${roundNum}-${seatNum}`);
                        const isCurrentCell =
                          roomState.status === "active" &&
                          roomState.current_pick_index + 1 === overallPick;

                        if (pickRec) {
                          const sec = getPlayerSector(pickRec.player);
                          return (
                            <div
                              key={`cell-${roundNum}-${seatNum}`}
                              className={`sleeper-cell sector-${sec}`}
                            >
                              <div className="sleeper-cell-top">
                                <span>
                                  {roundNum}.{String(pickInRound).padStart(2, "0")} (#{overallPick})
                                </span>
                                <span className={`pos-tag pos-${sec}`} style={{ margin: 0 }}>
                                  {pickRec.player.primary_position} • {pickRec.player.overall}
                                </span>
                              </div>

                              <div className="sleeper-cell-name" title={pickRec.player.short_name}>
                                {pickRec.player.short_name}
                              </div>

                              <div className="sleeper-cell-meta">
                                <span>{pickRec.player.club_name}</span>
                                <span>
                                  {pickRec.source === "commissioner"
                                    ? "👑"
                                    : pickRec.source !== "manual"
                                    ? "⚡"
                                    : ""}
                                </span>
                              </div>
                            </div>
                          );
                        }

                        return (
                          <div
                            key={`cell-${roundNum}-${seatNum}`}
                            className={`sleeper-cell ${isCurrentCell ? "on-the-clock" : ""}`}
                          >
                            <div className="sleeper-cell-top" style={{ color: "var(--text-muted)" }}>
                              <span>
                                {roundNum}.{String(pickInRound).padStart(2, "0")} (#{overallPick})
                              </span>
                              <span>{isOddRound ? "→" : "←"}</span>
                            </div>
                            <div
                              style={{
                                fontSize: "0.78rem",
                                fontWeight: 700,
                                color: isCurrentCell ? "var(--accent-neon)" : "var(--text-muted)",
                                margin: "auto 0",
                              }}
                            >
                              {isCurrentCell ? "⏱ Na Vez..." : "Disponível"}
                            </div>
                            <div className="sleeper-cell-meta" style={{ opacity: 0.5 }}>
                              <span>{pt.display_name}</span>
                            </div>
                          </div>
                        );
                      });
                    })}
                  </div>
                </div>
              </>
            )}

            {/* ABA 3: LISTA CRONOLÓGICA DE ESCOLHAS */}
            {activeTab === "history" && (
              <div className="players-table-wrap">
                {roomState.picks_history.length === 0 ? (
                  <p style={{ color: "var(--text-muted)", padding: 20 }}>
                    Nenhuma escolha realizada ainda.
                  </p>
                ) : (
                  roomState.picks_history
                    .slice()
                    .reverse()
                    .map((rec) => {
                      const sec = getPlayerSector(rec.player);
                      return (
                        <div key={rec.overall_pick} className="player-row">
                          <div className="ovr-badge ovr-gold">#{rec.overall_pick}</div>
                          <div>
                            <div style={{ fontWeight: 800 }}>
                              {rec.player.short_name} ({rec.player.overall} OVR)
                            </div>
                            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                              <span className={`pos-tag pos-${sec}`}>
                                {rec.player.primary_position} ({sec})
                              </span>
                              {rec.player.club_name} • {rec.player.league_name}
                            </div>
                          </div>
                          <div>
                            <div style={{ fontWeight: 700, color: "var(--accent-neon)" }}>
                              #{rec.seat_number} {rec.participant_name}
                            </div>
                            <div style={{ fontSize: "0.73rem", color: "var(--text-muted)" }}>
                              Rodada {rec.round_number} • Pick {rec.pick_in_round}
                            </div>
                          </div>
                          <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                            {rec.source === "manual"
                              ? "Escolha Manual"
                              : rec.source === "commissioner"
                              ? "👑 Comissário"
                              : "⚡ Auto-Draft"}
                          </div>
                          <div />
                        </div>
                      );
                    })
                )}
              </div>
            )}
          </section>

          {/* COLUNA 3: ELENCO INSPECIONADO & FILA DE AUTO-DRAFT */}
          <aside className="glass-panel">
            <div className="panel-header">
              <div className="panel-title">
                🛡️ Elenco: {inspectedParticipant?.display_name || `Time #${inspectedSeat}`}
              </div>
            </div>

            {inspectedParticipant && (
              <>
                <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
                  {(["GK", "DEF", "MID", "ATT"] as const).map((sec) => {
                    const count = inspectedParticipant.roster.filter(
                      (p) => getPlayerSector(p) === sec
                    ).length;
                    return (
                      <span
                        key={sec}
                        className={`pos-tag pos-${sec}`}
                        style={{ fontSize: "0.76rem", padding: "4px 9px" }}
                      >
                        {sec}: {count}
                      </span>
                    );
                  })}
                </div>

                <div className="seat-list" style={{ maxHeight: 330, marginBottom: 18 }}>
                  {inspectedParticipant.roster.length === 0 ? (
                    <div style={{ fontSize: "0.83rem", color: "var(--text-muted)", padding: "8px 0" }}>
                      Nenhum jogador escolhido por este time ainda.
                    </div>
                  ) : (
                    inspectedParticipant.roster.map((p, idx) => {
                      const sec = getPlayerSector(p);
                      return (
                        <div key={p.player_id} className="seat-item" style={{ cursor: "default" }}>
                          <div>
                            <div style={{ fontWeight: 700, fontSize: "0.86rem" }}>
                              {idx + 1}. {p.short_name}
                            </div>
                            <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                              <span className={`pos-tag pos-${sec}`}>
                                {p.primary_position} ({sec})
                              </span>
                              {p.club_name}
                            </div>
                          </div>
                          <span className="pill-badge" style={{ color: "#fcd34d" }}>
                            {p.overall}
                          </span>
                        </div>
                      );
                    })
                  )}
                </div>
              </>
            )}

            {/* FILA DE AUTO-DRAFT DO USUÁRIO */}
            <div className="panel-header" style={{ marginTop: 8 }}>
              <div className="panel-title" style={{ fontSize: "0.95rem" }}>
                ★ Minha Fila de Prioridade ({myParticipant?.queue.length || 0})
              </div>
            </div>
            <div className="seat-list" style={{ maxHeight: 190 }}>
              {!myParticipant || myParticipant.queue.length === 0 ? (
                <div style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
                  Clique em "+ Fila" nos atletas para o Auto-Draft priorizá-los caso seu tempo acabe.
                </div>
              ) : (
                myParticipant.queue.map((pid, idx) => {
                  const pl = playersPool.find((x) => x.player_id === pid);
                  if (!pl) return null;
                  return (
                    <div key={pid} className="seat-item">
                      <div style={{ fontSize: "0.84rem", fontWeight: 700 }}>
                        {idx + 1}. {pl.short_name} ({pl.primary_position} • {pl.overall})
                      </div>
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={() => handleToggleQueue(pid)}
                      >
                        ✕
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </aside>
        </section>
      </main>
    </div>
  );
}

export default App;