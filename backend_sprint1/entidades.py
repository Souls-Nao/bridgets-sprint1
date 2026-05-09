from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from motor_db import Base


class UsuarioDB(Base):
    __tablename__ = "cuentas_v2"

    id = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String, nullable=False)
    codigo = Column(String, nullable=False)
    correo_electronico = Column(String, unique=True, index=True, nullable=False)
    usuario_login = Column(String, unique=True, index=True, nullable=False)
    hash_acceso = Column(String, nullable=False)
    tipo_cuenta = Column(String, nullable=False)  # 'estudiante' | 'tutor'

    clases_dictadas = relationship("ClaseDB", back_populates="tutor", cascade="all, delete-orphan")
    inscripciones = relationship("InscripcionDB", back_populates="estudiante", cascade="all, delete-orphan")
    notas = relationship("NotaEstudianteDB", back_populates="estudiante", cascade="all, delete-orphan")


class ClaseDB(Base):
    __tablename__ = "clases"

    id = Column(Integer, primary_key=True, index=True)
    codigo_clase = Column(String(8), unique=True, index=True, nullable=False)
    nombre = Column(String(80), nullable=False, index=True)
    materia = Column(String(80), nullable=False, index=True)
    descripcion = Column(Text, nullable=True)
    tutor_id = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=False)
    es_privada = Column(Boolean, nullable=False, server_default="false", default=False)
    creada_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tutor = relationship("UsuarioDB", back_populates="clases_dictadas")
    inscripciones = relationship("InscripcionDB", back_populates="clase", cascade="all, delete-orphan")
    anuncios = relationship("AnuncioClaseDB", back_populates="clase", cascade="all, delete-orphan")
    archivos = relationship("ArchivoClaseDB", back_populates="clase", cascade="all, delete-orphan")
    notas = relationship("NotaEstudianteDB", back_populates="clase", cascade="all, delete-orphan")
    salas_chat = relationship("SalaChatDB", back_populates="clase", cascade="all, delete-orphan")
    tareas = relationship("TareaDB", back_populates="clase", cascade="all, delete-orphan")


class InscripcionDB(Base):
    __tablename__ = "inscripciones"
    __table_args__ = (UniqueConstraint("clase_id", "estudiante_id", name="uq_inscripcion_clase_estudiante"),)

    id = Column(Integer, primary_key=True, index=True)
    clase_id = Column(Integer, ForeignKey("clases.id", ondelete="CASCADE"), nullable=False, index=True)
    estudiante_id = Column(Integer, ForeignKey("cuentas_v2.id", ondelete="CASCADE"), nullable=False, index=True)
    inscrito_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    clase = relationship("ClaseDB", back_populates="inscripciones")
    estudiante = relationship("UsuarioDB", back_populates="inscripciones")


class AnuncioClaseDB(Base):
    __tablename__ = "anuncios_clase"

    id = Column(Integer, primary_key=True, index=True)
    clase_id = Column(Integer, ForeignKey("clases.id", ondelete="CASCADE"), nullable=False, index=True)
    autor_id = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=False)
    titulo = Column(String(120), nullable=False)
    contenido = Column(Text, nullable=False)
    publicado_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    anclado = Column(Boolean, nullable=False, default=False, server_default="false")

    clase = relationship("ClaseDB", back_populates="anuncios")
    comentarios = relationship(
        "ComentarioAnuncioDB",
        back_populates="anuncio",
        cascade="all, delete-orphan",
    )


class ArchivoClaseDB(Base):
    __tablename__ = "archivos_clase"

    id = Column(Integer, primary_key=True, index=True)
    clase_id = Column(Integer, ForeignKey("clases.id", ondelete="CASCADE"), nullable=False, index=True)
    autor_id = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=False)
    nombre_original = Column(String(200), nullable=False)
    mime = Column(String(120), nullable=False)
    tamano_bytes = Column(Integer, nullable=False)
    subido_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Etiqueta libre para agrupar archivos: "Tareas", "Material", "Lecturas"...
    categoria = Column(String(40), nullable=True)

    clase = relationship("ClaseDB", back_populates="archivos")
    blob = relationship("ArchivoBlobDB", back_populates="archivo", uselist=False, cascade="all, delete-orphan")


