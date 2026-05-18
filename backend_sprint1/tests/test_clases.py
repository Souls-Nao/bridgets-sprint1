"""Tests del CRUD de clases, inscripción/desinscripción/expulsión, y permisos asociados."""


def _crear_clase(client, headers, nombre="Mate", privada=False):
    r = client.post("/api/clases", headers=headers, json={
        "nombre": nombre, "materia": "Materia X",
        "descripcion": "desc", "es_privada": privada,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_crear_listar_y_detalle(client, auth):
    creada = _crear_clase(client, auth)
    assert creada["es_propietario"] is True

    r = client.get("/api/clases/mis", headers=auth)
    assert r.status_code == 200
    assert any(c["id"] == creada["id"] for c in r.json())

    r = client.get(f"/api/clases/{creada['id']}", headers=auth)
    assert r.status_code == 200


def test_estudiante_no_puede_crear_clase(client, auth_estudiante):
    r = client.post("/api/clases", headers=auth_estudiante, json={
        "nombre": "X", "materia": "Y", "descripcion": "", "es_privada": False,
    })
    assert r.status_code == 403


def test_inscripcion_y_desinscripcion(client, tutor, estudiante):
    auth_t = {"Authorization": f"Bearer {tutor['token']}"}
    auth_e = {"Authorization": f"Bearer {estudiante['token']}"}
    clase = _crear_clase(client, auth_t)

    # Estudiante se inscribe por código.
    r = client.post("/api/inscripciones", headers=auth_e, json={
        "codigo_clase": clase["codigo_clase"],
    })
    assert r.status_code == 201

    # Aparece en sus clases.
    r = client.get("/api/clases/mis", headers=auth_e)
    assert any(c["id"] == clase["id"] and c["inscrito"] for c in r.json())

    # Doble inscripción no permitida.
    r = client.post("/api/inscripciones", headers=auth_e, json={
        "codigo_clase": clase["codigo_clase"],
    })
    assert r.status_code == 400

    # Se da de baja.
    r = client.delete(f"/api/clases/{clase['id']}/inscripcion", headers=auth_e)
    assert r.status_code == 204

    # Ya no aparece en sus clases.
    r = client.get("/api/clases/mis", headers=auth_e)
    assert not any(c["id"] == clase["id"] for c in r.json())


def test_expulsion_por_tutor(client, tutor, estudiante):
    auth_t = {"Authorization": f"Bearer {tutor['token']}"}
    auth_e = {"Authorization": f"Bearer {estudiante['token']}"}
    clase = _crear_clase(client, auth_t)
    client.post("/api/inscripciones", headers=auth_e, json={"codigo_clase": clase["codigo_clase"]})

    # Tutor lista estudiantes.
    r = client.get(f"/api/clases/{clase['id']}/estudiantes", headers=auth_t)
    assert r.status_code == 200
    assert len(r.json()) == 1
    estudiante_id = r.json()[0]["id"]

    # Estudiante NO puede listar estudiantes (rol).
    assert client.get(f"/api/clases/{clase['id']}/estudiantes", headers=auth_e).status_code == 403

    # Tutor expulsa.
    r = client.delete(
        f"/api/clases/{clase['id']}/estudiantes/{estudiante_id}", headers=auth_t,
    )
    assert r.status_code == 204
    assert client.get(f"/api/clases/{clase['id']}/estudiantes", headers=auth_t).json() == []


def test_eliminar_clase_solo_dueno(client, tutor):
    auth_t = {"Authorization": f"Bearer {tutor['token']}"}
    clase = _crear_clase(client, auth_t)

    # Otro tutor no puede borrar.
    r = client.post("/api/registro", json={
        "nombre_completo": "Otro Tutor", "codigo": "2",
        "correo_electronico": "otro@test.com", "usuario_login": "otro_tutor",
        "password": "password-segura", "tipo_cuenta": "tutor",
    })
    assert r.status_code == 201
    sesion_otro = client.post("/api/login", json={
        "usuario_login": "otro_tutor", "password": "password-segura",
    }).json()
    auth_otro = {"Authorization": f"Bearer {sesion_otro['token']}"}
    assert client.delete(f"/api/clases/{clase['id']}", headers=auth_otro).status_code == 403

    # El dueño sí puede.
    assert client.delete(f"/api/clases/{clase['id']}", headers=auth_t).status_code == 204
    assert client.get(f"/api/clases/{clase['id']}", headers=auth_t).status_code == 404


def test_tutor_birdgt_dirigido_a_un_estudiante(client, tutor, estudiante):
    """El tutor puede abrir Birdgt con UN estudiante específico vía
    `?estudiante_id=`, sin enviar solicitud a todos los demás."""
    auth_t = {"Authorization": f"Bearer {tutor['token']}"}
    auth_e = {"Authorization": f"Bearer {estudiante['token']}"}
    clase = _crear_clase(client, auth_t)
    # Inscribimos dos estudiantes para asegurar el aislamiento.
    client.post("/api/inscripciones", headers=auth_e, json={"codigo_clase": clase["codigo_clase"]})
    client.post("/api/registro", json={
        "nombre_completo": "Otro", "codigo": "9",
        "correo_electronico": "otro@x.com", "usuario_login": "otro_est",
        "password": "password-segura", "tipo_cuenta": "estudiante",
    })
    otro = client.post("/api/login", json={
        "usuario_login": "otro_est", "password": "password-segura",
    }).json()
    client.post("/api/inscripciones", headers={"Authorization": f"Bearer {otro['token']}"},
                json={"codigo_clase": clase["codigo_clase"]})

    # Tutor dispara Birdgt solo con el primer estudiante.
    r = client.post(
        f"/api/clases/{clase['id']}/birdgt",
        headers=auth_t,
        params={"estudiante_id": estudiante["id"]},
    )
    assert r.status_code == 201, r.text
    salas = r.json()["salas"]
    assert len(salas) == 1
    assert salas[0]["contraparte"]["id"] == estudiante["id"]


def test_tutor_birdgt_dirigido_rechaza_estudiante_externo(client, tutor):
    """Si el id no está inscrito en la clase, el endpoint responde 400."""
    auth_t = {"Authorization": f"Bearer {tutor['token']}"}
    clase = _crear_clase(client, auth_t)
    r = client.post(
        f"/api/clases/{clase['id']}/birdgt",
        headers=auth_t,
        params={"estudiante_id": 9999},
    )
    assert r.status_code == 400


def test_clase_privada_no_aparece_en_busqueda_publica(client, tutor):
    auth_t = {"Authorization": f"Bearer {tutor['token']}"}
    publica = _crear_clase(client, auth_t, nombre="Pública", privada=False)
    privada = _crear_clase(client, auth_t, nombre="Privada", privada=True)

    # Búsqueda sin código solo trae públicas.
    r = client.get("/api/clases/buscar", headers=auth_t, params={"q": ""})
    ids = [c["id"] for c in r.json()]
    assert publica["id"] in ids
    assert privada["id"] not in ids

    # Búsqueda por código exacto sí trae la privada.
    r = client.get("/api/clases/buscar", headers=auth_t, params={"q": privada["codigo_clase"]})
    ids = [c["id"] for c in r.json()]
    assert privada["id"] in ids
