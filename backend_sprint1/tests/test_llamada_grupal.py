"""
Tests del módulo de llamadas grupales:
  - Solo tutor de la clase puede iniciar.
  - Solo una llamada activa por clase a la vez.
  - Cualquier miembro (inscrito o tutor) puede unirse; externos reciben 403.
  - Salir / finalizar funcionan idempotentemente.
  - Señalización mesh por WS reenvía a `destino_id` añadiendo `origen_id`.
"""

import pytest


def _auth(s):
    return {"Authorization": f"Bearer {s['token']}"}


def _crear_clase_con(tutor, *estudiantes, client):
    auth_t = _auth(tutor)
    clase = client.post("/api/clases", headers=auth_t, json={
        "nombre": "Mate", "materia": "Mate", "descripcion": "", "es_privada": False,
    }).json()
    for est in estudiantes:
        client.post("/api/inscripciones", headers=_auth(est),
                    json={"codigo_clase": clase["codigo_clase"]})
    return clase


def _drenar_bienvenida(ws):
    msg = ws.receive_json()
    assert msg["tipo"] == "bienvenida"


# ----------------- REST -----------------

def test_estudiante_no_puede_iniciar_grupal(client, tutor, estudiante):
    clase = _crear_clase_con(tutor, estudiante, client=client)
    r = client.post(
        f"/api/clases/{clase['id']}/llamada-grupal",
        headers=_auth(estudiante), json={"titulo": "Repaso"},
    )
    assert r.status_code == 403


def test_iniciar_unirse_y_finalizar(client, tutor, estudiante):
    clase = _crear_clase_con(tutor, estudiante, client=client)

    r = client.post(
        f"/api/clases/{clase['id']}/llamada-grupal",
        headers=_auth(tutor), json={"titulo": "Repaso"},
    )
    assert r.status_code == 201, r.text
    llamada = r.json()
    assert llamada["estado"] == "activa"
    assert llamada["iniciador_id"] == tutor["id"]
    # El iniciador ya cuenta como participante.
    assert any(p["usuario_id"] == tutor["id"] for p in llamada["participantes"])

    # GET activa devuelve la misma llamada.
    r = client.get(
        f"/api/clases/{clase['id']}/llamada-grupal/activa", headers=_auth(estudiante),
    )
    assert r.status_code == 200
    assert r.json()["id"] == llamada["id"]

    # Estudiante se une.
    r = client.post(f"/api/llamadas-grupales/{llamada['id']}/unirse", headers=_auth(estudiante))
    assert r.status_code == 200
    assert any(p["usuario_id"] == estudiante["id"] for p in r.json()["participantes"])

    # Tutor finaliza.
    r = client.post(f"/api/llamadas-grupales/{llamada['id']}/finalizar", headers=_auth(tutor))
    assert r.status_code == 200
    assert r.json()["estado"] == "finalizada"

    # GET activa ya no encuentra.
    r = client.get(
        f"/api/clases/{clase['id']}/llamada-grupal/activa", headers=_auth(estudiante),
    )
    assert r.status_code == 204


def test_no_se_puede_iniciar_dos_grupales_a_la_vez(client, tutor, estudiante):
    clase = _crear_clase_con(tutor, estudiante, client=client)
    r1 = client.post(
        f"/api/clases/{clase['id']}/llamada-grupal", headers=_auth(tutor), json={},
    )
    assert r1.status_code == 201
    r2 = client.post(
        f"/api/clases/{clase['id']}/llamada-grupal", headers=_auth(tutor), json={},
    )
    assert r2.status_code == 400


