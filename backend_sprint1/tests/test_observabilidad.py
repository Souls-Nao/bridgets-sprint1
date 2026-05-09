"""Tests del módulo 9: salud, métricas, audit log y endpoint de limpieza admin."""


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_metrics_cuenta_requests(client):
    # Disparamos varios requests conocidos y verificamos que el contador sube.
    snap_inicial = client.get("/metrics").json()
    base = snap_inicial["requests_total"]
    for _ in range(3):
        client.get("/healthz")
    snap_final = client.get("/metrics").json()
    # 3 healthz + 1 metrics adicional como mínimo.
    assert snap_final["requests_total"] >= base + 4
    # Hay desglose por método HTTP.
    assert snap_final["requests_por_metodo"].get("GET", 0) >= 4
    # Hay desglose por clase de status; nuestros calls son 200 = 2xx.
    assert snap_final["requests_por_clase"].get("2xx", 0) >= 4


def test_audit_log_se_escribe_en_login(client, db):
    from servicio_auditoria import listar_eventos

    client.post("/api/registro", json={
        "nombre_completo": "Cris", "codigo": "1",
        "correo_electronico": "cris@test.com", "usuario_login": "cris",
        "password": "password-segura", "tipo_cuenta": "estudiante",
    })
    # Login fallido genera evento.
    client.post("/api/login", json={"usuario_login": "cris", "password": "mala"})
    client.post("/api/login", json={"usuario_login": "cris", "password": "password-segura"})

    eventos = [e.accion for e in listar_eventos(db, limite=20)]
    assert "registro" in eventos
    assert "login_fallido" in eventos
    assert "login_exitoso" in eventos


def test_admin_limpieza_requiere_token(client):
    # Sin token → 403.
    r = client.post("/admin/limpieza")
    assert r.status_code == 403

    # Con token → 200 y devuelve los conteos.
    r = client.post("/admin/limpieza", headers={"X-Admin-Token": "test-admin-token"})
    assert r.status_code == 200
    body = r.json()
    assert "revocaciones_eliminadas" in body
    assert "notificaciones_eliminadas" in body
    assert "mensajes_eliminados" in body