class ArchivoBlobDB(Base):
    __tablename__ = "archivos_blob"

    archivo_id = Column(Integer, ForeignKey("archivos_clase.id", ondelete="CASCADE"), primary_key=True)
    contenido = Column(LargeBinary, nullable=False)

    archivo = relationship("ArchivoClaseDB", back_populates="blob")


class NotaEstudianteDB(Base):
    __tablename__ = "notas_estudiante"

    id = Column(Integer, primary_key=True, index=True)
    clase_id = Column(Integer, ForeignKey("clases.id", ondelete="CASCADE"), nullable=False, index=True)
    estudiante_id = Column(Integer, ForeignKey("cuentas_v2.id", ondelete="CASCADE"), nullable=False, index=True)
    titulo = Column(String(120), nullable=False)
    contenido = Column(Text, nullable=False, default="")
    creada_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actualizada_en = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    clase = relationship("ClaseDB", back_populates="notas")
    estudiante = relationship("UsuarioDB", back_populates="notas")


class SalaChatDB(Base):
    __tablename__ = "salas_chat"
    __table_args__ = (UniqueConstraint("clase_id", "estudiante_id", name="uq_sala_clase_estudiante"),)

    id = Column(Integer, primary_key=True, index=True)
    clase_id = Column(Integer, ForeignKey("clases.id", ondelete="CASCADE"), nullable=False, index=True)
    estudiante_id = Column(Integer, ForeignKey("cuentas_v2.id", ondelete="CASCADE"), nullable=False, index=True)
    tutor_id = Column(Integer, ForeignKey("cuentas_v2.id", ondelete="CASCADE"), nullable=False, index=True)
    iniciador_id = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=False)
    estado = Column(String(20), nullable=False, default="pendiente")  # pendiente | activa | cerrada
    creada_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actualizada_en = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    # Último mensaje_id leído por cada rol. Para no-leídos: COUNT(mensajes con id > ultimo_visto_X).
    # default Python + server_default DDL para que filas nuevas y existentes obtengan 0.
    ultimo_visto_estudiante = Column(Integer, nullable=False, default=0, server_default="0")
    ultimo_visto_tutor = Column(Integer, nullable=False, default=0, server_default="0")

    clase = relationship("ClaseDB", back_populates="salas_chat")
    mensajes = relationship("MensajeChatDB", back_populates="sala", cascade="all, delete-orphan")


class MensajeChatDB(Base):
    __tablename__ = "mensajes_chat"

    id = Column(Integer, primary_key=True, index=True)
    sala_id = Column(Integer, ForeignKey("salas_chat.id", ondelete="CASCADE"), nullable=False, index=True)
    autor_id = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=False)
    contenido = Column(Text, nullable=False)
    enviado_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    editado_en = Column(DateTime(timezone=True), nullable=True)

    sala = relationship("SalaChatDB", back_populates="mensajes")


class TareaDB(Base):
    """Tarea/asignación que el tutor publica para una clase."""
    __tablename__ = "tareas"

    id = Column(Integer, primary_key=True, index=True)
    clase_id = Column(Integer, ForeignKey("clases.id", ondelete="CASCADE"), nullable=False, index=True)
    autor_id = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=False)
    titulo = Column(String(120), nullable=False)
    descripcion = Column(Text, nullable=True)
    fecha_limite = Column(DateTime(timezone=True), nullable=True)
    max_puntos = Column(Float, nullable=False, default=10.0, server_default="10.0")
    creada_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    clase = relationship("ClaseDB", back_populates="tareas")
    entregas = relationship("EntregaDB", back_populates="tarea", cascade="all, delete-orphan")


