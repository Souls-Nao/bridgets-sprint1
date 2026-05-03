from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ------------------------- Cuentas -------------------------

class RegistroPeticion(BaseModel):
    nombre_completo: str = Field(..., min_length=3, max_length=50)
    codigo: str = Field(..., pattern=r"^\d+$", min_length=1, max_length=9)
    correo_electronico: EmailStr
    usuario_login: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6)
    tipo_cuenta: str = Field(..., pattern=r"^(estudiante|tutor)$")


class LoginPeticion(BaseModel):
    usuario_login: str
    password: str


class SesionRespuesta(BaseModel):
    token: str
    tipo_token: str = "Bearer"
    id: int
    nombre: str
    rol: str
    usuario: str


# ------------------------- Clases -------------------------

class ClaseCrear(BaseModel):
    nombre: str = Field(..., min_length=3, max_length=80)
    materia: str = Field(..., min_length=2, max_length=80)
    descripcion: Optional[str] = Field(None, max_length=2000)
    es_privada: bool = False


class TutorResumen(BaseModel):
    id: int
    nombre: str
    usuario: str


class ClaseResumen(BaseModel):
    id: int
    codigo_clase: str
    nombre: str
    materia: str
    tutor: TutorResumen
    inscritos: int
    inscrito: bool = False
    es_privada: bool = False


class ClaseDetalle(ClaseResumen):
    descripcion: Optional[str] = None
    creada_en: datetime
    es_propietario: bool = False


class InscripcionPeticion(BaseModel):
    codigo_clase: Optional[str] = Field(None, min_length=4, max_length=8)


# ------------------------- Anuncios -------------------------

class AnuncioCrear(BaseModel):
    titulo: str = Field(..., min_length=3, max_length=120)
    contenido: str = Field(..., min_length=1, max_length=4000)


class AnuncioResumen(BaseModel):
    id: int
    titulo: str
    contenido: str
    publicado_en: datetime
    autor_nombre: str

    model_config = ConfigDict(from_attributes=True)


# ------------------------- Archivos -------------------------

class ArchivoResumen(BaseModel):
    id: int
    nombre_original: str
    mime: str
    tamano_bytes: int
    subido_en: datetime
    autor_nombre: str

    model_config = ConfigDict(from_attributes=True)


# ------------------------- Notas -------------------------

class NotaCrear(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=120)
    contenido: str = Field("", max_length=20000)


class NotaActualizar(BaseModel):
    titulo: Optional[str] = Field(None, min_length=1, max_length=120)
    contenido: Optional[str] = Field(None, max_length=20000)


class NotaResumen(BaseModel):
    id: int
    titulo: str
    contenido: str
    creada_en: datetime
    actualizada_en: datetime

    model_config = ConfigDict(from_attributes=True)


# ------------------------- Disponibilidad / registro -------------------------

class DisponibilidadRespuesta(BaseModel):
    disponible: bool
    motivo: Optional[str] = None


# ------------------------- Birdgt (chat) -------------------------

class ContraparteResumen(BaseModel):
    id: int
    nombre: str
    rol: str


class SalaResumen(BaseModel):
    id: int
    clase_id: int
    clase_nombre: str
    estado: str
    iniciador_id: int
    contraparte: ContraparteResumen
    creada_en: datetime
    actualizada_en: datetime

    model_config = ConfigDict(from_attributes=True)


class MensajeResumen(BaseModel):
    id: int
    sala_id: int
    autor_id: int
    contenido: str
    enviado_en: datetime

    model_config = ConfigDict(from_attributes=True)


class MensajeCrear(BaseModel):
    contenido: str = Field(..., min_length=1, max_length=4000)


class BirdgtIniciarRespuesta(BaseModel):
    salas: list[SalaResumen]
    creadas: int
    reutilizadas: int
