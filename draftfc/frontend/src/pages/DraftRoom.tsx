import React, { useCallback, useEffect, useState } from "react";
import { localAdapter } from "../adapters/localAdapter";
import { CommissionerBar } from "../components/CommissionerBar";
import { DraftBoard } from "../components/DraftBoard";
import { PlayerPool } from "../components/PlayerPool";
import { RosterTracker } from "../components/RosterTracker";
import { RoomStateSnapshot } from "../types/draft";

interface DraftRoomProps {
  initialRoom: RoomStateSnapshot;
  onExit: () => void;
}

export const DraftRoom: React.FC<DraftRoomProps> = ({ initialRoom, onExit }) => {
  const [room, setRoom] = useState<RoomStateSnapshot>(initialRoom);
  const [showBoard, setShowBoard] = useState(true);
  const [followTurn, setFollowTurn] = useState(true);
  const [selectedParticipant, setSelectedParticipant] = useState<string>(
    initialRoom.current_slot?.participant_id ?? initialRoom.ordered_participants[0]
  );
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (followTurn && room.current_slot) {
      setSelectedParticipant(room.current_slot.participant_id);
    }
  }, [followTurn, room.current_slot]);

  const notify = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 4000);
  };

  const runAction = async (fn: () => Promise<RoomStateSnapshot>) => {
    setBusy(true);
    try {
      const updated = await fn();
      setRoom(updated);
    } catch (err: any) {
      notify(`⚠ ${err.message}`);
      const refreshed = await localAdapter.getRoom(room.room_id).catch(() => null);
      if (refreshed) setRoom(refreshed);
    } finally {
      setBusy(false);
    }
  };

  const handleClockExpired = useCallback(
    (expectedOverallPick: number) => {
      localAdapter
        .autoPick(room.room_id, expectedOverallPick)
        .then((updated) => {
          setRoom(updated);
          const last = updated.picks_history[updated.picks_history.length - 1];
          if (last) notify(`⏰ Tempo esgotado! Auto-Draft escolheu ${last.player_short_name} para ${last.participant_id}.`);
        })
        .catch(() => null);
    },
    [room.room_id]
  );

  const handleMoveQueue = (uid: string, index: number, dir: -1 | 1) => {
    const currentIds = room.rosters[uid].queue.map((q) => q.player_id);
    const target = index + dir;
    if (target < 0 || target >= currentIds.length) return;
    [currentIds[index], currentIds[target]] = [currentIds[target], currentIds[index]];
    runAction(() => localAdapter.reorderQueue(room.room_id, uid, currentIds));
  };

  return (
    <div className="draft-room-layout">
      <CommissionerBar
        room={room}
        busy={busy}
        showBoard={showBoard}
        onToggleBoard={() => setShowBoard((v) => !v)}
        onPauseResume={() =>
          runAction(() =>
            room.status === "paused"
              ? localAdapter.resumeRoom(room.room_id)
              : localAdapter.pauseRoom(room.room_id)
          )
        }
        onResetClock={() => runAction(() => localAdapter.resetClock(room.room_id))}
        onAutoPickNow={() => {
          if (!room.current_slot) return;
          runAction(() => localAdapter.autoPick(room.room_id, room.current_slot!.overall_pick));
        }}
        onUndo={() => runAction(() => localAdapter.undoLastPick(room.room_id))}
        onExitRoom={onExit}
        onClockExpired={handleClockExpired}
      />

      {toast && <div className="toast-banner">{toast}</div>}

      {showBoard && (
        <DraftBoard
          room={room}
          selectedParticipant={selectedParticipant}
          onSelectParticipant={(uid) => {
            setFollowTurn(false);
            setSelectedParticipant(uid);
          }}
        />
      )}

      <main className="draft-workspace">
        <PlayerPool
          room={room}
          queueTargetUser={selectedParticipant}
          busy={busy}
          onDraftPlayer={(pid) => {
            if (!room.current_slot) return;
            runAction(() => localAdapter.makePick(room.room_id, pid, room.current_slot!.overall_pick));
          }}
          onQueuePlayer={(uid, pid) =>
            runAction(() => localAdapter.enqueuePlayer(room.room_id, uid, pid))
          }
        />

        <RosterTracker
          room={room}
          selectedParticipant={selectedParticipant}
          followTurn={followTurn}
          onSelectParticipant={setSelectedParticipant}
          onToggleFollowTurn={setFollowTurn}
          onToggleAutoPick={(uid, en) =>
            runAction(() => localAdapter.setAutoPickMode(room.room_id, uid, en))
          }
          onRemoveQueue={(uid, pid) =>
            runAction(() => localAdapter.removeFromQueue(room.room_id, uid, pid))
          }
          onMoveQueue={handleMoveQueue}
        />
      </main>
    </div>
  );
};