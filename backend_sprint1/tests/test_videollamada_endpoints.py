"""
Tests de integración HTTP del módulo de videollamada: flujo feliz completo,
rechazo, permisos cruzados y configuración de ICE servers. La sala se monta
vía la API de Birdgt (tutor inicia → estudiante acepta) para reproducir el
mismo camino que usa el cliente real.
"""

import pytest


def _auth(sesion: dict) -> dict:
    return {"Authorization": f"Bearer {sesion['token']}"}


@pytest.fixture
def sala_activa(client, tutor, estudiante):
    """Devuelve (sala_id, tutor, estudiante) con la sala en estado 'activa'."""
    auth_t = _auth(tutor)
    auth_e = _auth(estudiante)
    clase = client.post("/api/clases", headers=auth_t, json={
        "nombre": "Mate", "materia": "Mate",
        "descripcion": "", "es_privada": False,
    }).json()
    assert client.post("/api/inscripciones", headers=auth_e, json={
        "codigo_clase": clase["codigo_clase"],
    }).status_code == 201
    # Tutor inicia Birdgt → crea sala pendiente con el estudiante.
    r = client.post(f"/api/clases/{clase['id']}/birdgt", headers=auth_t)
    assert r.status_code == 201, r.text
    salas = r.json()["salas"]
    assert len(salas) == 1
    sala_id = salas[0]["id"]
    # Estudiante acepta → la sala pasa a 'activa'.
    r = client.post(f"/api/birdgt/{sala_id}/aceptar", headers=auth_e)
    assert r.status_code == 200, r.text
    return sala_id, tutor, estudiante


def test_flujo_feliz_iniciar_aceptar_finalizar(client, sala_activa):
    sala_id, tutor, estudiante = sala_activa
    auth_t, auth_e = _auth(tutor), _auth(estudiante)

    # Iniciar
    r = client.post("/api/video/sesiones", headers=auth_t, json={
        "sala_id": sala_id, "modo": "camara",
    })
    assert r.status_code == 201, r.text
    sesion = r.json()
    assert sesion["estado"] == "solicitada"
    assert sesion["iniciador_id"] == tutor["id"]
    assert sesion["receptor_id"] == estudiante["id"]
    sesion_id = sesion["id"]

    # GET activa devuelve la sesión viva.
    r = client.get("/api/video/sesiones/activa", headers=auth_t, params={"sala_id": sala_id})
    assert r.status_code == 200
    assert r.json()["id"] == sesion_id

    # Aceptar (receptor)
    r = client.post(f"/api/video/sesiones/{sesion_id}/aceptar", headers=_auth(estudiante))
    assert r.status_code == 200
    assert r.json()["estado"] == "aceptada"
    assert r.json()["aceptada_en"] is not None

    # Finalizar (cualquiera)
    r = client.post(f"/api/video/sesiones/{sesion_id}/finalizar", headers=auth_t)
    assert r.status_code == 200
    assert r.json()["estado"] == "finalizada"

    # Ya no hay sesión activa para la sala → 204.
    r = client.get("/api/video/sesiones/activa", headers=auth_t, params={"sala_id": sala_id})
    assert r.status_code == 204


def test_no_se_puede_iniciar_dos_veces(client, sala_activa):
    sala_id, tutor, _ = sala_activa
    auth_t = _auth(tutor)
    r = client.post("/api/video/sesiones", headers=auth_t, json={"sala_id": sala_id, "modo": "camara"})
    assert r.status_code == 201
    r = client.post("/api/video/sesiones", headers=auth_t, json={"sala_id": sala_id, "modo": "camara"})
    assert r.status_code == 400


def test_iniciador_no_puede_aceptar_su_propia_llamada(client, sala_activa):
    sala_id, tutor, _ = sala_activa
    auth_t = _auth(tutor)
    r = client.post("/api/video/sesiones", headers=auth_t, json={"sala_id": sala_id, "modo": "camara"})
    sesion_id = r.json()["id"]
    r = client.post(f"/api/video/sesiones/{sesion_id}/aceptar", headers=auth_t)
    assert r.status_code == 403


