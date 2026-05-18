"""
Tests e2e de la señalización de videollamada sobre `/ws/chat`. Abrimos dos
WebSockets simultáneos (uno por cada peer) con `TestClient.websocket_connect`,
intercambiamos `webrtc_offer/answer/ice` y verificamos que el backend reenvía
verbatim al otro extremo, marca la sesión como `activa` al ver el primer
offer y notifica `video_finalizada` cuando alguien envía `video_colgar`.

El backend nunca interpreta el SDP/ICE: usamos cadenas dummy.
"""

import pytest


def _auth(sesion: dict) -> dict:
    return {"Authorization": f"Bearer {sesion['token']}"}


@pytest.fixture
def sala_y_sesion(client, tutor, estudiante):
    """Crea sala activa + sesión de video ya aceptada (lista para señalizar)."""
    auth_t, auth_e = _auth(tutor), _auth(estudiante)
    clase = client.post("/api/clases", headers=auth_t, json={
        "nombre": "Mate", "materia": "Mate",
        "descripcion": "", "es_privada": False,
    }).json()
    client.post("/api/inscripciones", headers=auth_e, json={"codigo_clase": clase["codigo_clase"]})
    salas = client.post(f"/api/clases/{clase['id']}/birdgt", headers=auth_t).json()["salas"]
    sala_id = salas[0]["id"]
    client.post(f"/api/birdgt/{sala_id}/aceptar", headers=auth_e)
    sesion = client.post("/api/video/sesiones", headers=auth_t, json={
        "sala_id": sala_id, "modo": "camara",
    }).json()
    client.post(f"/api/video/sesiones/{sesion['id']}/aceptar", headers=auth_e)
    return sala_id, sesion["id"], tutor, estudiante


def _drenar_bienvenida(ws):
    """Consume el `{"tipo": "bienvenida"}` inicial del handshake."""
    msg = ws.receive_json()
    assert msg["tipo"] == "bienvenida"


def test_offer_se_reenvia_al_peer_y_marca_activa(client, sala_y_sesion):
    _, sesion_id, tutor, estudiante = sala_y_sesion
    with client.websocket_connect(f"/ws/chat?token={tutor['token']}") as ws_t, \
         client.websocket_connect(f"/ws/chat?token={estudiante['token']}") as ws_e:
        _drenar_bienvenida(ws_t)
        _drenar_bienvenida(ws_e)

        ws_t.send_json({
            "tipo": "webrtc_offer", "sesion_id": sesion_id,
            "sdp": "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\n", "type": "offer",
        })
        recibido = ws_e.receive_json()
        assert recibido["tipo"] == "webrtc_offer"
        assert recibido["sesion_id"] == sesion_id
        assert recibido["type"] == "offer"
        assert recibido["sdp"].startswith("v=0")

    # Tras el offer la sesión debe estar `activa`.
    r = client.get("/api/video/sesiones/activa",
                   headers=_auth(tutor), params={"sala_id": sala_y_sesion[0]})
    assert r.status_code == 200
    assert r.json()["estado"] == "activa"


def test_answer_e_ice_reenviados(client, sala_y_sesion):
    _, sesion_id, tutor, estudiante = sala_y_sesion
    with client.websocket_connect(f"/ws/chat?token={tutor['token']}") as ws_t, \
         client.websocket_connect(f"/ws/chat?token={estudiante['token']}") as ws_e:
        _drenar_bienvenida(ws_t)
        _drenar_bienvenida(ws_e)

        ws_e.send_json({
            "tipo": "webrtc_answer", "sesion_id": sesion_id,
            "sdp": "v=0 dummy answer", "type": "answer",
        })
        recibido = ws_t.receive_json()
        assert recibido["tipo"] == "webrtc_answer"
        assert recibido["sdp"] == "v=0 dummy answer"

        ws_t.send_json({
            "tipo": "webrtc_ice", "sesion_id": sesion_id,
            "candidate": "candidate:1 1 UDP 1 192.168.1.10 5000 typ host",
            "sdpMid": "0", "sdpMLineIndex": 0,
        })
        recibido = ws_e.receive_json()
        assert recibido["tipo"] == "webrtc_ice"
        assert recibido["candidate"].startswith("candidate:")
        assert recibido["sdpMid"] == "0"
        assert recibido["sdpMLineIndex"] == 0


