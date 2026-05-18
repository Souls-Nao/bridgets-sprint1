"""
Tests del sistema opt-in de suscripciones a anuncios:
  - Toggle suscripción (POST/DELETE + GET de estado).
  - Aislamiento entre usuarios: la suscripción de A no afecta a B.
  - Crear un comentario solo notifica a los suscriptos (no al autor, no a
    quien no se suscribió).
  - Persistencia de notificación `video_solicitud` al iniciar una llamada.
"""

import pytest


def _auth(s):
    return {"Authorization": f"Bearer {s['token']}"}


def _crear_clase_con_anuncio(client, tutor, estudiante):
    auth_t = _auth(tutor)
    auth_e = _auth(estudiante)
    clase = client.post("/api/clases", headers=auth_t, json={
        "nombre": "Mate", "materia": "Mate", "descripcion": "", "es_privada": False,
    }).json()
    client.post("/api/inscripciones", headers=auth_e, json={"codigo_clase": clase["codigo_clase"]})
    anuncio = client.post(
        f"/api/clases/{clase['id']}/anuncios", headers=auth_t,
        json={"titulo": "Aviso", "contenido": "Lean el capítulo 3"},
    ).json()
    return clase, anuncio


def test_toggle_suscripcion_anuncio(client, tutor, estudiante):
    auth_t = _auth(tutor)
    _, anuncio = _crear_clase_con_anuncio(client, tutor, estudiante)

    # Por defecto: no suscrito.
    r = client.get(f"/api/anuncios/{anuncio['id']}/suscripcion", headers=auth_t)
    assert r.status_code == 200 and r.json() == {"suscrito": False}

    # Suscribirse (idempotente: dos POST → sigue suscrito una sola vez).
    assert client.post(f"/api/anuncios/{anuncio['id']}/suscripcion", headers=auth_t).status_code == 204
    assert client.post(f"/api/anuncios/{anuncio['id']}/suscripcion", headers=auth_t).status_code == 204
    r = client.get(f"/api/anuncios/{anuncio['id']}/suscripcion", headers=auth_t)
    assert r.json() == {"suscrito": True}

    # Desuscribirse (idempotente).
    assert client.delete(f"/api/anuncios/{anuncio['id']}/suscripcion", headers=auth_t).status_code == 204
    assert client.delete(f"/api/anuncios/{anuncio['id']}/suscripcion", headers=auth_t).status_code == 204
    r = client.get(f"/api/anuncios/{anuncio['id']}/suscripcion", headers=auth_t)
    assert r.json() == {"suscrito": False}


def test_comentario_solo_notifica_a_suscriptos(client, tutor, estudiante):
    auth_t = _auth(tutor)
    auth_e = _auth(estudiante)
    _, anuncio = _crear_clase_con_anuncio(client, tutor, estudiante)

    # Tutor se suscribe; estudiante NO.
    client.post(f"/api/anuncios/{anuncio['id']}/suscripcion", headers=auth_t)

    # Estudiante comenta. El tutor debería recibir 1 notif `comentario_nuevo`;
    # el estudiante (autor) NO debería tener ninguna.
    r = client.post(
        f"/api/anuncios/{anuncio['id']}/comentarios", headers=auth_e,
        json={"contenido": "Gracias profe"},
    )
    assert r.status_code == 201, r.text

    notifs_tutor = client.get("/api/notificaciones", headers=auth_t).json()
    tipos_tutor = [n["tipo"] for n in notifs_tutor]
    assert tipos_tutor.count("comentario_nuevo") == 1

    notifs_est = client.get("/api/notificaciones", headers=auth_e).json()
    assert "comentario_nuevo" not in [n["tipo"] for n in notifs_est]


def test_comentario_propio_no_notifica_al_autor_aunque_este_suscrito(client, tutor, estudiante):
    """El autor del comentario nunca recibe una notif por su propio comentario."""
    auth_t = _auth(tutor)
    _, anuncio = _crear_clase_con_anuncio(client, tutor, estudiante)
    client.post(f"/api/anuncios/{anuncio['id']}/suscripcion", headers=auth_t)

    client.post(
        f"/api/anuncios/{anuncio['id']}/comentarios", headers=auth_t,
        json={"contenido": "Yo mismo comento"},
    )
    notifs = client.get("/api/notificaciones", headers=auth_t).json()
    assert all(n["tipo"] != "comentario_nuevo" for n in notifs)


def test_video_solicitud_persiste_como_notificacion(client, tutor, estudiante):
    """Iniciar una videollamada debe dejar una notif persistente para el
    receptor con enlace_tipo=video_sesion."""
    auth_t = _auth(tutor)
    auth_e = _auth(estudiante)
    clase = client.post("/api/clases", headers=auth_t, json={
        "nombre": "Mate", "materia": "Mate", "descripcion": "", "es_privada": False,
    }).json()
    client.post("/api/inscripciones", headers=auth_e, json={"codigo_clase": clase["codigo_clase"]})
    salas = client.post(f"/api/clases/{clase['id']}/birdgt", headers=auth_t).json()["salas"]
    sala_id = salas[0]["id"]
    client.post(f"/api/birdgt/{sala_id}/aceptar", headers=auth_e)

    r = client.post(
        "/api/video/sesiones", headers=auth_t,
        json={"sala_id": sala_id, "modo": "camara"},
    )
    assert r.status_code == 201, r.text
    sesion_id = r.json()["id"]

    notifs = client.get("/api/notificaciones", headers=auth_e).json()
    video_notifs = [n for n in notifs if n["tipo"] == "video_solicitud"]
    assert len(video_notifs) == 1
    n = video_notifs[0]
    assert n["enlace_tipo"] == "video_sesion"
    assert n["enlace_id"] == sesion_id