def test_rechazo_por_receptor(client, sala_activa):
    sala_id, tutor, estudiante = sala_activa
    r = client.post("/api/video/sesiones", headers=_auth(tutor), json={"sala_id": sala_id, "modo": "camara"})
    sesion_id = r.json()["id"]
    r = client.post(f"/api/video/sesiones/{sesion_id}/rechazar", headers=_auth(estudiante))
    assert r.status_code == 200
    assert r.json()["estado"] == "rechazada"
    # Ya no se puede aceptar lo rechazado.
    r = client.post(f"/api/video/sesiones/{sesion_id}/aceptar", headers=_auth(estudiante))
    assert r.status_code == 400


def test_endpoint_sin_token_es_401(client, sala_activa):
    sala_id, *_ = sala_activa
    r = client.post("/api/video/sesiones", json={"sala_id": sala_id, "modo": "camara"})
    assert r.status_code == 401


def test_usuario_externo_no_puede_iniciar_en_sala_ajena(client, sala_activa):
    sala_id, *_ = sala_activa
    # Tercer usuario no inscrito en la clase ni participante en la sala.
    client.post("/api/registro", json={
        "nombre_completo": "Externo", "codigo": "9",
        "correo_electronico": "ext@x.com", "usuario_login": "externo",
        "password": "password-segura", "tipo_cuenta": "estudiante",
    })
    externo = client.post("/api/login", json={
        "usuario_login": "externo", "password": "password-segura",
    }).json()
    r = client.post("/api/video/sesiones", headers=_auth(externo), json={
        "sala_id": sala_id, "modo": "camara",
    })
    assert r.status_code == 403


def test_usuario_externo_no_puede_aceptar_sesion_ajena(client, sala_activa):
    sala_id, tutor, _ = sala_activa
    r = client.post("/api/video/sesiones", headers=_auth(tutor), json={"sala_id": sala_id, "modo": "camara"})
    sesion_id = r.json()["id"]
    client.post("/api/registro", json={
        "nombre_completo": "Externo", "codigo": "9",
        "correo_electronico": "ext@x.com", "usuario_login": "externo",
        "password": "password-segura", "tipo_cuenta": "estudiante",
    })
    externo = client.post("/api/login", json={
        "usuario_login": "externo", "password": "password-segura",
    }).json()
    r = client.post(f"/api/video/sesiones/{sesion_id}/aceptar", headers=_auth(externo))
    assert r.status_code == 403


def test_get_activa_204_cuando_no_hay(client, sala_activa):
    sala_id, tutor, _ = sala_activa
    r = client.get("/api/video/sesiones/activa", headers=_auth(tutor), params={"sala_id": sala_id})
    assert r.status_code == 204


def test_config_devuelve_ice_servers_con_stun_por_defecto(client, auth):
    r = client.get("/api/video/config", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert "ice_servers" in data
    assert len(data["ice_servers"]) >= 1
    urls = data["ice_servers"][0]["urls"]
    assert any("stun" in u for u in urls)


def test_config_incluye_turn_si_envvars_presentes(client, auth, monkeypatch):
    monkeypatch.setenv("BRIDGETS_TURN_URL", "turn:turn.example.com:3478")
    monkeypatch.setenv("BRIDGETS_TURN_USER", "u")
    monkeypatch.setenv("BRIDGETS_TURN_CRED", "c")
    r = client.get("/api/video/config", headers=auth)
    assert r.status_code == 200
    data = r.json()
    turn = [s for s in data["ice_servers"] if s.get("username") == "u"]
    assert len(turn) == 1
    assert turn[0]["credential"] == "c"
    assert turn[0]["urls"] == ["turn:turn.example.com:3478"]


def test_sesion_inexistente_es_404(client, auth):
    r = client.post("/api/video/sesiones/9999/aceptar", headers=auth)
    assert r.status_code == 404
