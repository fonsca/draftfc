from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from backend.multiplayer.models import CreateRoomRequest, JoinRoomRequest, PickActionRequest
from backend.multiplayer.room_manager import room_manager

router = APIRouter(tags=["multiplayer"])


@router.get("/api/multiplayer/rooms")
def list_multiplayer_rooms() -> dict:
    rooms = [r.model_dump() for r in room_manager.list_rooms()]
    return {
        "total": len(rooms),
        "rooms": rooms,
    }


@router.post("/api/multiplayer/rooms")
async def create_multiplayer_room(req: CreateRoomRequest) -> dict:
    room, token = room_manager.create_room(req)
    return {
        "room_code": room.room_code,
        "seat_number": 1,
        "seat_token": token,
        "commissioner_token": token,
        "state": room.public_state().model_dump(),
    }


@router.post("/api/multiplayer/rooms/{room_code}/join")
async def join_multiplayer_room(room_code: str, req: JoinRoomRequest) -> dict:
    try:
        room, seat, token = room_manager.join_room(room_code, req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await room.broadcast(
        {
            "event": "participant_joined",
            "room_code": room.room_code,
            "seat_number": seat,
            "display_name": room.participants[seat].display_name,
            "state": room.public_state().model_dump(),
        }
    )
    return {
        "room_code": room.room_code,
        "seat_number": seat,
        "seat_token": token,
        "commissioner_token": room.commissioner_token if seat == 1 else "",
        "state": room.public_state().model_dump(),
    }


@router.post("/api/multiplayer/rooms/{room_code}/start")
async def start_multiplayer_room(
    room_code: str,
    commissioner_token: str = Query(...),
    auto_clock: bool = Query(default=True),
) -> dict:
    try:
        room = await room_manager.start_room(room_code, commissioner_token, start_background_clock=auto_clock)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return {"room_code": room.room_code, "state": room.public_state().model_dump()}


@router.get("/api/multiplayer/rooms/{room_code}")
async def get_multiplayer_room_state(room_code: str) -> dict:
    try:
        room = room_manager.get_room(room_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"room_code": room.room_code, "state": room.public_state().model_dump()}


@router.get("/api/multiplayer/rooms/{room_code}/players")
async def get_multiplayer_room_players(room_code: str) -> dict:
    try:
        room = room_manager.get_room(room_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    players = [room.players_by_id[pid].model_dump() for pid in room.sorted_player_ids]
    return {
        "room_code": room.room_code,
        "total": len(players),
        "players": players,
    }


@router.get("/api/multiplayer/rooms/{room_code}/export/csv")
def export_room_rosters_csv(room_code: str):
    try:
        room = room_manager.get_room(room_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    import csv
    import io
    from fastapi.responses import Response

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "seat_number",
        "team_name",
        "slot_index",
        "role",
        "player_id",
        "short_name",
        "primary_position",
        "overall",
        "club_name",
        "league_name",
        "nationality",
        "value_eur",
    ])

    for seat in sorted(room.participants.keys()):
        p = room.participants[seat]
        for idx, pl in enumerate(p.roster):
            role = "XI" if idx < 11 else ("SUB" if idx < 18 else "RES")
            writer.writerow([
                seat,
                p.display_name,
                idx + 1,
                role,
                pl.player_id,
                pl.short_name,
                pl.primary_position,
                pl.overall,
                pl.club_name,
                pl.league_name,
                pl.nationality,
                pl.value_eur,
            ])

    filename = f"draftfc_elencos_{room.room_code.upper()}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/api/multiplayer/rooms/{room_code}")
