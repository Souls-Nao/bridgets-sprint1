from sqlalchemy import text
from sqlalchemy.engine import Engine


def aplicar_migraciones_idempotentes(engine: Engine) -> None:
    """
    Migraciones DDL ligeras que no maneja create_all (porque la tabla ya existe).
    Se ejecutan al arranque del servidor; cada statement es idempotente.
    """
    sentencias = [
        # Sprint 4: clases privadas
        "ALTER TABLE clases ADD COLUMN IF NOT EXISTS es_privada BOOLEAN NOT NULL DEFAULT FALSE",
    ]
    with engine.begin() as conexion:
        for sql in sentencias:
            conexion.execute(text(sql))