class EntregaDB(Base):
    """
    Entrega de un estudiante para una tarea. Un estudiante tiene como máximo
    una entrega por tarea (constraint única); puede actualizarla mientras la
    tarea siga abierta. Calificación y feedback son opcionales hasta que el
    tutor las ponga.
    """
    __tablename__ = "entregas"
    __table_args__ = (
        UniqueConstraint("tarea_id", "estudiante_id", name="uq_entrega_tarea_estudiante"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tarea_id = Column(Integer, ForeignKey("tareas.id", ondelete="CASCADE"), nullable=False, index=True)
    estudiante_id = Column(Integer, ForeignKey("cuentas_v2.id", ondelete="CASCADE"), nullable=False, index=True)
    contenido = Column(Text, nullable=False, default="")
    entregada_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actualizada_en = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    # Calificación numérica + feedback. Nulos hasta que el tutor califica.
    calificacion = Column(Float, nullable=True)
    feedback = Column(Text, nullable=True)
    calificada_en = Column(DateTime(timezone=True), nullable=True)
    calificada_por = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=True)

    tarea = relationship("TareaDB", back_populates="entregas")


class ComentarioAnuncioDB(Base):
    """Comentario de un usuario sobre un anuncio (engagement)."""
    __tablename__ = "comentarios_anuncio"

    id = Column(Integer, primary_key=True, index=True)
    anuncio_id = Column(
        Integer,
        ForeignKey("anuncios_clase.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    autor_id = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=False)
    contenido = Column(Text, nullable=False)
    publicado_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    anuncio = relationship("AnuncioClaseDB", back_populates="comentarios")


class NotificacionDB(Base):
    """
    Eventos in-app destinados a un usuario: anuncio nuevo, archivo nuevo,
    tarea nueva, calificación recibida, solicitud de chat, etc.
    Se crean de forma persistente y, en el mismo flujo, se hace push por WS
    al destinatario si está conectado.
    """
    __tablename__ = "notificaciones"

    id = Column(Integer, primary_key=True, index=True)
    destinatario_id = Column(
        Integer,
        ForeignKey("cuentas_v2.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tipo = Column(String(40), nullable=False, index=True)  # ej.: anuncio_nuevo, calificacion
    titulo = Column(String(200), nullable=False)
    cuerpo = Column(Text, nullable=True)
    # `enlace_tipo`/`enlace_id` permiten que el cliente sepa a dónde llevar al usuario
    # cuando hace click (ej.: ("anuncio", 42)). Opcional — algunas notificaciones son
    # informativas y no llevan a nada.
    enlace_tipo = Column(String(40), nullable=True)
    enlace_id = Column(Integer, nullable=True)
    creada_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    leida_en = Column(DateTime(timezone=True), nullable=True)


class EventoAuditoriaDB(Base):
    """
    Bitácora de acciones sensibles para investigación forense y compliance.
    Se escribe siempre que ocurra una acción "que importa" (login, borrado,
    cambio de credenciales, expulsión, etc.). Append-only por convención.
    """
    __tablename__ = "eventos_auditoria"

    id = Column(Integer, primary_key=True, index=True)
    # actor_id puede ser NULL para eventos anónimos (login fallido sin sesión).
    actor_id = Column(Integer, ForeignKey("cuentas_v2.id", ondelete="SET NULL"), nullable=True, index=True)
    accion = Column(String(60), nullable=False, index=True)
    recurso_tipo = Column(String(40), nullable=True)
    recurso_id = Column(Integer, nullable=True)
    ip = Column(String(45), nullable=True)  # IPv4 o IPv6
    detalles = Column(Text, nullable=True)
    creada_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class TokenRevocadoDB(Base):
    """
    Lista negra de JWTs revocados (logout, cambio de contraseña, etc.).
    El claim 'jti' del token (RFC 7519) es la clave primaria.
    `expira_en` permite limpiar entradas que ya no podrían usarse de todas formas.
    """
    __tablename__ = "tokens_revocados"

    jti = Column(String(64), primary_key=True, index=True)
    expira_en = Column(DateTime(timezone=True), nullable=False, index=True)
    revocado_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