def test_video_estado_reenvia_flags(client, sala_y_sesion):
    _, sesion_id, tutor, estudiante = sala_y_sesion
    with client.websocket_connect(f"/ws/chat?token={tutor['token']}") as ws_t, \
         client.websocket_connect(f"/ws/chat?token={estudiante['token']}") as ws_e:
        _drenar_bienvenida(ws_t)
        _drenar_bienvenida(ws_e)
        ws_t.send_json({
            "tipo": "video_estado", "sesion_id": sesion_id,
            "cam": False, "mic": True, "pantalla": False,
        })
        recibido = ws_e.receive_json()
        assert recibido == {
            "tipo": "video_estado", "sesion_id": sesion_id,
            "cam": False, "mic": True, "pantalla": False,
        }


def test_video_colgar_finaliza_y_notifica_al_peer(client, sala_y_sesion):
    sala_id, sesion_id, tutor, estudiante = sala_y_sesion
    # El iniciador (tutor) cuelga sin abrir su propio WS. El receptor (estudiante)
    # debe recibir `video_finalizada` por su WS. Esta forma evita la complicación
    # de comprobar la entrega al propio remitente dentro del TestClient sincrono.
    with client.websocket_connect(f"/ws/chat?token={estudiante['token']}") as ws_e, \
         client.websocket_connect(f"/ws/chat?token={tutor['token']}") as ws_t:
        _drenar_bienvenida(ws_e)
        _drenar_bienvenida(ws_t)
        ws_t.send_json({"tipo": "video_colgar", "sesion_id": sesion_id})
        msg = ws_e.receive_json()
        assert msg["tipo"] == "video_finalizada"
        assert msg["sesion"]["id"] == sesion_id
        assert msg["sesion"]["estado"] == "finalizada"
        assert msg["sesion"]["motivo_fin"] == "colgada"

    # GET activa ya no la ve.
    r = client.get("/api/video/sesiones/activa",
                   headers=_auth(tutor), params={"sala_id": sala_id})
    assert r.status_code == 204




def test_senal_de_sesion_ajena_devuelve_error(client, sala_y_sesion):
    _, sesion_id, tutor, _ = sala_y_sesion
    # Tercer usuario sin participación en la sesión.
    client.post("/api/registro", json={
        "nombre_completo": "Externo", "codigo": "9",
        "correo_electronico": "ext@x.com", "usuario_login": "externo",
        "password": "password-segura", "tipo_cuenta": "estudiante",
    })
    externo = client.post("/api/login", json={
        "usuario_login": "externo", "password": "password-segura",
    }).json()
    with client.websocket_connect(f"/ws/chat?token={externo['token']}") as ws_x:
        _drenar_bienvenida(ws_x)
        ws_x.send_json({
            "tipo": "webrtc_offer", "sesion_id": sesion_id,
            "sdp": "v=0", "type": "offer",
        })
        msg = ws_x.receive_json()
        assert msg["tipo"] == "error"
        assert "inaccesible" in msg["mensaje"].lower()


def test_senalizar_sin_sesion_id_es_error(client, tutor):
    with client.websocket_connect(f"/ws/chat?token={tutor['token']}") as ws:
        _drenar_bienvenida(ws)
        ws.send_json({"tipo": "webrtc_offer", "sdp": "v=0", "type": "offer"})
        msg = ws.receive_json()
        assert msg["tipo"] == "error"
        assert "sesion_id" in msg["mensaje"].lower()


def test_senalizar_sobre_sesion_no_aceptada_es_error(client, sala_y_sesion):
    sala_id, sesion_id_old, tutor, estudiante = sala_y_sesion
    # Cerramos la sesión anterior y creamos una nueva en estado `solicitada`.
    client.post(f"/api/video/sesiones/{sesion_id_old}/finalizar", headers=_auth(tutor))
    nueva = client.post("/api/video/sesiones", headers=_auth(tutor), json={
        "sala_id": sala_id, "modo": "camara",
    }).json()
    with client.websocket_connect(f"/ws/chat?token={tutor['token']}") as ws_t:
        _drenar_bienvenida(ws_t)
        ws_t.send_json({
            "tipo": "webrtc_offer", "sesion_id": nueva["id"],
            "sdp": "v=0", "type": "offer",
        })
        msg = ws_t.receive_json()
        assert msg["tipo"] == "error"
        assert "solicitada" in msg["mensaje"].lower()
