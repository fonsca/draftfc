import React from "react";
import { RecordedPick, RoomStateSnapshot } from "../types/draft";

interface DraftBoardProps {
  room: RoomStateSnapshot;
  selectedParticipant: string;
  onSelectParticipant: (uid: string) => void;
}

export const DraftBoard: React.FC<DraftBoardProps> = ({
  room,
  selectedParticipant,
  onSelectParticipant,
}) => {
  const cols = room.ordered_participants;
  const rounds = Array.from({ length: room.rules.roster_size }, (_, i) => i + 1);

  const pickByParticipantRound = new Map<string, RecordedPick>();
  for (const pk of room.picks_history) {
    pickByParticipantRound.set(`${pk.participant_id}:${pk.round_number}`, pk);
  }

  const isReversedRound = (rnd: number): boolean => {
    const fmt = room.rules.draft_format;
    if (fmt === "linear") return false;
    if (fmt === "snake") return rnd % 2 === 0;
    if (rnd === 1) return false;
    if (rnd === 2 || rnd === 3) return true;
    return rnd % 2 === 1;
  };

  return (
    <section className="draft-board-wrapper">
      <div
        className="draft-board-grid"
        style={{ gridTemplateColumns: `56px repeat(${cols.length}, minmax(145px, 1fr))` }}
      >
        <div className="board-corner">ROD</div>
        {cols.map((uid, idx) => {
          const isCurrent = room.current_slot?.participant_id === uid;
          const isFocused = selectedParticipant === uid;
          return (
            <div
              key={uid}
              onClick={() => onSelectParticipant(uid)}
              className={`board-col-header ${isCurrent ? "col-active" : ""} ${isFocused ? "col-focused" : ""}`}
            >
              <span className="col-order">#{idx + 1}</span>
              <span className="col-name">{uid}</span>
              {room.rosters[uid]?.auto_pick_enabled && <span className="badge-afk">AFK</span>}
            </div>
          );
        })}

        {rounds.map((rnd) => {
          const reversed = isReversedRound(rnd);
          return (
            <React.Fragment key={rnd}>
              <div className="board-round-label">
                R{rnd} <span>{reversed ? "←" : "→"}</span>
              </div>
              {cols.map((uid, colIdx) => {
                const pk = pickByParticipantRound.get(`${uid}:${rnd}`);
                const pickInRnd = reversed ? cols.length - colIdx : colIdx + 1;
                const isCurrentTurn =
                  room.current_slot?.round_number === rnd &&
                  room.current_slot?.participant_id === uid;

                if (pk) {
                  return (
                    <div key={`${uid}-${rnd}`} className={`board-cell cell-picked sector-${pk.sector.toLowerCase()}`}>
                      <div className="cell-top">
                        <span className="cell-pick-code">{rnd}.{String(pk.pick_in_round).padStart(2, "0")}</span>
                        <span className="cell-ovr">{pk.overall}</span>
                      </div>
                      <div className="cell-player">{pk.player_short_name}</div>
                      <div className="cell-meta">
                        {pk.sector} • {pk.club_name} {pk.auto_drafted ? "⚡" : ""}
                      </div>
                    </div>
                  );
                }

                return (
                  <div key={`${uid}-${rnd}`} className={`board-cell cell-empty ${isCurrentTurn ? "cell-on-clock" : ""}`}>
                    <span className="cell-pick-code">{rnd}.{String(pickInRnd).padStart(2, "0")}</span>
                    {isCurrentTurn && <span className="on-clock-dot">ESCOLHENDO...</span>}
                  </div>
                );
              })}
            </React.Fragment>
          );
        })}
      </div>
    </section>
  );
};