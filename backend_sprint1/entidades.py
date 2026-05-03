from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
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

    clase = relationship("ClaseDB", back_populates="anuncios")


class ArchivoClaseDB(Base):
    __tablename__ = "archivos_clase"

    id = Column(Integer, primary_key=True, index=True)
    clase_id = Column(Integer, ForeignKey("clases.id", ondelete="CASCADE"), nullable=False, index=True)
    autor_id = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=False)
    nombre_original = Column(String(200), nullable=False)
    mime = Column(String(120), nullable=False)
    tamano_bytes = Column(Integer, nullable=False)
    subido_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

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

    clase = relationship("ClaseDB", back_populates="salas_chat")
    mensajes = relationship("MensajeChatDB", back_populates="sala", cascade="all, delete-orphan")


class MensajeChatDB(Base):
    __tablename__ = "mensajes_chat"

    id = Column(Integer, primary_key=True, index=True)
    sala_id = Column(Integer, ForeignKey("salas_chat.id", ondelete="CASCADE"), nullable=False, index=True)
    autor_id = Column(Integer, ForeignKey("cuentas_v2.id"), nullable=False)
    contenido = Column(Text, nullable=False)
    enviado_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    sala = relationship("SalaChatDB", back_populates="mensajes")
