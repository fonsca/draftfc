from __future__ import annotations

import asyncio
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.multiplayer.models import CreateRoomRequest, JoinRoomRequest, RoomConfig, RoomPlayer
from backend.multiplayer.room_manager import MultiplayerRoomManager


def _make_test_pool(count: int = 120) -> list[RoomPlayer]:
    positions = ["GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]
    return [
        RoomPlayer(
            player_id=1000 + i,
            short_name=f"Craque {i}",
            overall=95 - (i // 5),
            primary_position=positions[i % len(positions)],
            club_id=(i % 20) + 1,
            club_name=f"Club {(i % 20) + 1}",
            league_name=f"League {(i % 5) + 1}",
            nationality=f"Nation {(i % 10) + 1}",
            value_eur=100_000_000 - (i * 500_000),
        )
        for i in range(1, count + 1)
    ]


def test_race_condition_concurrent_pick() -> None:
    """
    Simula duas requisições clicando no mesmo jogador (1001) no exato mesmo milissegundo.
    Apenas uma deve suceder e a segunda deve receber erro estruturado sem duplicar o atleta.
    """
    async def _run_race() -> None:
        manager = MultiplayerRoomManager()
        room, comm_token = manager.create_room(
            CreateRoomRequest(
                config=RoomConfig(room_name="Sala Race", password="123", num_teams=4, rounds=2, pick_clock_seconds=45),
                commissioner_name="Player 1",
                custom_players=_make_test_pool(40),
            )
        )
        _, _, seat2_token = manager.join_room(
            room.room_code, JoinRoomRequest(display_name="Player 2", password="123", seat_number=2)
        )
        await manager.start_room(room.room_code, comm_token, start_background_clock=False)

        # Cenário A: Duas conexões do titular do turno (ou comissário) clicam no mesmo atleta no mesmo instante
        res1, res2 = await asyncio.gather(
            room.make_pick(comm_token, player_id=1001, request_id="req-A"),
            room.make_pick(comm_token, player_id=1001, request_id="req-B"),
        )
        outcomes = [res1, res2]
        successes = [r for r in outcomes if r[0] is True]
        failures = [r for r in outcomes if r[0] is False]

        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0][1]["event"] == "pick_error"
        assert failures[0][1]["code"] in ("PLAYER_ALREADY_DRAFTED", "OUT_OF_TURN")
        assert len(room.picks_history) == 1
        assert room.picks_history[0].player.player_id == 1001

        # Cenário B: No turno do Assento 2, Assento 2 e Comissário clicam simultaneamente no atleta 1002
        res_a, res_b = await asyncio.gather(
            room.make_pick(seat2_token, player_id=1002, request_id="req-C"),
            room.make_pick(comm_token, player_id=1002, request_id="req-D"),
        )
        assert [res_a[0], res_b[0]].count(True) == 1
        assert [res_a[0], res_b[0]].count(False) == 1
        assert len(room.picks_history) == 2

    asyncio.run(_run_race())


def test_snake_draft_16_teams_order_and_clock_reset() -> None:
    """
    Garante que o relógio reseta para o tempo pré-determinado a cada escolha e que a
    ordem Snake respeita estritamente 1..16 na 1ª rodada, 16..1 na 2ª e 1..16 na 3ª.
    """
    async def _run_snake() -> None:
        manager = MultiplayerRoomManager()
        room, comm_token = manager.create_room(
            CreateRoomRequest(
                config=RoomConfig(
                    room_name="Liga 16 Times",
                    password="fc26",
                    num_teams=16,
                    rounds=3,
                    pick_clock_seconds=30,
                ),
                commissioner_name="Time 1",
                custom_players=_make_test_pool(100),
            )
        )
        tokens: dict[int, str] = {1: comm_token}
        for seat in range(2, 17):
            _, _, tok = manager.join_room(
                room.room_code,
                JoinRoomRequest(display_name=f"Time {seat}", password="fc26", seat_number=seat),
            )
            tokens[seat] = tok

        await manager.start_room(room.room_code, comm_token, start_background_clock=False)

        # Rodada 1: Ordem deve ser 1 -> 16
        round1_seats: list[int] = []
        for idx, expected_seat in enumerate(range(1, 17), start=1):
            assert room.current_slot is not None
            assert room.current_slot[3] == expected_seat
            # Simula gasto parcial do relógio antes da escolha
            await room.step_clock(12)
            assert room.remaining_seconds == 18

            ok, payload = await room.make_pick(tokens[expected_seat], player_id=1000 + idx)
            assert ok is True
            round1_seats.append(payload["pick"]["seat_number"])
            # Relógio deve resetar imediatamente para 30s após a escolha
            assert room.remaining_seconds == 30

        assert round1_seats == list(range(1, 17))

        # Rodada 2: Ordem deve inverter para 16 -> 1 (Snake)
        round2_seats: list[int] = []
        for idx, expected_seat in enumerate(range(16, 0, -1), start=17):
            assert room.current_slot is not None
            assert room.current_slot[3] == expected_seat
            ok, payload = await room.make_pick(tokens[expected_seat], player_id=1000 + idx)
            assert ok is True
            round2_seats.append(payload["pick"]["seat_number"])
            assert room.remaining_seconds == 30

        assert round2_seats == list(range(16, 0, -1))

        # Rodada 3, Pick 33: Volta para o Assento 1; testa estouro de relógio (Auto-Draft + Reset)
        assert room.current_slot is not None
        assert room.current_slot[3] == 1
        timeout_ev = await room.step_clock(30)
        assert timeout_ev["event"] == "player_drafted"
        assert timeout_ev["pick"]["seat_number"] == 1
        assert timeout_ev["pick"]["source"] in ("autodraft_best", "autodraft_queue")
        assert room.remaining_seconds == 30
        assert room.current_slot[3] == 2

    asyncio.run(_run_snake())


def test_websocket_disconnect_and_reconnection_sync() -> None:
    """
    Desconecta um cliente no meio do draft, avança escolhas enquanto ele está offline,
    reconecta o cliente e verifica se ele recebe automaticamente o estado atualizado.
    """
    client = TestClient(app)

    create_resp = client.post(
        "/api/multiplayer/rooms",
        json={
            "commissioner_name": "Host",
            "config": {
                "room_name": "Sala Reconexão",
                "password": "secret",
                "num_teams": 2,
                "rounds": 2,
                "pick_clock_seconds": 60,
            },
            "custom_players": [p.model_dump() for p in _make_test_pool(20)],
        },
    )
    assert create_resp.status_code == 200
    room_data = create_resp.json()
    room_code = room_data["room_code"]
    host_token = room_data["seat_token"]

    # Senha incorreta deve ser bloqueada com HTTP 403
    bad_join = client.post(
        f"/api/multiplayer/rooms/{room_code}/join",
        json={"display_name": "Intruso", "password": "errada"},
    )
    assert bad_join.status_code == 403

    # Entrada válida do Participante 2
    join_resp = client.post(
        f"/api/multiplayer/rooms/{room_code}/join",
        json={"display_name": "Visitante", "password": "secret"},
    )
    assert join_resp.status_code == 200
    p2_token = join_resp.json()["seat_token"]

    # Inicia o draft sem loop infinito de background para controle determinístico no teste
    start_resp = client.post(
        f"/api/multiplayer/rooms/{room_code}/start?commissioner_token={host_token}&auto_clock=false"
    )
    assert start_resp.status_code == 200

    with client.websocket_connect(f"/ws/rooms/{room_code}?seat_token={host_token}") as ws_host:
        init_host = ws_host.receive_json()
        assert init_host["event"] == "room_state"

        # Participante 2 conecta e em seguida perde a conexão (sai do bloco with)
        with client.websocket_connect(f"/ws/rooms/{room_code}?seat_token={p2_token}") as ws_p2:
            init_p2 = ws_p2.receive_json()
            assert init_p2["event"] == "room_state"
            assert init_p2["seat_number"] == 2

        # Enquanto o Participante 2 está offline, o Host faz a escolha #1 (jogador 1001)
        ws_host.send_json({"action": "make_pick", "player_id": 1001, "request_id": "pick-1"})

        # Participante 2 reconecta usando seu seat_token e deve receber o estado atualizado automaticamente
        with client.websocket_connect(f"/ws/rooms/{room_code}?seat_token={p2_token}") as ws_p2_reconnected:
            synced = ws_p2_reconnected.receive_json()
            assert synced["event"] == "room_state"
            state = synced["state"]
            assert state["current_pick_index"] == 1
            assert state["current_seat"] == 2
            assert 1001 in state["drafted_player_ids"]
            assert len(state["picks_history"]) == 1
            assert state["picks_history"][0]["player"]["player_id"] == 1001
            assert state["participants"][1]["is_online"] is True


def test_list_and_delete_active_rooms() -> None:
    client = TestClient(app)

    create_resp = client.post(
        "/api/multiplayer/rooms",
        json={
            "commissioner_name": "Admin",
            "config": {
                "room_name": "Sala Lista Teste",
                "password": "senha",
                "num_teams": 4,
                "rounds": 2,
                "pick_clock_seconds": 30,
            },
            "custom_players": [p.model_dump() for p in _make_test_pool(10)],
        },
    )
    assert create_resp.status_code == 200
    data = create_resp.json()
    code = data["room_code"]
    comm_token = data["commissioner_token"]

    # 1. Visualizar salas ativas
    list_resp = client.get("/api/multiplayer/rooms")
    assert list_resp.status_code == 200
    rooms = list_resp.json()["rooms"]
    found = next((r for r in rooms if r["room_code"] == code), None)
    assert found is not None
    assert found["room_name"] == "Sala Lista Teste"
    assert found["has_password"] is True
    assert found["claimed_seats"] == 1

    # 2. Exclusão com token inválido deve falhar com 403
    del_bad = client.delete(f"/api/multiplayer/rooms/{code}?commissioner_token=token_invalido")
    assert del_bad.status_code == 403

    # 3. Exclusão com token de comissário válido
    del_ok = client.delete(f"/api/multiplayer/rooms/{code}?commissioner_token={comm_token}")
    assert del_ok.status_code == 200
    assert del_ok.json()["deleted"] is True

    # 4. Verificar se a sala realmente sumiu da listagem
    after_list = client.get("/api/multiplayer/rooms").json()["rooms"]
    assert all(r["room_code"] != code for r in after_list)
