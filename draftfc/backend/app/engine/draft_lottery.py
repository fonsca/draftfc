import hashlib
import random
import secrets
from typing import List, Optional, Sequence
from pydantic import BaseModel
from backend.app.engine.models import DraftFormat


class PickSlot(BaseModel):
    overall_pick: int
    round_number: int
    pick_in_round: int
    participant_id: str


class LotteryReceipt(BaseModel):
    ordered_participant_ids: List[str]
    seed_used: str
    verification_hash: str


def draw_draft_order(
    participant_ids: Sequence[str],
    seed: Optional[str] = None,
) -> LotteryReceipt:
    unique_sorted = sorted(set(p.strip() for p in participant_ids if p.strip()))
    if len(unique_sorted) < 2:
        raise ValueError("O sorteio do draft exige pelo menos 2 participantes únicos.")

    effective_seed = seed.strip() if seed and seed.strip() else secrets.token_hex(16)
    canonical_input = f"{effective_seed}|{'|'.join(unique_sorted)}"
    digest = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()

    rng = random.Random(int(digest, 16))
    shuffled = list(unique_sorted)
    rng.shuffle(shuffled)

    return LotteryReceipt(
        ordered_participant_ids=shuffled,
        seed_used=effective_seed,
        verification_hash=digest,
    )


def build_pick_schedule(
    ordered_participant_ids: Sequence[str],
    roster_size: int,
    draft_format: DraftFormat = DraftFormat.SNAKE,
) -> List[PickSlot]:
    if len(ordered_participant_ids) < 2:
        raise ValueError("São necessários pelo menos 2 participantes para gerar os turnos.")

    schedule: List[PickSlot] = []
    overall = 1

    for rnd in range(1, roster_size + 1):
        if draft_format == DraftFormat.LINEAR:
            round_order = list(ordered_participant_ids)
        elif draft_format == DraftFormat.SNAKE:
            round_order = (
                list(ordered_participant_ids)
                if rnd % 2 == 1
                else list(reversed(ordered_participant_ids))
            )
        elif draft_format == DraftFormat.THIRD_ROUND_REVERSAL:
            if rnd == 1:
                round_order = list(ordered_participant_ids)
            elif rnd in (2, 3):
                round_order = list(reversed(ordered_participant_ids))
            else:
                round_order = (
                    list(ordered_participant_ids)
                    if rnd % 2 == 0
                    else list(reversed(ordered_participant_ids))
                )
        else:
            raise ValueError(f"Formato desconhecido: {draft_format}")

        for idx, pid in enumerate(round_order, start=1):
            schedule.append(
                PickSlot(
                    overall_pick=overall,
                    round_number=rnd,
                    pick_in_round=idx,
                    participant_id=pid,
                )
            )
            overall += 1

    return schedule