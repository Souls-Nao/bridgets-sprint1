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
        # Módulo 4: contadores de no-leídos por participante (uno por rol)
        "ALTER TABLE salas_chat ADD COLUMN IF NOT EXISTS ultimo_visto_estudiante INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE salas_chat ADD COLUMN IF NOT EXISTS ultimo_visto_tutor INTEGER NOT NULL DEFAULT 0",
        # Módulo 4: marca temporal de edición de mensaje (NULL = sin editar)
        "ALTER TABLE mensajes_chat ADD COLUMN IF NOT EXISTS editado_en TIMESTAMPTZ NULL",
        # Módulo 5: pin de anuncios y categoría de archivos
        "ALTER TABLE anuncios_clase ADD COLUMN IF NOT EXISTS anclado BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE archivos_clase ADD COLUMN IF NOT EXISTS categoria VARCHAR(40) NULL",
        # Módulo 6: las nuevas tablas tareas / entregas las crea Base.metadata.create_all,
        # no requieren ALTER aquí.
        # Fase E (post-deploy): propiedad transferible en llamadas grupales —
        # el tutor puede salir cediendo el control sin terminar la llamada.
        # Inicializamos `propietario_id` al `iniciador_id` para filas viejas.
        "ALTER TABLE llamadas_grupales ADD COLUMN IF NOT EXISTS propietario_id INTEGER NULL REFERENCES cuentas_v2(id) ON DELETE SET NULL",
        "UPDATE llamadas_grupales SET propietario_id = iniciador_id WHERE propietario_id IS NULL",
    ]
    with engine.begin() as conexion:
        for sql in sentencias:
            conexion.execute(text(sql))