async def delete_multiplayer_room(
    room_code: str,
    commissioner_token: str | None = Query(default=None),
    password: str | None = Query(default=None),
    force: bool = Query(default=False),
) -> dict:
    try:
        await room_manager.delete_room(
            room_code,
            commissioner_token=commissioner_token,
            password=password,
            force=force,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return {"deleted": True, "room_code": room_code.strip().upper()}


@router.post("/api/multiplayer/rooms/{room_code}/pick")
async def rest_make_pick(room_code: str, req: PickActionRequest) -> dict:
    try:
        room = room_manager.get_room(room_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ok, payload = await room.make_pick(req.seat_token, req.player_id, req.request_id)
    if not ok:
        raise HTTPException(status_code=409, detail=payload)
    return payload


@router.websocket("/ws/rooms/{room_code}")
async def room_websocket_endpoint(
    websocket: WebSocket,
    room_code: str,
    seat_token: str | None = Query(default=None),
) -> None:
    try:
        room = room_manager.get_room(room_code)
    except KeyError:
        await websocket.close(code=4004)
        return

    await websocket.accept()
    seat_number = room.token_to_seat.get(seat_token or "", 0)
    room.connections[websocket] = seat_number

    if seat_number in room.participants:
        room.participants[seat_number].is_online = True

    await websocket.send_json(
        {
            "event": "room_state",
            "room_code": room.room_code,
            "seat_number": seat_number,
            "state": room.public_state().model_dump(),
        }
    )

    if seat_number in room.participants:
        await room.broadcast(
            {
                "event": "participant_presence",
                "room_code": room.room_code,
                "seat_number": seat_number,
                "is_online": True,
            }
        )

    try:
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")

            if action == "make_pick":
                token = msg.get("seat_token") or seat_token or ""
                player_id = int(msg.get("player_id", 0))
                request_id = msg.get("request_id")
                ok, result = await room.make_pick(token, player_id, request_id=request_id)
                if not ok:
                    await websocket.send_json(result)

            elif action == "set_queue":
                token = msg.get("seat_token") or seat_token or ""
                target_seat = room.token_to_seat.get(token, 0)
                if target_seat in room.participants:
                    raw_ids = msg.get("player_ids", [])
                    room.participants[target_seat].queue = [
                        int(pid) for pid in raw_ids if int(pid) not in room.drafted_player_ids
                    ]
                    await websocket.send_json(
                        {
                            "event": "queue_updated",
                            "seat_number": target_seat,
                            "queue": room.participants[target_seat].queue,
                        }
                    )

            elif action == "sync_state":
                await websocket.send_json(
                    {
                        "event": "room_state",
                        "room_code": room.room_code,
                        "seat_number": seat_number,
                        "state": room.public_state().model_dump(),
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        room.connections.pop(websocket, None)
        if seat_number in room.participants:
            still_connected = any(s == seat_number for s in room.connections.values())
            if not still_connected:
                room.participants[seat_number].is_online = False
                await room.broadcast(
                    {
                        "event": "participant_presence",
                        "room_code": room.room_code,
                        "seat_number": seat_number,
                        "is_online": False,
                    }
                )

@router.post("/api/multiplayer/rooms/{room_code}/randomize-order")
async def randomize_multiplayer_room_order(
    room_code: str,
    commissioner_token: str = Query(default=""),
) -> dict:
    try:
        room = room_manager.get_room(room_code)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Sala não encontrada na memória (crie uma nova sala se o servidor reiniciou).",
        ) from exc

    ok, payload = await room.randomize_order(commissioner_token)
    if not ok:
        raise HTTPException(status_code=400, detail=payload.get("message", "Erro ao sortear ordem."))
    return payload


@router.post("/api/multiplayer/rooms/{room_code}/undo-pick")
async def undo_multiplayer_room_pick(
    room_code: str,
    commissioner_token: str = Query(default=""),
) -> dict:
    try:
        room = room_manager.get_room(room_code)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Sala não encontrada na memória (crie uma nova sala se o servidor reiniciou).",
        ) from exc

    ok, payload = await room.undo_last_pick(commissioner_token)
    if not ok:
        raise HTTPException(status_code=400, detail=payload.get("message", "Erro ao voltar escolha."))
    return payload


@router.post("/api/multiplayer/rooms/{room_code}/toggle-pause")
async def toggle_pause_multiplayer_room(
    room_code: str,
    commissioner_token: str = Query(default=""),
) -> dict:
    try:
        room = room_manager.get_room(room_code)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Sala não encontrada na memória (crie uma nova sala se o servidor reiniciou).",
        ) from exc

    ok, payload = await room.toggle_pause(commissioner_token)
    if not ok:
        raise HTTPException(status_code=400, detail=payload.get("message", "Erro ao pausar/retomar."))
    return payload