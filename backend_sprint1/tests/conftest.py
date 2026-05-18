"""
Configuración compartida de pytest:

  - Variables de entorno mínimas para que `app_server` cargue (DATABASE_URL,
    SECRET_KEY) ANTES de importar el módulo.
  - Engine sqlite en memoria con `StaticPool` para que la misma conexión
    se reuse entre el thread del TestClient y el thread principal — evita
    el bug clásico de "no such table" cuando cada conexión abre su propia
    base in-memory.
  - Fixture `client` que devuelve un TestClient con la app, su DB ya creada
    y los limitadores de rate-limit reseteados entre tests.
"""

import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-32bytes-12345678901234567890")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("BRIDGETS_ADMIN_TOKEN", "test-admin-token")

# El paquete vive en backend_sprint1/, así que lo añadimos al path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Sustituimos el engine real por uno sqlite-en-memoria con StaticPool ANTES de
# que app_server lo importe.
import motor_db  # noqa: E402

motor_db.engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
motor_db.SessionLocal = sessionmaker(bind=motor_db.engine, autoflush=False, autocommit=False)

# Las migraciones ALTER TABLE son sintaxis Postgres; en sqlite las neutralizamos.
import migraciones  # noqa: E402
migraciones.aplicar_migraciones_idempotentes = lambda engine: None

import app_server  # noqa: E402, F401
from entidades import Base  # noqa: E402

# Aseguramos que las tablas existan en la nueva base in-memory.
Base.metadata.create_all(bind=motor_db.engine)


@pytest.fixture
def client():
    # Reseteamos los rate-limiters para que tests independientes no se interfieran.
    from servicio_rate_limit import limitador_disponibilidad, limitador_login, limitador_registro
    for lim in (limitador_disponibilidad, limitador_login, limitador_registro):
        with lim._lock:
            lim._historial.clear()
    # Usar TestClient como context manager garantiza que el portal de starlette
    # se levante y se cierre limpiamente entre tests — clave para evitar que
    # estado de WebSockets se filtre entre suites.
    with TestClient(app_server.app) as cliente:
        yield cliente


@pytest.fixture
def db():
    """Sesión de DB para tests que necesitan inspeccionar/limpiar estado directo."""
    s = motor_db.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _registrar_y_loguear(client: TestClient, login: str, rol: str = "estudiante") -> dict:
    """Helper: registra un usuario nuevo y devuelve su sesión (token incluido)."""
    payload = {
        "nombre_completo": f"Usuario {login}",
        "codigo": "1",
        "correo_electronico": f"{login}@test.com",
        "usuario_login": login,
        "password": "password-segura",
        "tipo_cuenta": rol,
    }
    r = client.post("/api/registro", json=payload)
    assert r.status_code == 201, r.text
    r = client.post("/api/login", json={"usuario_login": login, "password": "password-segura"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def tutor(client):
    """Tutor recién registrado y autenticado."""
    return _registrar_y_loguear(client, "tutor_test", "tutor")


@pytest.fixture
def estudiante(client):
    """Estudiante recién registrado y autenticado."""
    return _registrar_y_loguear(client, "estudiante_test", "estudiante")


@pytest.fixture
def auth(tutor):
    """Header Authorization Bearer para el tutor."""
    return {"Authorization": f"Bearer {tutor['token']}"}


@pytest.fixture
def auth_estudiante(estudiante):
    return {"Authorization": f"Bearer {estudiante['token']}"}


@pytest.fixture(autouse=True)
def _limpiar_db_entre_tests():
    """Garantiza aislamiento: tras cada test borramos las tablas y las recreamos.
    También limpiamos el registro de conexiones WS, que es un singleton de
    proceso y de lo contrario arrastraría entradas obsoletas entre tests."""
    yield
    Base.metadata.drop_all(bind=motor_db.engine)
    Base.metadata.create_all(bind=motor_db.engine)
    from servicio_ws import gestor_conexiones
    gestor_conexiones._conexiones.clear()