def test_externo_no_puede_unirse(client, tutor, estudiante):
    clase = _crear_clase_con(tutor, estudiante, client=client)
    llamada = client.post(
        f"/api/clases/{clase['id']}/llamada-grupal", headers=_auth(tutor), json={},
    ).json()
    # Externo no inscrito.
    client.post("/api/registro", json={
        "nombre_completo": "Externo", "codigo": "9",
        "correo_electronico": "ext@x.com", "usuario_login": "externo",
        "password": "password-segura", "tipo_cuenta": "estudiante",
    })
    externo = client.post("/api/login", json={
        "usuario_login": "externo", "password": "password-segura",
    }).json()
    r = client.post(f"/api/llamadas-grupales/{llamada['id']}/unirse", headers=_auth(externo))
    assert r.status_code == 403


def test_salir_es_idempotente(client, tutor, estudiante):
    clase = _crear_clase_con(tutor, estudiante, client=client)
    llamada = client.post(
        f"/api/clases/{clase['id']}/llamada-grupal", headers=_auth(tutor), json={},
    ).json()
    client.post(f"/api/llamadas-grupales/{llamada['id']}/unirse", headers=_auth(estudiante))
    # Dos `salir` seguidos no rompen nada.
    assert client.post(f"/api/llamadas-grupales/{llamada['id']}/salir", headers=_auth(estudiante)).status_code == 204
    assert client.post(f"/api/llamadas-grupales/{llamada['id']}/salir", headers=_auth(estudiante)).status_code == 204


def test_solo_iniciador_puede_finalizar(client, tutor, estudiante):
    clase = _crear_clase_con(tutor, estudiante, client=client)
    llamada = client.post(
        f"/api/clases/{clase['id']}/llamada-grupal", headers=_auth(tutor), json={},
    ).json()
    client.post(f"/api/llamadas-grupales/{llamada['id']}/unirse", headers=_auth(estudiante))
    r = client.post(f"/api/llamadas-grupales/{llamada['id']}/finalizar", headers=_auth(estudiante))
    assert r.status_code == 403


# ----------------- Señalización WS -----------------

def test_signal_grupal_reenvia_al_destino_con_origen(client, tutor, estudiante):
    """Un offer mesh enviado por el tutor con destino_id=estudiante llega al
    estudiante con `origen_id=tutor`."""
    clase = _crear_clase_con(tutor, estudiante, client=client)
    llamada = client.post(
        f"/api/clases/{clase['id']}/llamada-grupal", headers=_auth(tutor), json={},
    ).json()
    client.post(f"/api/llamadas-grupales/{llamada['id']}/unirse", headers=_auth(estudiante))

    with client.websocket_connect(f"/ws/chat?token={estudiante['token']}") as ws_e, \
         client.websocket_connect(f"/ws/chat?token={tutor['token']}") as ws_t:
        _drenar_bienvenida(ws_e)
        _drenar_bienvenida(ws_t)

        ws_t.send_json({
            "tipo": "grupal_offer",
            "llamada_id": llamada["id"],
            "destino_id": estudiante["id"],
            "sdp": "v=0 dummy", "type": "offer",
        })
        msg = ws_e.receive_json()
        assert msg["tipo"] == "grupal_offer"
        assert msg["origen_id"] == tutor["id"]
        assert msg["destino_id"] == estudiante["id"]
        assert msg["sdp"] == "v=0 dummy"


def test_signal_grupal_rechaza_si_no_participo(client, tutor, estudiante):
    """Quien no está dentro de la llamada no puede usar la señalización
    grupal (devuelve error por WS)."""
    clase = _crear_clase_con(tutor, estudiante, client=client)
    llamada = client.post(
        f"/api/clases/{clase['id']}/llamada-grupal", headers=_auth(tutor), json={},
    ).json()
    with client.websocket_connect(f"/ws/chat?token={estudiante['token']}") as ws_e:
        _drenar_bienvenida(ws_e)
        ws_e.send_json({
            "tipo": "grupal_offer",
            "llamada_id": llamada["id"],
            "destino_id": tutor["id"],
            "sdp": "v=0", "type": "offer",
        })
        msg = ws_e.receive_json()
        assert msg["tipo"] == "error"
        assert "participas" in msg["mensaje"].lower()
