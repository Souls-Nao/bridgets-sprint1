"""Tests del flujo de cuenta: registro, login, /api/yo, logout, password, eliminar cuenta."""


def test_registro_login_y_yo(client):
    payload = {
        "nombre_completo": "Ana Test",
        "codigo": "1",
        "correo_electronico": "ana@test.com",
        "usuario_login": "ana",
        "password": "password-segura",
        "tipo_cuenta": "estudiante",
    }
    r = client.post("/api/registro", json=payload)
    assert r.status_code == 201

    r = client.post("/api/login", json={"usuario_login": "ana", "password": "password-segura"})
    assert r.status_code == 200
    sesion = r.json()
    assert sesion["rol"] == "estudiante"
    assert sesion["usuario"] == "ana"
    assert sesion["token"]

    headers = {"Authorization": f"Bearer {sesion['token']}"}
    r = client.get("/api/yo", headers=headers)
    assert r.status_code == 200
    assert r.json()["usuario"] == "ana"


def test_login_credenciales_invalidas(client):
    client.post("/api/registro", json={
        "nombre_completo": "Beto Test", "codigo": "1",
        "correo_electronico": "beto@test.com", "usuario_login": "beto",
        "password": "password-segura", "tipo_cuenta": "estudiante",
    })
    r = client.post("/api/login", json={"usuario_login": "beto", "password": "incorrecta"})
    assert r.status_code == 401


def test_logout_revoca_token(client, auth_estudiante):
    # El token funciona...
    assert client.get("/api/yo", headers=auth_estudiante).status_code == 200
    # ...se llama logout...
    assert client.post("/api/logout", headers=auth_estudiante).status_code == 204
    # ...y deja de funcionar.
    assert client.get("/api/yo", headers=auth_estudiante).status_code == 401


def test_cambio_password_revoca_token(client, estudiante):
    headers = {"Authorization": f"Bearer {estudiante['token']}"}
    r = client.post("/api/yo/password", headers=headers, json={
        "password_actual": "password-segura",
        "password_nueva": "otra-password",
    })
    assert r.status_code == 204
    # El token previo ya no sirve.
    assert client.get("/api/yo", headers=headers).status_code == 401
    # La nueva contraseña sí logea.
    r = client.post("/api/login", json={"usuario_login": "estudiante_test", "password": "otra-password"})
    assert r.status_code == 200


def test_cambio_password_actual_incorrecta(client, auth_estudiante):
    r = client.post("/api/yo/password", headers=auth_estudiante, json={
        "password_actual": "incorrecta",
        "password_nueva": "otra-password",
    })
    assert r.status_code == 400


def test_eliminar_cuenta(client, estudiante):
    headers = {"Authorization": f"Bearer {estudiante['token']}"}
    # httpx.delete() no acepta body en versiones recientes — usamos request().
    r = client.request("DELETE", "/api/yo", headers=headers, json={"password": "password-segura"})
    assert r.status_code == 204
    # La cuenta ya no existe ni el token sirve.
    assert client.get("/api/yo", headers=headers).status_code == 401
    r = client.post("/api/login", json={"usuario_login": "estudiante_test", "password": "password-segura"})
    assert r.status_code == 401


def test_endpoint_protegido_sin_token(client):
    r = client.get("/api/yo")
    # FastAPI HTTPBearer puede devolver 401 o 403 según versión; ambos son
    # "no autenticado" desde la perspectiva del cliente.
    assert r.status_code in (401, 403)
