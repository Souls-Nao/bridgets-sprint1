from datetime import datetime
from typing import Literal, Optional

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


class PerfilActualizar(BaseModel):
    """PATCH parcial sobre el perfil. `usuario_login` no es editable."""
    nombre_completo: Optional[str] = Field(None, min_length=3, max_length=50)
    codigo: Optional[str] = Field(None, pattern=r"^\d+$", min_length=1, max_length=9)
    correo_electronico: Optional[EmailStr] = None


class CambiarPasswordPeticion(BaseModel):
    password_actual: str = Field(..., min_length=1)
    password_nueva: str = Field(..., min_length=6)


class EliminarCuentaPeticion(BaseModel):
    password: str = Field(..., min_length=1)


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


class ClaseActualizar(BaseModel):
    """Campos editables de una clase. Todos opcionales para PATCH parcial."""
    nombre: Optional[str] = Field(None, min_length=3, max_length=80)
    materia: Optional[str] = Field(None, min_length=2, max_length=80)
    descripcion: Optional[str] = Field(None, max_length=2000)
    es_privada: Optional[bool] = None


class TutorResumen(BaseModel):
    id: int
    nombre: str
    usuario: str


class EstudianteResumen(BaseModel):
    id: int
    nombre: str
    usuario: str
    correo: str
    inscrito_en: datetime


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


class AnuncioActualizar(BaseModel):
    """PATCH parcial: cualquier campo omitido se deja como está."""
    titulo: Optional[str] = Field(None, min_length=3, max_length=120)
    contenido: Optional[str] = Field(None, min_length=1, max_length=4000)
    anclado: Optional[bool] = None


class AnuncioResumen(BaseModel):
    id: int
    titulo: str
    contenido: str
    publicado_en: datetime
    autor_nombre: str
    anclado: bool = False

    model_config = ConfigDict(from_attributes=True)


class ComentarioCrear(BaseModel):
    contenido: str = Field(..., min_length=1, max_length=2000)


class ComentarioResumen(BaseModel):
    id: int
    anuncio_id: int
    autor_id: int
    autor_nombre: str
    contenido: str
    publicado_en: datetime

    model_config = ConfigDict(from_attributes=True)


# ------------------------- Archivos -------------------------

class ArchivoResumen(BaseModel):
    id: int
    nombre_original: str
    mime: str
    tamano_bytes: int
    subido_en: datetime
    autor_nombre: str
    categoria: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ArchivoActualizar(BaseModel):
    """Por ahora solo la categoría es editable; nombre/contenido requerirían versionado."""
    categoria: Optional[str] = Field(None, max_length=40)


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
    mensajes_no_leidos: int = 0

    model_config = ConfigDict(from_attributes=True)


class MensajeResumen(BaseModel):
    id: int
    sala_id: int
    autor_id: int
    contenido: str
    enviado_en: datetime
    editado_en: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MensajeCrear(BaseModel):
    contenido: str = Field(..., min_length=1, max_length=4000)


class MensajeActualizar(BaseModel):
    contenido: str = Field(..., min_length=1, max_length=4000)


class NoLeidosClase(BaseModel):
    """Total de mensajes no leídos del usuario en todas sus salas de una clase."""
    total: int


# ------------------------- Tareas y entregas -------------------------

class TareaCrear(BaseModel):
    titulo: str = Field(..., min_length=3, max_length=120)
    descripcion: Optional[str] = Field(None, max_length=4000)
    fecha_limite: Optional[datetime] = None
    max_puntos: float = Field(10.0, ge=0.1, le=100.0)


class TareaActualizar(BaseModel):
    titulo: Optional[str] = Field(None, min_length=3, max_length=120)
    descripcion: Optional[str] = Field(None, max_length=4000)
    fecha_limite: Optional[datetime] = None
    max_puntos: Optional[float] = Field(None, ge=0.1, le=100.0)


class EntregaResumen(BaseModel):
    """Datos de la entrega (visible para el dueño-estudiante y el tutor)."""
    id: int
    tarea_id: int
    estudiante_id: int
    estudiante_nombre: str
    contenido: str
    entregada_en: datetime
    actualizada_en: datetime
    calificacion: Optional[float] = None
    feedback: Optional[str] = None
    calificada_en: Optional[datetime] = None


class TareaResumen(BaseModel):
    id: int
    clase_id: int
    titulo: str
    descripcion: Optional[str] = None
    fecha_limite: Optional[datetime] = None
    max_puntos: float
    creada_en: datetime
    autor_nombre: str
    # Información de contexto según quién consulta:
    # - Estudiante: su propia entrega (None si no entregó).
    # - Tutor: total de entregas recibidas.
    entrega_propia: Optional[EntregaResumen] = None
    total_entregas: Optional[int] = None


class EntregaCrear(BaseModel):
    contenido: str = Field(..., min_length=1, max_length=10000)


class CalificarPeticion(BaseModel):
    calificacion: float = Field(..., ge=0.0)
    feedback: Optional[str] = Field(None, max_length=4000)


# ------------------------- Notificaciones -------------------------

class NotificacionResumen(BaseModel):
    id: int
    tipo: str
    titulo: str
    cuerpo: Optional[str] = None
    enlace_tipo: Optional[str] = None
    enlace_id: Optional[int] = None
    creada_en: datetime
    leida_en: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TotalNoLeidas(BaseModel):
    total: int


class BirdgtIniciarRespuesta(BaseModel):
    salas: list[SalaResumen]
    creadas: int
    reutilizadas: int


# ------------------------- Videollamadas -------------------------

class VideoSesionCrear(BaseModel):
    sala_id: int = Field(..., ge=1)
    # camara: solo cámara + audio. pantalla: solo pantalla compartida.
    # ambos: cámara + pantalla simultáneas.
    modo: Literal["camara", "pantalla", "ambos"] = "camara"


class VideoSesionResumen(BaseModel):
    id: int
    sala_id: int
    iniciador_id: int
    receptor_id: int
    estado: str
    modo: str
    creada_en: datetime
    aceptada_en: Optional[datetime] = None
    finalizada_en: Optional[datetime] = None
    motivo_fin: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class VideoConfigRespuesta(BaseModel):
    """Configuración WebRTC que el cliente usa para crear su `RTCPeerConnection`."""
    ice_servers: list[dict]


# ------------------------- Llamadas grupales -------------------------

class LlamadaGrupalCrear(BaseModel):
    titulo: Optional[str] = Field(None, max_length=120)


class ParticipanteLlamadaResumen(BaseModel):
    usuario_id: int
    nombre: str
    es_iniciador: bool = False
    es_propietario: bool = False
    unido_en: datetime


class LlamadaGrupalResumen(BaseModel):
    id: int
    clase_id: int
    iniciador_id: int
    iniciador_nombre: str
    propietario_id: Optional[int] = None
    propietario_nombre: Optional[str] = None
    titulo: Optional[str] = None
    estado: str
    creada_en: datetime
    finalizada_en: Optional[datetime] = None
    participantes: list[ParticipanteLlamadaResumen] = []
