import logging
import os
import re
from typing import Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from entidades import AnuncioClaseDB, LlamadaGrupalDB, SuscripcionAnuncioDB, UsuarioDB
from migraciones import aplicar_migraciones_idempotentes
from motor_db import Base, SessionLocal, engine, obtener_sesion
from servicio_anuncios import ControladorAnuncios
from servicio_archivos import ControladorArchivos
from servicio_auditoria import auditar
from servicio_auth import (
    exigir_rol,
    limpiar_revocaciones_expiradas,
    obtener_usuario_actual,
    obtener_usuario_y_payload,
    revocar_token,
    usuario_desde_token,
)
from servicio_chat import ControladorChat
from servicio_clases import ControladorClases
from servicio_comentarios import ControladorComentarios
from servicio_metricas import metricas
from servicio_notas import ControladorNotas
from servicio_notificaciones import ControladorNotificaciones
from servicio_rate_limit import (
    limitador_disponibilidad,
    limitador_login,
    limitador_registro,
)
from servicio_tareas import ControladorTareas
from servicio_llamada_grupal import ControladorLlamadaGrupal
from servicio_videollamada import ESTADOS_SENALIZABLES, ControladorVideollamada
from servicio_usuarios import ControladorUsuarios
from servicio_ws import gestor_conexiones
from validadores import (
    AnuncioActualizar,
    AnuncioCrear,
    AnuncioResumen,
    ArchivoActualizar,
    BirdgtIniciarRespuesta,
    CalificarPeticion,
    CambiarPasswordPeticion,
    ClaseActualizar,
    ClaseCrear,
    ClaseDetalle,
    ClaseResumen,
    ComentarioCrear,
    ComentarioResumen,
    DisponibilidadRespuesta,
    EliminarCuentaPeticion,
    EntregaCrear,
    EntregaResumen,
    EstudianteResumen,
    InscripcionPeticion,
    LlamadaGrupalCrear,
    LlamadaGrupalResumen,
    LoginPeticion,
    ParticipanteLlamadaResumen,
    MensajeActualizar,
    MensajeCrear,
    MensajeResumen,
    NoLeidosClase,
    NotaActualizar,
    NotaCrear,
    NotaResumen,
    NotificacionResumen,
    PerfilActualizar,
    RegistroPeticion,
    SalaResumen,
    SesionRespuesta,
    TareaActualizar,
    TareaCrear,
    TareaResumen,
    TotalNoLeidas,
    VideoConfigRespuesta,
    VideoSesionCrear,
    VideoSesionResumen,
)

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bridgets")


# ----------------------------------------------------------------------
# Esquema y migraciones
# ----------------------------------------------------------------------

Base.metadata.create_all(bind=engine)
aplicar_migraciones_idempotentes(engine)

app = FastAPI(title="Bridgets API")


# ----------------------------------------------------------------------
# CORS
# ----------------------------------------------------------------------
# Lista de orígenes permitidos por env (separados por coma).
# - Si está definida → CORS estricto + allow_credentials=True (para enviar Authorization).
# - Si está vacía    → fallback permisivo (["*"]) sin credenciales (FastAPI rechaza ambos).
_origenes_env = (os.getenv("BRIDGETS_CORS_ORIGINS") or "").strip()
if _origenes_env:
    origenes = [o.strip() for o in _origenes_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origenes,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("CORS estricto habilitado para orígenes: %s", origenes)
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.warning(
        "CORS abierto a '*' (sin credenciales). Define BRIDGETS_CORS_ORIGINS "
        "en producción para restringir."
    )


# ----------------------------------------------------------------------
# Middleware: límite de tamaño de petición
# ----------------------------------------------------------------------
# Cubre tanto JSON normal como subida de archivos. El endpoint de upload
# valida además contra MAX_UPLOAD_BYTES (más estricto sobre el blob).

MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(30 * 1024 * 1024)))  # 30 MB


class LimiteTamañoBodyMiddleware(BaseHTTPMiddleware):
    """Rechaza con 413 si Content-Length supera MAX_REQUEST_BYTES."""

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": f"Petición excede {MAX_REQUEST_BYTES} bytes."},
                    )
            except ValueError:
                pass
        return await call_next(request)


app.add_middleware(LimiteTamañoBodyMiddleware)


# ----------------------------------------------------------------------
# Middleware: métricas in-memory
# ----------------------------------------------------------------------

class MetricasMiddleware(BaseHTTPMiddleware):
    """Cuenta requests, las clasifica por método y status, y mide latencia."""

    async def dispatch(self, request: Request, call_next):
        import time as _time
        inicio = _time.monotonic()
        try:
            respuesta = await call_next(request)
            status_code = respuesta.status_code
            return respuesta
        except Exception:
            status_code = 500
            raise
        finally:
            metricas.registrar(
                metodo=request.method,
                status=status_code,
                duracion_seg=_time.monotonic() - inicio,
            )


app.add_middleware(MetricasMiddleware)


# ----------------------------------------------------------------------
# Salud
# ----------------------------------------------------------------------

@app.get("/")
def estado_servidor():
    return {"estado": "ok", "mensaje": "Servidor activo en Render conectado a Neon"}


@app.get("/healthz")
def estado_health(db: Session = Depends(obtener_sesion)):
    """
    Health check para orquestadores / monitorización. Verifica conectividad con
    la base. Devuelve 200 incluso si la DB falla, pero con el campo `db` en
    'error' (algunos orquestadores prefieren 503 — se puede ajustar).
    """
    try:
        db.execute(text("SELECT 1"))
        estado_db = "ok"
    except Exception as exc:
        logger.error("healthz: fallo al conectar con la base: %s", exc)
        estado_db = "error"
    return {"status": "ok" if estado_db == "ok" else "degraded", "db": estado_db}


@app.get("/metrics")
def estado_metrics():
    """
    Métricas in-memory (no acumulan entre reinicios) en formato JSON.
    Pensado para uptime checks y dashboards simples sin dependencia Prometheus.
    Incluye el conteo de conexiones WS activas en el momento de la consulta.
    """
    snap = metricas.snapshot()
    snap["websocket_conexiones"] = len(gestor_conexiones._conexiones)
    return snap


# ----------------------------------------------------------------------
# Admin (token via env BRIDGETS_ADMIN_TOKEN)
# ----------------------------------------------------------------------

def _exigir_admin(request: Request) -> None:
    """
    Gate mínimo para endpoints administrativos: header `X-Admin-Token` debe
    coincidir con env `BRIDGETS_ADMIN_TOKEN`. Si la env no está configurada,
    el endpoint queda explícitamente deshabilitado (no se puede llamar).
    """
    esperado = os.getenv("BRIDGETS_ADMIN_TOKEN", "").strip()
    if not esperado:
        raise HTTPException(status_code=503, detail="Admin no configurado.")
    enviado = request.headers.get("x-admin-token", "").strip()
    if enviado != esperado:
        raise HTTPException(status_code=403, detail="Token de administración inválido.")


@app.post("/admin/limpieza")
def endpoint_admin_limpieza(
    request: Request,
    dias_revocaciones: int = Query(0, ge=0, description="0 = solo expiradas reales"),
    dias_notificaciones: int = Query(30, ge=1),
    dias_mensajes_cerrados: int = Query(90, ge=1),
    db: Session = Depends(obtener_sesion),
):
    """
    Ejecuta tareas de mantenimiento. Diseñado para llamarse desde un cron
    externo. Devuelve cuántas filas eliminó cada limpieza.
    """
    _exigir_admin(request)
    revocaciones = limpiar_revocaciones_expiradas(db)
    notificaciones = ControladorNotificaciones(db).limpiar_leidas_antiguas(
        dias=dias_notificaciones,
    )
    mensajes = ControladorChat(db).limpiar_mensajes_de_salas_cerradas_antiguas(
        dias=dias_mensajes_cerrados,
    )
    logger.info(
        "limpieza ejecutada: revocaciones=%d notificaciones=%d mensajes=%d",
        revocaciones, notificaciones, mensajes,
    )
    return {
        "revocaciones_eliminadas": revocaciones,
        "notificaciones_eliminadas": notificaciones,
        "mensajes_eliminados": mensajes,
    }


# ----------------------------------------------------------------------
# Cuentas
# ----------------------------------------------------------------------

_PATRON_USUARIO = re.compile(r"^[A-Za-z0-9_.-]{3,20}$")
_PATRON_CORREO = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")


def _ip_de(request: Request) -> str:
    return (request.client.host if request.client else "anon") or "anon"


def _exigir_rate_limit(limitador, ip: str, etiqueta: str, mensaje_429: str):
    """Emite 429 si la IP excede el límite. Loguea para detectar abuso."""
    if not limitador.permitir(ip):
        logger.warning("rate-limit %s alcanzado por %s", etiqueta, ip)
        raise HTTPException(status_code=429, detail=mensaje_429)


@app.post("/api/registro", status_code=status.HTTP_201_CREATED)
def endpoint_registro(
    peticion: RegistroPeticion,
    request: Request,
    db: Session = Depends(obtener_sesion),
):
    _exigir_rate_limit(
        limitador_registro,
        _ip_de(request),
        "registro",
        "Demasiados intentos de registro. Espera unos minutos.",
    )
    controlador = ControladorUsuarios(db)
    exito, mensaje = controlador.registrar_nuevo_usuario(peticion)
    if not exito:
        raise HTTPException(status_code=400, detail=mensaje)
    auditar(db, "registro", ip=_ip_de(request), detalles=peticion.usuario_login)
    return {"mensaje": mensaje}


@app.get("/api/registro/disponibilidad", response_model=DisponibilidadRespuesta)
def endpoint_disponibilidad(
    request: Request,
    usuario: str | None = Query(None, max_length=40),
    correo: str | None = Query(None, max_length=120),
    db: Session = Depends(obtener_sesion),
):
    _exigir_rate_limit(
        limitador_disponibilidad,
        _ip_de(request),
        "disponibilidad",
        "Demasiadas consultas. Intenta de nuevo en un momento.",
    )

    if not usuario and not correo:
        raise HTTPException(status_code=400, detail="Indica 'usuario' o 'correo'.")

    if usuario is not None:
        usuario = usuario.strip()
        if not _PATRON_USUARIO.match(usuario):
            return DisponibilidadRespuesta(disponible=False, motivo="Formato no válido")
        existe = db.query(UsuarioDB.id).filter(UsuarioDB.usuario_login == usuario).first() is not None
        return DisponibilidadRespuesta(disponible=not existe)

    correo_norm = (correo or "").strip().lower()
    if not _PATRON_CORREO.match(correo_norm):
        return DisponibilidadRespuesta(disponible=False, motivo="Formato no válido")
    existe = db.query(UsuarioDB.id).filter(UsuarioDB.correo_electronico == correo_norm).first() is not None
    return DisponibilidadRespuesta(disponible=not existe)


@app.post("/api/login", response_model=SesionRespuesta)
def endpoint_login(
    peticion: LoginPeticion,
    request: Request,
    db: Session = Depends(obtener_sesion),
):
    _exigir_rate_limit(
        limitador_login,
        _ip_de(request),
        "login",
        "Demasiados intentos de inicio de sesión. Espera unos minutos.",
    )
    controlador = ControladorUsuarios(db)
    exito, resultado = controlador.autenticar_usuario(peticion.usuario_login, peticion.password)
    if not exito:
        auditar(db, "login_fallido", ip=_ip_de(request), detalles=peticion.usuario_login)
        raise HTTPException(status_code=401, detail=resultado)
    auditar(
        db, "login_exitoso", actor_id=resultado["id"],
        ip=_ip_de(request), detalles=peticion.usuario_login,
    )
    return resultado


@app.get("/api/yo", response_model=SesionRespuesta)
def endpoint_yo(usuario: UsuarioDB = Depends(obtener_usuario_actual)):
    return SesionRespuesta(
        token="",
        id=usuario.id,
        nombre=usuario.nombre_completo,
        rol=usuario.tipo_cuenta,
        usuario=usuario.usuario_login,
    )


@app.patch("/api/yo", response_model=SesionRespuesta)
def endpoint_actualizar_perfil(
    datos: PerfilActualizar,
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    exito, resultado = ControladorUsuarios(db).actualizar_perfil(usuario, datos)
    if not exito:
        raise HTTPException(status_code=400, detail=resultado)
    return SesionRespuesta(
        token="",
        id=resultado.id,
        nombre=resultado.nombre_completo,
        rol=resultado.tipo_cuenta,
        usuario=resultado.usuario_login,
    )


@app.post("/api/yo/password", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_cambiar_password(
    datos: CambiarPasswordPeticion,
    request: Request,
    usuario_y_payload: tuple = Depends(obtener_usuario_y_payload),
    db: Session = Depends(obtener_sesion),
):
    """
    Cambia la contraseña tras verificar la actual. Revoca el token usado
    en la petición para forzar re-login (buena práctica de seguridad).
    """
    usuario, payload = usuario_y_payload
    exito, mensaje = ControladorUsuarios(db).cambiar_password(usuario, datos)
    if not exito:
        raise HTTPException(status_code=400, detail=mensaje)
    revocar_token(db, payload)
    auditar(db, "password_cambiada", actor_id=usuario.id, ip=_ip_de(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/api/yo", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_eliminar_cuenta(
    datos: EliminarCuentaPeticion,
    request: Request,
    usuario_y_payload: tuple = Depends(obtener_usuario_y_payload),
    db: Session = Depends(obtener_sesion),
):
    usuario, payload = usuario_y_payload
    # Revocamos primero (mientras el usuario aún existe en BD); si la
    # eliminación falla, el token queda revocado de todas formas — comportamiento
    # conservador: nadie sigue usando una sesión sobre una cuenta a punto de irse.
    revocar_token(db, payload)
    user_id = usuario.id
    user_login = usuario.usuario_login
    exito, mensaje = ControladorUsuarios(db).eliminar_cuenta(usuario, datos.password)
    if not exito:
        raise HTTPException(status_code=400, detail=mensaje)
    # Después del delete el actor_id ya no apunta a un usuario vivo, lo dejamos
    # como referencia histórica con detalles del login eliminado.
    auditar(
        db, "cuenta_eliminada", actor_id=user_id,
        ip=_ip_de(request), detalles=user_login,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/logout", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_logout(
    request: Request,
    usuario_y_payload: tuple = Depends(obtener_usuario_y_payload),
    db: Session = Depends(obtener_sesion),
):
    """Revoca el JWT (jti se añade a la blacklist hasta su `exp`)."""
    usuario, payload = usuario_y_payload
    revocar_token(db, payload)
    auditar(db, "logout", actor_id=usuario.id, ip=_ip_de(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------
# Clases
# ----------------------------------------------------------------------

@app.get("/api/clases/mis", response_model=list[ClaseResumen])
def endpoint_mis_clases(
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    return ControladorClases(db).listar_mis_clases(usuario)


@app.get("/api/clases/buscar", response_model=list[ClaseResumen])
def endpoint_buscar_clases(
    q: str = Query("", max_length=80),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    return ControladorClases(db).buscar(q, usuario)


@app.post("/api/clases", response_model=ClaseDetalle, status_code=status.HTTP_201_CREATED)
def endpoint_crear_clase(
    datos: ClaseCrear,
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    return ControladorClases(db).crear_clase(datos, tutor)


@app.get("/api/clases/{clase_id}", response_model=ClaseDetalle)
def endpoint_detalle_clase(
    clase_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    detalle = ControladorClases(db).detalle_para_usuario(clase_id, usuario)
    if detalle is None:
        raise HTTPException(status_code=404, detail="Clase no encontrada.")
    return detalle


@app.post("/api/clases/{clase_id}/inscripcion", status_code=status.HTTP_201_CREATED)
def endpoint_inscribir(
    clase_id: int = Path(..., ge=1),
    peticion: InscripcionPeticion | None = None,
    usuario: UsuarioDB = Depends(exigir_rol("estudiante")),
    db: Session = Depends(obtener_sesion),
):
    codigo = peticion.codigo_clase if peticion else None
    exito, resultado = ControladorClases(db).inscribir_estudiante(
        usuario,
        clase_id=clase_id,
        codigo_clase=codigo,
    )
    if not exito:
        raise HTTPException(status_code=400, detail=resultado)
    return {"mensaje": "Inscripción exitosa.", "clase_id": resultado}


@app.post("/api/inscripciones", status_code=status.HTTP_201_CREATED)
def endpoint_inscribir_por_codigo(
    peticion: InscripcionPeticion,
    usuario: UsuarioDB = Depends(exigir_rol("estudiante")),
    db: Session = Depends(obtener_sesion),
):
    if not peticion.codigo_clase:
        raise HTTPException(status_code=400, detail="Falta el código de la clase.")
    exito, resultado = ControladorClases(db).inscribir_estudiante(
        usuario,
        codigo_clase=peticion.codigo_clase,
    )
    if not exito:
        raise HTTPException(status_code=400, detail=resultado)
    return {"mensaje": "Inscripción exitosa.", "clase_id": resultado}


@app.patch("/api/clases/{clase_id}", response_model=ClaseDetalle)
def endpoint_actualizar_clase(
    datos: ClaseActualizar,
    clase_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_tutor_propietario(db, clase_id, tutor)
    return ControladorClases(db).actualizar(clase, datos, tutor)


@app.delete("/api/clases/{clase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def endpoint_eliminar_clase(
    request: Request,
    clase_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_tutor_propietario(db, clase_id, tutor)
    nombre = clase.nombre
    # Tomamos los ids de inscritos ANTES de borrar (la cascada los borra junto
    # con la clase y los necesitamos para notificarles).
    ctrl = ControladorClases(db)
    inscritos = ctrl.listar_estudiantes_ids(clase_id)
    ctrl.eliminar(clase)
    auditar(
        db, "clase_eliminada", actor_id=tutor.id,
        recurso_tipo="clase", recurso_id=clase_id,
        ip=_ip_de(request), detalles=nombre,
    )
    # Aviso por WS a los inscritos para que su cliente limpie la UI sin
    # esperar a un refresh manual. El propio tutor también recibe el evento
    # (útil si tenía la app abierta en otra sesión).
    await gestor_conexiones.enviar_a_varios(
        list(set(inscritos + [tutor.id])),
        {"tipo": "clase_eliminada", "clase_id": clase_id, "nombre": nombre},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/clases/{clase_id}/estudiantes", response_model=list[EstudianteResumen])
def endpoint_listar_estudiantes(
    clase_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_tutor_propietario(db, clase_id, tutor)
    return ControladorClases(db).listar_estudiantes(clase)


@app.delete("/api/clases/{clase_id}/inscripcion", status_code=status.HTTP_204_NO_CONTENT)
async def endpoint_desinscribirse(
    clase_id: int = Path(..., ge=1),
    estudiante: UsuarioDB = Depends(exigir_rol("estudiante")),
    db: Session = Depends(obtener_sesion),
):
    """Baja voluntaria del estudiante. Cierra cualquier sala de chat que tuviera en la clase."""
    ctrl = ControladorClases(db)
    clase = ctrl.obtener_clase(clase_id)
    if clase is None:
        raise HTTPException(status_code=404, detail="Clase no encontrada.")
    chat = ControladorChat(db)
    salas_cerradas = chat.cerrar_por_clase_y_estudiante(clase_id, estudiante.id)
    if not ctrl.desinscribir(clase, estudiante):
        raise HTTPException(status_code=404, detail="No estás inscrito en esta clase.")
    for sala in salas_cerradas:
        await gestor_conexiones.enviar_a_varios(
            [sala.estudiante_id, sala.tutor_id],
            {"tipo": "sala_cerrada", "sala_id": sala.id},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete(
    "/api/clases/{clase_id}/estudiantes/{estudiante_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def endpoint_expulsar_estudiante(
    request: Request,
    clase_id: int = Path(..., ge=1),
    estudiante_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_tutor_propietario(db, clase_id, tutor)
    if estudiante_id == tutor.id:
        raise HTTPException(status_code=400, detail="No puedes expulsarte a ti mismo.")
    estudiante = db.query(UsuarioDB).filter(UsuarioDB.id == estudiante_id).first()
    if estudiante is None:
        raise HTTPException(status_code=404, detail="Estudiante no encontrado.")
    chat = ControladorChat(db)
    salas_cerradas = chat.cerrar_por_clase_y_estudiante(clase_id, estudiante_id)
    if not ControladorClases(db).desinscribir(clase, estudiante):
        raise HTTPException(status_code=404, detail="El estudiante no está inscrito en esta clase.")
    auditar(
        db, "estudiante_expulsado", actor_id=tutor.id,
        recurso_tipo="clase", recurso_id=clase_id,
        ip=_ip_de(request), detalles=f"estudiante_id={estudiante_id}",
    )
    for sala in salas_cerradas:
        await gestor_conexiones.enviar_a_varios(
            [sala.estudiante_id, sala.tutor_id],
            {"tipo": "sala_cerrada", "sala_id": sala.id},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------
# Helper: acceso a clase
# ----------------------------------------------------------------------

def _exigir_acceso_clase(db: Session, clase_id: int, usuario: UsuarioDB):
    ctrl = ControladorClases(db)
    clase = ctrl.obtener_clase(clase_id)
    if clase is None:
        raise HTTPException(status_code=404, detail="Clase no encontrada.")
    if not ctrl.usuario_puede_ver(clase, usuario):
        raise HTTPException(status_code=403, detail="No tienes acceso a esta clase.")
    return clase


def _exigir_tutor_propietario(db: Session, clase_id: int, tutor: UsuarioDB):
    """Devuelve la clase si el tutor es el dueño; si no, 404/403 según el caso."""
    clase = ControladorClases(db).obtener_clase(clase_id)
    if clase is None:
        raise HTTPException(status_code=404, detail="Clase no encontrada.")
    if clase.tutor_id != tutor.id:
        raise HTTPException(
            status_code=403,
            detail="Solo el tutor dueño de la clase puede realizar esta acción.",
        )
    return clase


# ----------------------------------------------------------------------
# Notificaciones in-app
# ----------------------------------------------------------------------

async def _notificar(
    db: Session,
    destinatarios: list[int],
    tipo: str,
    titulo: str,
    cuerpo: str | None = None,
    enlace_tipo: str | None = None,
    enlace_id: int | None = None,
) -> None:
    """
    Persiste una notificación por destinatario y, en el mismo flujo, hace push
    por WebSocket a quienes estén conectados. Si la lista llega vacía, no-op.
    """
    if not destinatarios:
        return
    ctrl = ControladorNotificaciones(db)
    creadas = ctrl.crear_para_varios(
        destinatarios=destinatarios,
        tipo=tipo,
        titulo=titulo,
        cuerpo=cuerpo,
        enlace_tipo=enlace_tipo,
        enlace_id=enlace_id,
    )
    for n in creadas:
        payload = NotificacionResumen.model_validate(n).model_dump(mode="json")
        await gestor_conexiones.enviar_a(n.destinatario_id, {
            "tipo": "notificacion_nueva",
            "notificacion": payload,
        })


# ----------------------------------------------------------------------
# Anuncios
# ----------------------------------------------------------------------

@app.get("/api/clases/{clase_id}/anuncios", response_model=list[AnuncioResumen])
def endpoint_listar_anuncios(
    clase_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    _exigir_acceso_clase(db, clase_id, usuario)
    return ControladorAnuncios(db).listar(clase_id)


@app.post("/api/clases/{clase_id}/anuncios", response_model=AnuncioResumen, status_code=status.HTTP_201_CREATED)
async def endpoint_crear_anuncio(
    datos: AnuncioCrear,
    clase_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_acceso_clase(db, clase_id, tutor)
    if clase.tutor_id != tutor.id:
        raise HTTPException(status_code=403, detail="Solo el tutor de la clase puede publicar anuncios.")
    creado = ControladorAnuncios(db).crear(clase_id, tutor, datos)
    # Notificar a todos los inscritos.
    inscritos = ControladorClases(db).listar_estudiantes_ids(clase_id)
    await _notificar(
        db, inscritos,
        tipo="anuncio_nuevo",
        titulo=f"📢 {clase.nombre}: {creado.titulo}",
        cuerpo=creado.contenido[:200],
        enlace_tipo="clase",
        enlace_id=clase_id,
    )
    return creado


def _cargar_anuncio_o_404(db: Session, anuncio_id: int, tutor: UsuarioDB):
    """Devuelve el anuncio si existe y el tutor es dueño de su clase."""
    ctrl_anuncios = ControladorAnuncios(db)
    anuncio = ctrl_anuncios.obtener(anuncio_id)
    if anuncio is None:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado.")
    _exigir_tutor_propietario(db, anuncio.clase_id, tutor)
    return ctrl_anuncios, anuncio


@app.patch("/api/anuncios/{anuncio_id}", response_model=AnuncioResumen)
def endpoint_actualizar_anuncio(
    datos: AnuncioActualizar,
    anuncio_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    ctrl_anuncios, anuncio = _cargar_anuncio_o_404(db, anuncio_id, tutor)
    return ctrl_anuncios.actualizar(anuncio, tutor, datos)


@app.delete("/api/anuncios/{anuncio_id}", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_eliminar_anuncio(
    anuncio_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    ctrl_anuncios, anuncio = _cargar_anuncio_o_404(db, anuncio_id, tutor)
    ctrl_anuncios.eliminar(anuncio)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------
# Comentarios de anuncios
# ----------------------------------------------------------------------

def _cargar_anuncio_y_acceso(db: Session, anuncio_id: int, usuario: UsuarioDB):
    """Devuelve el anuncio si existe y el usuario tiene acceso a su clase."""
    anuncio = ControladorAnuncios(db).obtener(anuncio_id)
    if anuncio is None:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado.")
    _exigir_acceso_clase(db, anuncio.clase_id, usuario)
    return anuncio


@app.get("/api/anuncios/{anuncio_id}/comentarios", response_model=list[ComentarioResumen])
def endpoint_listar_comentarios(
    anuncio_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    _cargar_anuncio_y_acceso(db, anuncio_id, usuario)
    return ControladorComentarios(db).listar(anuncio_id)


@app.post(
    "/api/anuncios/{anuncio_id}/comentarios",
    response_model=ComentarioResumen,
    status_code=status.HTTP_201_CREATED,
)
async def endpoint_crear_comentario(
    datos: ComentarioCrear,
    anuncio_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    anuncio = _cargar_anuncio_y_acceso(db, anuncio_id, usuario)
    creado = ControladorComentarios(db).crear(anuncio_id, usuario, datos)

    # Notificar a quienes se hayan suscrito al anuncio (excepto al autor del
    # comentario, que ya sabe que lo escribió). El autor del anuncio queda
    # incluido si se ha suscrito explícitamente — no se autoasume.
    suscritos = (
        db.query(SuscripcionAnuncioDB.usuario_id)
        .filter(
            SuscripcionAnuncioDB.anuncio_id == anuncio_id,
            SuscripcionAnuncioDB.usuario_id != usuario.id,
        )
        .all()
    )
    destinatarios = [s.usuario_id for s in suscritos]
    if destinatarios:
        clase = ControladorClases(db).obtener_clase(anuncio.clase_id)
        clase_nombre = clase.nombre if clase else "una clase"
        await _notificar(
            db, destinatarios,
            tipo="comentario_nuevo",
            titulo=f"💬 Nuevo comentario en «{anuncio.titulo}»",
            cuerpo=f"{usuario.nombre_completo} en {clase_nombre}: {datos.contenido[:140]}",
            enlace_tipo="anuncio",
            enlace_id=anuncio_id,
        )
    return creado


# -------------------- Suscripciones a anuncios --------------------
# Modelo: opt-in. Solo los usuarios que explícitamente se suscriben reciben
# notificación de nuevos comentarios en ese anuncio. Tutor y estudiantes
# usan el mismo flujo.

def _cargar_anuncio_para_suscripcion(db: Session, anuncio_id: int, usuario: UsuarioDB) -> AnuncioClaseDB:
    """Comparte la validación con `_cargar_anuncio_y_acceso`: el caller debe
    tener acceso a la clase del anuncio para poder gestionar su suscripción.
    Nombre distinto del `_cargar_anuncio_o_404` ya existente (que devuelve
    una tupla y aplica reglas de tutor) para no colisionar."""
    return _cargar_anuncio_y_acceso(db, anuncio_id, usuario)


@app.get("/api/anuncios/{anuncio_id}/suscripcion")
def endpoint_estado_suscripcion_anuncio(
    anuncio_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    _cargar_anuncio_para_suscripcion(db, anuncio_id, usuario)
    existe = (
        db.query(SuscripcionAnuncioDB.id)
        .filter(
            SuscripcionAnuncioDB.usuario_id == usuario.id,
            SuscripcionAnuncioDB.anuncio_id == anuncio_id,
        )
        .first()
    )
    return {"suscrito": existe is not None}


@app.post("/api/anuncios/{anuncio_id}/suscripcion", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_suscribir_anuncio(
    anuncio_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    _cargar_anuncio_para_suscripcion(db, anuncio_id, usuario)
    existente = (
        db.query(SuscripcionAnuncioDB)
        .filter(
            SuscripcionAnuncioDB.usuario_id == usuario.id,
            SuscripcionAnuncioDB.anuncio_id == anuncio_id,
        )
        .first()
    )
    if existente is None:
        db.add(SuscripcionAnuncioDB(usuario_id=usuario.id, anuncio_id=anuncio_id))
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/api/anuncios/{anuncio_id}/suscripcion", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_desuscribir_anuncio(
    anuncio_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    _cargar_anuncio_para_suscripcion(db, anuncio_id, usuario)
    db.query(SuscripcionAnuncioDB).filter(
        SuscripcionAnuncioDB.usuario_id == usuario.id,
        SuscripcionAnuncioDB.anuncio_id == anuncio_id,
    ).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/api/comentarios/{comentario_id}", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_eliminar_comentario(
    comentario_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    ctrl = ControladorComentarios(db)
    comentario = ctrl.obtener(comentario_id)
    if comentario is None:
        raise HTTPException(status_code=404, detail="Comentario no encontrado.")
    # Borrar: el autor del comentario o el tutor dueño de la clase del anuncio.
    anuncio = ControladorAnuncios(db).obtener(comentario.anuncio_id)
    if anuncio is None:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado.")
    clase = ControladorClases(db).obtener_clase(anuncio.clase_id)
    es_autor = comentario.autor_id == usuario.id
    es_tutor_clase = clase is not None and clase.tutor_id == usuario.id
    if not (es_autor or es_tutor_clase):
        raise HTTPException(
            status_code=403,
            detail="Solo el autor del comentario o el tutor de la clase pueden eliminarlo.",
        )
    ctrl.eliminar(comentario)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------
# Archivos
# ----------------------------------------------------------------------

@app.get("/api/clases/{clase_id}/archivos")
def endpoint_listar_archivos(
    clase_id: int = Path(..., ge=1),
    categoria: str | None = Query(None, max_length=40),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    _exigir_acceso_clase(db, clase_id, usuario)
    return ControladorArchivos(db).listar(clase_id, categoria=categoria)


@app.get("/api/clases/{clase_id}/archivos/categorias", response_model=list[str])
def endpoint_categorias_archivos(
    clase_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    _exigir_acceso_clase(db, clase_id, usuario)
    return ControladorArchivos(db).listar_categorias(clase_id)


@app.post("/api/clases/{clase_id}/archivos", status_code=status.HTTP_201_CREATED)
async def endpoint_subir_archivo(
    clase_id: int = Path(..., ge=1),
    archivo: UploadFile = File(...),
    categoria: str | None = Query(None, max_length=40),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_acceso_clase(db, clase_id, tutor)
    if clase.tutor_id != tutor.id:
        raise HTTPException(status_code=403, detail="Solo el tutor de la clase puede subir archivos.")

    contenido = await archivo.read()
    exito, resultado = ControladorArchivos(db).guardar(
        clase_id=clase_id,
        autor=tutor,
        nombre_original=archivo.filename or "archivo",
        mime=archivo.content_type or "application/octet-stream",
        contenido=contenido,
        categoria=categoria,
    )
    if not exito:
        raise HTTPException(status_code=400, detail=resultado)
    inscritos = ControladorClases(db).listar_estudiantes_ids(clase_id)
    await _notificar(
        db, inscritos,
        tipo="archivo_nuevo",
        titulo=f"📎 {clase.nombre}: nuevo archivo",
        cuerpo=resultado.nombre_original,
        enlace_tipo="clase",
        enlace_id=clase_id,
    )
    return resultado


@app.patch("/api/archivos/{archivo_id}")
def endpoint_actualizar_archivo(
    datos: ArchivoActualizar,
    archivo_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    ctrl = ControladorArchivos(db)
    archivo = ctrl.obtener(archivo_id)
    if archivo is None:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    _exigir_tutor_propietario(db, archivo.clase_id, tutor)
    return ctrl.actualizar(archivo, tutor, datos)


@app.get("/api/archivos/{archivo_id}/descargar")
def endpoint_descargar_archivo(
    archivo_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    archivo = ControladorArchivos(db).obtener_para_descarga(archivo_id)
    if archivo is None or archivo.blob is None:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    _exigir_acceso_clase(db, archivo.clase_id, usuario)
    nombre_seguro = archivo.nombre_original.replace('"', '')
    return Response(
        content=archivo.blob.contenido,
        media_type=archivo.mime,
        headers={"Content-Disposition": f'attachment; filename="{nombre_seguro}"'},
    )


@app.delete("/api/archivos/{archivo_id}", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_eliminar_archivo(
    archivo_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    ctrl = ControladorArchivos(db)
    archivo = ctrl.obtener(archivo_id)
    if archivo is None:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    _exigir_tutor_propietario(db, archivo.clase_id, tutor)
    ctrl.eliminar(archivo)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------
# Notas
# ----------------------------------------------------------------------

@app.get("/api/clases/{clase_id}/notas", response_model=list[NotaResumen])
def endpoint_listar_notas(
    clase_id: int = Path(..., ge=1),
    estudiante: UsuarioDB = Depends(exigir_rol("estudiante")),
    db: Session = Depends(obtener_sesion),
):
    _exigir_acceso_clase(db, clase_id, estudiante)
    return ControladorNotas(db).listar(clase_id, estudiante)


@app.post("/api/clases/{clase_id}/notas", response_model=NotaResumen, status_code=status.HTTP_201_CREATED)
def endpoint_crear_nota(
    datos: NotaCrear,
    clase_id: int = Path(..., ge=1),
    estudiante: UsuarioDB = Depends(exigir_rol("estudiante")),
    db: Session = Depends(obtener_sesion),
):
    _exigir_acceso_clase(db, clase_id, estudiante)
    return ControladorNotas(db).crear(clase_id, estudiante, datos)


@app.put("/api/notas/{nota_id}", response_model=NotaResumen)
def endpoint_actualizar_nota(
    datos: NotaActualizar,
    nota_id: int = Path(..., ge=1),
    estudiante: UsuarioDB = Depends(exigir_rol("estudiante")),
    db: Session = Depends(obtener_sesion),
):
    actualizada = ControladorNotas(db).actualizar(nota_id, estudiante, datos)
    if actualizada is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    return actualizada


@app.delete("/api/notas/{nota_id}", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_eliminar_nota(
    nota_id: int = Path(..., ge=1),
    estudiante: UsuarioDB = Depends(exigir_rol("estudiante")),
    db: Session = Depends(obtener_sesion),
):
    if not ControladorNotas(db).eliminar(nota_id, estudiante):
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------
# Tareas y entregas
# ----------------------------------------------------------------------

def _cargar_tarea_o_404(db: Session, tarea_id: int):
    tarea = ControladorTareas(db).obtener(tarea_id)
    if tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return tarea


@app.get("/api/clases/{clase_id}/tareas", response_model=list[TareaResumen])
def endpoint_listar_tareas(
    clase_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    _exigir_acceso_clase(db, clase_id, usuario)
    return ControladorTareas(db).listar_para_usuario(clase_id, usuario)


@app.post(
    "/api/clases/{clase_id}/tareas",
    response_model=TareaResumen,
    status_code=status.HTTP_201_CREATED,
)
async def endpoint_crear_tarea(
    datos: TareaCrear,
    clase_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_tutor_propietario(db, clase_id, tutor)
    ctrl = ControladorTareas(db)
    tarea = ctrl.crear(clase_id, tutor, datos)
    # Reusamos el listado para volver el resumen con autor_nombre y total_entregas=0.
    resumenes = ctrl.listar_para_usuario(clase_id, tutor)
    creada = next((r for r in resumenes if r.id == tarea.id), None)
    if creada is None:
        raise HTTPException(status_code=500, detail="No se pudo construir el resumen de la tarea.")
    inscritos = ControladorClases(db).listar_estudiantes_ids(clase_id)
    await _notificar(
        db, inscritos,
        tipo="tarea_nueva",
        titulo=f"📝 {clase.nombre}: nueva tarea",
        cuerpo=creada.titulo,
        enlace_tipo="clase",
        enlace_id=clase_id,
    )
    return creada


@app.patch("/api/tareas/{tarea_id}", response_model=TareaResumen)
def endpoint_actualizar_tarea(
    datos: TareaActualizar,
    tarea_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    tarea = _cargar_tarea_o_404(db, tarea_id)
    _exigir_tutor_propietario(db, tarea.clase_id, tutor)
    ctrl = ControladorTareas(db)
    ctrl.actualizar(tarea, datos)
    resumenes = ctrl.listar_para_usuario(tarea.clase_id, tutor)
    return next(r for r in resumenes if r.id == tarea.id)


@app.delete("/api/tareas/{tarea_id}", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_eliminar_tarea(
    tarea_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    tarea = _cargar_tarea_o_404(db, tarea_id)
    _exigir_tutor_propietario(db, tarea.clase_id, tutor)
    ControladorTareas(db).eliminar(tarea)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/tareas/{tarea_id}/entregas", response_model=list[EntregaResumen])
def endpoint_listar_entregas(
    tarea_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    tarea = _cargar_tarea_o_404(db, tarea_id)
    _exigir_tutor_propietario(db, tarea.clase_id, tutor)
    return ControladorTareas(db).listar_entregas(tarea)


@app.post(
    "/api/tareas/{tarea_id}/entregar",
    response_model=EntregaResumen,
    status_code=status.HTTP_201_CREATED,
)
def endpoint_entregar(
    datos: EntregaCrear,
    tarea_id: int = Path(..., ge=1),
    estudiante: UsuarioDB = Depends(exigir_rol("estudiante")),
    db: Session = Depends(obtener_sesion),
):
    tarea = _cargar_tarea_o_404(db, tarea_id)
    _exigir_acceso_clase(db, tarea.clase_id, estudiante)
    exito, resultado = ControladorTareas(db).entregar(tarea, estudiante, datos)
    if not exito:
        raise HTTPException(status_code=400, detail=resultado)
    return resultado


@app.post("/api/entregas/{entrega_id}/calificar", response_model=EntregaResumen)
async def endpoint_calificar(
    datos: CalificarPeticion,
    entrega_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    ctrl = ControladorTareas(db)
    entrega = ctrl.obtener_entrega(entrega_id)
    if entrega is None:
        raise HTTPException(status_code=404, detail="Entrega no encontrada.")
    tarea = ctrl.obtener(entrega.tarea_id)
    if tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    _exigir_tutor_propietario(db, tarea.clase_id, tutor)
    exito, resultado = ctrl.calificar(entrega, tutor, datos, max_puntos=tarea.max_puntos)
    if not exito:
        raise HTTPException(status_code=400, detail=resultado)
    # Notificar al estudiante que recibió la calificación.
    await _notificar(
        db, [entrega.estudiante_id],
        tipo="calificacion_recibida",
        titulo=f"✓ Calificación: {tarea.titulo}",
        cuerpo=f"{resultado.calificacion} / {tarea.max_puntos}",
        enlace_tipo="clase",
        enlace_id=tarea.clase_id,
    )
    return resultado


# ----------------------------------------------------------------------
# Birdgt — chat
# ----------------------------------------------------------------------

@app.post("/api/clases/{clase_id}/birdgt", response_model=BirdgtIniciarRespuesta, status_code=status.HTTP_201_CREATED)
async def endpoint_iniciar_birdgt(
    clase_id: int = Path(..., ge=1),
    estudiante_id: Optional[int] = Query(None, ge=1, description="Si lo envía un tutor, abre la sala solo con ese estudiante."),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_acceso_clase(db, clase_id, usuario)
    chat = ControladorChat(db)
    try:
        salas, creadas, reutilizadas = chat.iniciar(clase, usuario, estudiante_id=estudiante_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    resumenes = []
    for sala in salas:
        resumen = chat.resumen_sala(sala, usuario)
        if resumen is None:
            continue
        resumenes.append(resumen)

        # Notificar por WS solo si la sala está pendiente (recién creada o reabierta).
        if sala.estado != "pendiente":
            continue
        otro = chat.contraparte_de(sala, usuario)
        if otro is None:
            continue
        otro_resumen = chat.resumen_sala(sala, otro)
        if otro_resumen is None:
            continue
        await gestor_conexiones.enviar_a(otro.id, {
            "tipo": "solicitud_nueva",
            "sala": otro_resumen.model_dump(mode="json"),
        })
        # Persistimos también la notificación en el centro in-app.
        await _notificar(
            db, [otro.id],
            tipo="chat_solicitud",
            titulo=f"💬 {clase.nombre}: solicitud de chat",
            cuerpo=f"{usuario.nombre_completo} quiere chatear contigo.",
            enlace_tipo="sala_chat",
            enlace_id=sala.id,
        )

    return BirdgtIniciarRespuesta(salas=resumenes, creadas=creadas, reutilizadas=reutilizadas)


@app.get("/api/birdgt/solicitudes", response_model=list[SalaResumen])
def endpoint_solicitudes(
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    return ControladorChat(db).solicitudes_pendientes_para(usuario)


@app.get("/api/birdgt/activas", response_model=list[SalaResumen])
def endpoint_salas_activas(
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    return ControladorChat(db).salas_activas_para(usuario)


def _cargar_sala_o_404(db: Session, sala_id: int, usuario: UsuarioDB):
    chat = ControladorChat(db)
    sala = chat.obtener_sala(sala_id)
    if sala is None:
        raise HTTPException(status_code=404, detail="Sala no encontrada.")
    if not chat.participa(sala, usuario):
        raise HTTPException(status_code=403, detail="No participas en esta sala.")
    return chat, sala


@app.post("/api/birdgt/{sala_id}/aceptar", response_model=SalaResumen)
async def endpoint_aceptar_sala(
    sala_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    chat, sala = _cargar_sala_o_404(db, sala_id, usuario)
    if sala.iniciador_id == usuario.id:
        raise HTTPException(status_code=400, detail="Tú creaste la sala; no requiere aceptación.")
    if sala.estado != "pendiente":
        raise HTTPException(status_code=400, detail=f"Sala en estado '{sala.estado}'.")
    sala = chat.aceptar(sala)

    # Quien acepta no es el iniciador, así que su contraparte ES el iniciador.
    iniciador = chat.contraparte_de(sala, usuario)
    if iniciador is not None:
        resumen_iniciador = chat.resumen_sala(sala, iniciador)
        if resumen_iniciador is not None:
            await gestor_conexiones.enviar_a(iniciador.id, {
                "tipo": "sala_aceptada",
                "sala": resumen_iniciador.model_dump(mode="json"),
            })
    return chat.resumen_sala(sala, usuario)


@app.post("/api/birdgt/{sala_id}/rechazar", response_model=SalaResumen)
async def endpoint_rechazar_sala(
    sala_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    chat, sala = _cargar_sala_o_404(db, sala_id, usuario)
    sala = chat.cerrar(sala)
    await gestor_conexiones.enviar_a_varios(
        [sala.estudiante_id, sala.tutor_id],
        {"tipo": "sala_cerrada", "sala_id": sala.id},
    )
    return chat.resumen_sala(sala, usuario)


@app.post("/api/birdgt/{sala_id}/cerrar", response_model=SalaResumen)
async def endpoint_cerrar_sala(
    sala_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    chat, sala = _cargar_sala_o_404(db, sala_id, usuario)
    sala = chat.cerrar(sala)
    await gestor_conexiones.enviar_a_varios(
        [sala.estudiante_id, sala.tutor_id],
        {"tipo": "sala_cerrada", "sala_id": sala.id},
    )
    return chat.resumen_sala(sala, usuario)


@app.get("/api/birdgt/{sala_id}/mensajes", response_model=list[MensajeResumen])
def endpoint_historial_mensajes(
    sala_id: int = Path(..., ge=1),
    desde: int = Query(0, ge=0, description="Devuelve mensajes con id > desde (sync incremental)."),
    hasta: int = Query(0, ge=0, description="Devuelve los `limite` mensajes anteriores a hasta (paginación hacia atrás)."),
    limite: int = Query(50, ge=1, le=200),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    chat, sala = _cargar_sala_o_404(db, sala_id, usuario)
    return chat.historial(
        sala,
        desde_id=desde,
        hasta_id=hasta if hasta > 0 else None,
        limite=limite,
    )


@app.post("/api/birdgt/{sala_id}/leido", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_marcar_leido(
    sala_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    chat, sala = _cargar_sala_o_404(db, sala_id, usuario)
    chat.marcar_leido(sala, usuario)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/birdgt/{sala_id}/mensajes", response_model=MensajeResumen, status_code=status.HTTP_201_CREATED)
async def endpoint_enviar_mensaje(
    datos: MensajeCrear,
    sala_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    chat, sala = _cargar_sala_o_404(db, sala_id, usuario)
    if sala.estado != "activa":
        raise HTTPException(status_code=400, detail="La sala no está activa.")
    mensaje = chat.enviar_mensaje(sala, usuario, datos.contenido)
    payload = MensajeResumen.model_validate(mensaje).model_dump(mode="json")
    await gestor_conexiones.enviar_a_varios(
        [sala.estudiante_id, sala.tutor_id],
        {"tipo": "mensaje_nuevo", "sala_id": sala.id, "mensaje": payload},
    )
    return mensaje


def _cargar_mensaje_propio_o_404(db: Session, mensaje_id: int, usuario: UsuarioDB):
    """Helper: trae el mensaje, valida autoría, ventana de edición y devuelve (chat, sala, mensaje)."""
    chat = ControladorChat(db)
    mensaje = chat.obtener_mensaje(mensaje_id)
    if mensaje is None:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    if mensaje.autor_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo el autor puede modificar este mensaje.")
    if not chat.es_editable(mensaje):
        raise HTTPException(
            status_code=400,
            detail="La ventana para editar o borrar este mensaje ha expirado.",
        )
    sala = chat.obtener_sala(mensaje.sala_id)
    if sala is None:
        raise HTTPException(status_code=404, detail="Sala no encontrada.")
    return chat, sala, mensaje


@app.patch("/api/mensajes/{mensaje_id}", response_model=MensajeResumen)
async def endpoint_editar_mensaje(
    datos: MensajeActualizar,
    mensaje_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    chat, sala, mensaje = _cargar_mensaje_propio_o_404(db, mensaje_id, usuario)
    mensaje = chat.editar_mensaje(mensaje, datos.contenido)
    payload = MensajeResumen.model_validate(mensaje).model_dump(mode="json")
    await gestor_conexiones.enviar_a_varios(
        [sala.estudiante_id, sala.tutor_id],
        {"tipo": "mensaje_editado", "sala_id": sala.id, "mensaje": payload},
    )
    return mensaje


@app.delete("/api/mensajes/{mensaje_id}", status_code=status.HTTP_204_NO_CONTENT)
async def endpoint_eliminar_mensaje(
    mensaje_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    chat, sala, mensaje = _cargar_mensaje_propio_o_404(db, mensaje_id, usuario)
    sala_id_local = sala.id
    estudiante_id = sala.estudiante_id
    tutor_id = sala.tutor_id
    chat.eliminar_mensaje(mensaje)
    await gestor_conexiones.enviar_a_varios(
        [estudiante_id, tutor_id],
        {"tipo": "mensaje_borrado", "sala_id": sala_id_local, "mensaje_id": mensaje_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/clases/{clase_id}/birdgt/no-leidos", response_model=NoLeidosClase)
def endpoint_no_leidos_clase(
    clase_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    """Total de mensajes no leídos del usuario en todas las salas activas de la clase."""
    _exigir_acceso_clase(db, clase_id, usuario)
    total = ControladorChat(db).total_no_leidos_en_clase(usuario, clase_id)
    return NoLeidosClase(total=total)


# ----------------------------------------------------------------------
# Centro de notificaciones (CRUD)
# ----------------------------------------------------------------------

@app.get("/api/notificaciones", response_model=list[NotificacionResumen])
def endpoint_listar_notificaciones(
    solo_no_leidas: bool = Query(False),
    limite: int = Query(50, ge=1, le=200),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    return ControladorNotificaciones(db).listar_para(
        usuario, solo_no_leidas=solo_no_leidas, limite=limite,
    )


@app.get("/api/notificaciones/no-leidas/total", response_model=TotalNoLeidas)
def endpoint_total_no_leidas(
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    return TotalNoLeidas(total=ControladorNotificaciones(db).total_no_leidas(usuario))


@app.post("/api/notificaciones/{notif_id}/leida", status_code=status.HTTP_204_NO_CONTENT)
def endpoint_marcar_notificacion_leida(
    notif_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    ctrl = ControladorNotificaciones(db)
    notif = ctrl.obtener(notif_id)
    if notif is None or notif.destinatario_id != usuario.id:
        raise HTTPException(status_code=404, detail="Notificación no encontrada.")
    ctrl.marcar_leida(notif)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/notificaciones/leidas", response_model=TotalNoLeidas)
def endpoint_marcar_todas_leidas(
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    afectadas = ControladorNotificaciones(db).marcar_todas_leidas(usuario)
    # Devolvemos el "total" referido a las que se marcaron, para que el cliente
    # refresque su badge sin otra llamada.
    return TotalNoLeidas(total=afectadas)


# ----------------------------------------------------------------------
# Videollamadas (señalización por WS; este bloque solo gestiona estado)
# ----------------------------------------------------------------------

def _cargar_sesion_video_o_404(db: Session, sesion_id: int, usuario: UsuarioDB):
    """Trae la sesión y verifica que el usuario participa; si no, 404/403."""
    ctrl = ControladorVideollamada(db)
    sesion = ctrl.obtener(sesion_id)
    if sesion is None:
        raise HTTPException(status_code=404, detail="Sesión de video no encontrada.")
    if not ctrl.participa(sesion, usuario):
        raise HTTPException(status_code=403, detail="No participas en esta sesión.")
    return ctrl, sesion


def _resumen_video_json(sesion) -> dict:
    return VideoSesionResumen.model_validate(sesion).model_dump(mode="json")


def _duracion_segundos(sesion) -> Optional[int]:
    if sesion.aceptada_en is None or sesion.finalizada_en is None:
        return None
    return int((sesion.finalizada_en - sesion.aceptada_en).total_seconds())


@app.post("/api/video/sesiones", response_model=VideoSesionResumen, status_code=status.HTTP_201_CREATED)
async def endpoint_iniciar_video(
    datos: VideoSesionCrear,
    request: Request,
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    sala = ControladorChat(db).obtener_sala(datos.sala_id)
    if sala is None:
        raise HTTPException(status_code=404, detail="Sala no encontrada.")
    if usuario.id not in (sala.estudiante_id, sala.tutor_id):
        raise HTTPException(status_code=403, detail="No participas en esta sala.")
    ctrl = ControladorVideollamada(db)
    try:
        sesion = ctrl.iniciar(sala, usuario, datos.modo)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    auditar(
        db, "video_iniciada", actor_id=usuario.id,
        recurso_tipo="sesion_video", recurso_id=sesion.id, ip=_ip_de(request),
        detalles=f"sala={sala.id}, modo={datos.modo}",
    )
    metricas.registrar_video("iniciada")
    await gestor_conexiones.enviar_a(sesion.receptor_id, {
        "tipo": "video_solicitud",
        "sesion": _resumen_video_json(sesion),
    })
    # Persistimos también en el centro in-app: si el receptor está offline al
    # llegar la solicitud, sigue viendo "te llamaron" al volver. El enlace
    # apunta a la propia sesión para que el click pueda intentar aceptar.
    await _notificar(
        db, [sesion.receptor_id],
        tipo="video_solicitud",
        titulo=f"📹 {usuario.nombre_completo} te está llamando",
        cuerpo=f"Videollamada en {sala.clase.nombre if sala.clase else 'la clase'}.",
        enlace_tipo="video_sesion",
        enlace_id=sesion.id,
    )
    return sesion


@app.post("/api/video/sesiones/{sesion_id}/aceptar", response_model=VideoSesionResumen)
async def endpoint_aceptar_video(
    request: Request,
    sesion_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    ctrl, sesion = _cargar_sesion_video_o_404(db, sesion_id, usuario)
    try:
        sesion = ctrl.aceptar(sesion, usuario)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    auditar(
        db, "video_aceptada", actor_id=usuario.id,
        recurso_tipo="sesion_video", recurso_id=sesion.id, ip=_ip_de(request),
    )
    metricas.registrar_video("aceptada")
    payload = {"tipo": "video_aceptada", "sesion": _resumen_video_json(sesion)}
    await gestor_conexiones.enviar_a_varios(
        [sesion.iniciador_id, sesion.receptor_id], payload,
    )
    return sesion


@app.post("/api/video/sesiones/{sesion_id}/rechazar", response_model=VideoSesionResumen)
async def endpoint_rechazar_video(
    request: Request,
    sesion_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    ctrl, sesion = _cargar_sesion_video_o_404(db, sesion_id, usuario)
    try:
        sesion = ctrl.rechazar(sesion, usuario)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    auditar(
        db, "video_rechazada", actor_id=usuario.id,
        recurso_tipo="sesion_video", recurso_id=sesion.id, ip=_ip_de(request),
    )
    metricas.registrar_video("rechazada", motivo="rechazada")
    await gestor_conexiones.enviar_a(sesion.iniciador_id, {
        "tipo": "video_rechazada", "sesion": _resumen_video_json(sesion),
    })
    return sesion


@app.post("/api/video/sesiones/{sesion_id}/finalizar", response_model=VideoSesionResumen)
async def endpoint_finalizar_video(
    request: Request,
    sesion_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    ctrl, sesion = _cargar_sesion_video_o_404(db, sesion_id, usuario)
    try:
        sesion = ctrl.finalizar(sesion, usuario, motivo="colgada")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    duracion = _duracion_segundos(sesion)
    detalles = f"motivo={sesion.motivo_fin}"
    if duracion is not None:
        detalles += f", duracion_s={duracion}"
    auditar(
        db, "video_finalizada", actor_id=usuario.id,
        recurso_tipo="sesion_video", recurso_id=sesion.id, ip=_ip_de(request),
        detalles=detalles,
    )
    metricas.registrar_video("finalizada", motivo=sesion.motivo_fin, duracion_seg=duracion)
    contraparte = sesion.receptor_id if usuario.id == sesion.iniciador_id else sesion.iniciador_id
    await gestor_conexiones.enviar_a(contraparte, {
        "tipo": "video_finalizada", "sesion": _resumen_video_json(sesion),
    })
    return sesion


@app.get("/api/video/sesiones/activa")
def endpoint_video_activa(
    sala_id: int = Query(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    sala = ControladorChat(db).obtener_sala(sala_id)
    if sala is None:
        raise HTTPException(status_code=404, detail="Sala no encontrada.")
    if usuario.id not in (sala.estudiante_id, sala.tutor_id):
        raise HTTPException(status_code=403, detail="No participas en esta sala.")
    sesion = ControladorVideollamada(db).activa_en_sala(sala_id)
    if sesion is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _resumen_video_json(sesion)


@app.get("/api/video/config", response_model=VideoConfigRespuesta)
def endpoint_video_config():
    """
    Devuelve los `iceServers` que el cliente debe pasar a su `RTCPeerConnection`.
    STUN público de Google por defecto; TURN opcional configurable vía env.
    """
    stun_csv = (os.getenv("BRIDGETS_STUN_URLS") or "stun:stun.l.google.com:19302").strip()
    stun_urls = [u.strip() for u in stun_csv.split(",") if u.strip()]
    ice_servers: list[dict] = []
    if stun_urls:
        ice_servers.append({"urls": stun_urls})
    turn_url = (os.getenv("BRIDGETS_TURN_URL") or "").strip()
    turn_user = (os.getenv("BRIDGETS_TURN_USER") or "").strip()
    turn_cred = (os.getenv("BRIDGETS_TURN_CRED") or "").strip()
    if turn_url and turn_user and turn_cred:
        ice_servers.append({
            "urls": [turn_url],
            "username": turn_user,
            "credential": turn_cred,
        })
    return VideoConfigRespuesta(ice_servers=ice_servers)


# ----------------------------------------------------------------------
# Llamadas grupales (modo aula)
# ----------------------------------------------------------------------

def _resumen_llamada_grupal(
    db: Session, llamada: LlamadaGrupalDB,
) -> LlamadaGrupalResumen:
    """Hidrata participantes activos + nombres para no exponer ids sueltos."""
    ctrl = ControladorLlamadaGrupal(db)
    activos = ctrl.participantes_activos(llamada.id)
    nombres = {
        u.id: u.nombre_completo for u in
        db.query(UsuarioDB)
        .filter(UsuarioDB.id.in_([p.usuario_id for p in activos] + [llamada.iniciador_id]))
        .all()
    }
    participantes = [
        ParticipanteLlamadaResumen(
            usuario_id=p.usuario_id,
            nombre=nombres.get(p.usuario_id, "?"),
            es_iniciador=(p.usuario_id == llamada.iniciador_id),
            unido_en=p.unido_en,
        )
        for p in activos
    ]
    return LlamadaGrupalResumen(
        id=llamada.id,
        clase_id=llamada.clase_id,
        iniciador_id=llamada.iniciador_id,
        iniciador_nombre=nombres.get(llamada.iniciador_id, "?"),
        titulo=llamada.titulo,
        estado=llamada.estado,
        creada_en=llamada.creada_en,
        finalizada_en=llamada.finalizada_en,
        participantes=participantes,
    )


def _cargar_llamada_grupal_o_404(
    db: Session, llamada_id: int,
) -> LlamadaGrupalDB:
    llamada = ControladorLlamadaGrupal(db).obtener(llamada_id)
    if llamada is None:
        raise HTTPException(status_code=404, detail="Llamada grupal no encontrada.")
    return llamada


@app.post(
    "/api/clases/{clase_id}/llamada-grupal",
    response_model=LlamadaGrupalResumen,
    status_code=status.HTTP_201_CREATED,
)
async def endpoint_iniciar_llamada_grupal(
    datos: LlamadaGrupalCrear,
    request: Request,
    clase_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    clase = ControladorClases(db).obtener_clase(clase_id)
    if clase is None:
        raise HTTPException(status_code=404, detail="Clase no encontrada.")
    if clase.tutor_id != tutor.id:
        raise HTTPException(status_code=403, detail="Solo el tutor de la clase puede iniciarla.")
    ctrl = ControladorLlamadaGrupal(db)
    try:
        llamada = ctrl.iniciar(clase, tutor, titulo=datos.titulo)
    except (ValueError, PermissionError) as exc:
        codigo = 400 if isinstance(exc, ValueError) else 403
        raise HTTPException(status_code=codigo, detail=str(exc))

    auditar(
        db, "llamada_grupal_iniciada", actor_id=tutor.id,
        recurso_tipo="llamada_grupal", recurso_id=llamada.id, ip=_ip_de(request),
        detalles=f"clase={clase_id}",
    )

    resumen = _resumen_llamada_grupal(db, llamada)
    payload = {"tipo": "grupal_iniciada", "llamada": resumen.model_dump(mode="json")}

    # Notificar a los inscritos por WS + persistencia para offline.
    inscritos = ControladorClases(db).listar_estudiantes_ids(clase_id)
    await gestor_conexiones.enviar_a_varios(inscritos, payload)
    await _notificar(
        db, inscritos,
        tipo="llamada_grupal_iniciada",
        titulo=f"📹 {clase.nombre}: llamada grupal en curso",
        cuerpo=f"{tutor.nombre_completo} inició la llamada. Únete cuando quieras.",
        enlace_tipo="llamada_grupal",
        enlace_id=llamada.id,
    )
    return resumen


@app.get(
    "/api/clases/{clase_id}/llamada-grupal/activa",
)
def endpoint_llamada_grupal_activa(
    clase_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    clase = ControladorClases(db).obtener_clase(clase_id)
    if clase is None:
        raise HTTPException(status_code=404, detail="Clase no encontrada.")
    ctrl = ControladorLlamadaGrupal(db)
    if not ctrl.es_miembro_clase(clase, usuario):
        raise HTTPException(status_code=403, detail="No eres miembro de esta clase.")
    llamada = ctrl.activa_en_clase(clase_id)
    if llamada is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _resumen_llamada_grupal(db, llamada)


@app.post(
    "/api/llamadas-grupales/{llamada_id}/unirse",
    response_model=LlamadaGrupalResumen,
)
async def endpoint_unirse_llamada_grupal(
    request: Request,
    llamada_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    llamada = _cargar_llamada_grupal_o_404(db, llamada_id)
    clase = ControladorClases(db).obtener_clase(llamada.clase_id)
    if clase is None:
        raise HTTPException(status_code=404, detail="Clase de la llamada no existe.")
    ctrl = ControladorLlamadaGrupal(db)
    try:
        ctrl.unirse(llamada, clase, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    auditar(
        db, "llamada_grupal_unido", actor_id=usuario.id,
        recurso_tipo="llamada_grupal", recurso_id=llamada.id, ip=_ip_de(request),
    )

    resumen = _resumen_llamada_grupal(db, llamada)

    # Aviso por WS al resto de participantes para que abran su PC contra el
    # nuevo peer (clave del mesh: el nuevo no sabe a quién contactar hasta
    # leer la lista que viene en la respuesta REST).
    otros = [p.usuario_id for p in ctrl.participantes_activos(llamada.id) if p.usuario_id != usuario.id]
    await gestor_conexiones.enviar_a_varios(otros, {
        "tipo": "grupal_unido",
        "llamada_id": llamada.id,
        "usuario_id": usuario.id,
        "nombre": usuario.nombre_completo,
    })
    return resumen


@app.post(
    "/api/llamadas-grupales/{llamada_id}/salir",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def endpoint_salir_llamada_grupal(
    request: Request,
    llamada_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    llamada = _cargar_llamada_grupal_o_404(db, llamada_id)
    ctrl = ControladorLlamadaGrupal(db)
    registro = ctrl.salir(llamada, usuario)
    if registro is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    auditar(
        db, "llamada_grupal_salido", actor_id=usuario.id,
        recurso_tipo="llamada_grupal", recurso_id=llamada.id, ip=_ip_de(request),
    )

    otros = [p.usuario_id for p in ctrl.participantes_activos(llamada.id) if p.usuario_id != usuario.id]
    await gestor_conexiones.enviar_a_varios(otros, {
        "tipo": "grupal_salido",
        "llamada_id": llamada.id,
        "usuario_id": usuario.id,
    })
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/llamadas-grupales/{llamada_id}/finalizar",
    response_model=LlamadaGrupalResumen,
)
async def endpoint_finalizar_llamada_grupal(
    request: Request,
    llamada_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    llamada = _cargar_llamada_grupal_o_404(db, llamada_id)
    ctrl = ControladorLlamadaGrupal(db)
    try:
        ctrl.finalizar(llamada, usuario)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    auditar(
        db, "llamada_grupal_finalizada", actor_id=usuario.id,
        recurso_tipo="llamada_grupal", recurso_id=llamada.id, ip=_ip_de(request),
    )

    resumen = _resumen_llamada_grupal(db, llamada)
    # Broadcast a todos los inscritos para que cierren su UI; los que estaban
    # dentro reciben además `participantes=[]` por estar ya cerrada.
    inscritos = ControladorClases(db).listar_estudiantes_ids(llamada.clase_id) + [llamada.iniciador_id]
    await gestor_conexiones.enviar_a_varios(list(set(inscritos)), {
        "tipo": "grupal_finalizada",
        "llamada": resumen.model_dump(mode="json"),
    })
    return resumen


# ----------------------------------------------------------------------
# WebSocket
# ----------------------------------------------------------------------

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket, token: str = Query(...)):
    db = SessionLocal()
    try:
        usuario = usuario_desde_token(token, db)
    finally:
        db.close()
    if usuario is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_id = usuario.id
    await gestor_conexiones.conectar(user_id, websocket)
    await websocket.send_json({"tipo": "bienvenida", "user_id": user_id})

    try:
        while True:
            data = await websocket.receive_json()
            tipo = data.get("tipo")
            if tipo == "ping":
                await websocket.send_json({"tipo": "pong"})
                continue
            if tipo == "enviar_mensaje":
                sala_id = int(data.get("sala_id", 0))
                contenido = (data.get("contenido") or "").strip()
                if not sala_id or not contenido:
                    await websocket.send_json({"tipo": "error", "mensaje": "Datos incompletos."})
                    continue
                # Procesamos en sesión propia
                db_local = SessionLocal()
                try:
                    chat = ControladorChat(db_local)
                    sala = chat.obtener_sala(sala_id)
                    if sala is None or not chat.participa(sala, usuario):
                        await websocket.send_json({"tipo": "error", "mensaje": "Sala inaccesible."})
                        continue
                    if sala.estado != "activa":
                        await websocket.send_json({"tipo": "error", "mensaje": "Sala no activa."})
                        continue
                    mensaje = chat.enviar_mensaje(sala, usuario, contenido)
                    payload = MensajeResumen.model_validate(mensaje).model_dump(mode="json")
                    await gestor_conexiones.enviar_a_varios(
                        [sala.estudiante_id, sala.tutor_id],
                        {"tipo": "mensaje_nuevo", "sala_id": sala.id, "mensaje": payload},
                    )
                finally:
                    db_local.close()
                continue

            # ---------- Señalización WebRTC + estado de videollamada ----------
            # Reglas comunes a todas las ramas siguientes:
            #   - Cargamos la SesionVideoDB en una Session local (igual que el chat).
            #   - Validamos que el usuario participa y que el estado permite
            #     señalización (`aceptada` o `activa`, salvo `video_colgar`).
            #   - El backend NO interpreta SDP/ICE: solo los reenvía a la
            #     contraparte añadiendo `tipo` y `sesion_id`.
            if tipo in ("webrtc_offer", "webrtc_answer", "webrtc_ice", "video_estado", "video_colgar"):
                sesion_id = int(data.get("sesion_id", 0) or 0)
                if not sesion_id:
                    await websocket.send_json({"tipo": "error", "mensaje": "sesion_id requerido."})
                    continue
                db_local = SessionLocal()
                try:
                    ctrl = ControladorVideollamada(db_local)
                    sesion = ctrl.obtener(sesion_id)
                    if sesion is None or not ctrl.participa(sesion, usuario):
                        await websocket.send_json({"tipo": "error", "mensaje": "Sesión inaccesible."})
                        continue
                    contraparte = ctrl.contraparte_id(sesion, usuario)

                    if tipo == "video_colgar":
                        # finalizar es idempotente; auditamos y notificamos solo
                        # si esta llamada realmente transicionó el estado.
                        estado_previo = sesion.estado
                        ctrl.finalizar(sesion, usuario, motivo="colgada")
                        if estado_previo != sesion.estado:
                            duracion = _duracion_segundos(sesion)
                            detalles = f"motivo={sesion.motivo_fin}, via=ws"
                            if duracion is not None:
                                detalles += f", duracion_s={duracion}"
                            auditar(
                                db_local, "video_finalizada", actor_id=usuario.id,
                                recurso_tipo="sesion_video", recurso_id=sesion.id,
                                detalles=detalles,
                            )
                            metricas.registrar_video(
                                "finalizada", motivo=sesion.motivo_fin, duracion_seg=duracion,
                            )
                            payload_fin = {
                                "tipo": "video_finalizada",
                                "sesion": _resumen_video_json(sesion),
                            }
                            # Notificamos a la contraparte para que su UI cierre la
                            # llamada. Al remitente no le hace falta el broadcast:
                            # ya disparó él el colgar y puede cerrar su UI al
                            # enviar el mensaje. Esto también evita un await
                            # `ws.send_json(self)` dentro del propio handler.
                            await gestor_conexiones.enviar_a(
                                ctrl.contraparte_id(sesion, usuario), payload_fin,
                            )
                        continue

                    if sesion.estado not in ESTADOS_SENALIZABLES:
                        await websocket.send_json({
                            "tipo": "error",
                            "mensaje": f"Sesión en estado '{sesion.estado}' no acepta señalización.",
                        })
                        continue

                    # El primer offer marca la sesión como `activa`.
                    if tipo == "webrtc_offer" and sesion.estado == "aceptada":
                        ctrl.marcar_activa(sesion)

                    payload = {"tipo": tipo, "sesion_id": sesion.id}
                    # Reenviamos los campos del cliente verbatim, sin interpretarlos.
                    for clave in ("sdp", "type", "candidate", "sdpMid", "sdpMLineIndex", "cam", "mic", "pantalla"):
                        if clave in data:
                            payload[clave] = data[clave]
                    await gestor_conexiones.enviar_a(contraparte, payload)
                finally:
                    db_local.close()
                continue

            # ---------- Señalización grupal (mesh: peer ↔ peer dentro de una llamada) ----------
            # Cada mensaje lleva `llamada_id` y `destino_id` (el peer al que va
            # dirigido). El backend valida participación y reenvía añadiendo
            # `origen_id` para que el receptor sepa de quién viene.
            if tipo in (
                "grupal_offer", "grupal_answer", "grupal_ice",
                "grupal_estado", "grupal_colgar",
            ):
                llamada_id = int(data.get("llamada_id", 0) or 0)
                destino_id = int(data.get("destino_id", 0) or 0)
                if not llamada_id:
                    await websocket.send_json({"tipo": "error", "mensaje": "llamada_id requerido."})
                    continue
                db_local = SessionLocal()
                try:
                    ctrl_grupal = ControladorLlamadaGrupal(db_local)
                    llamada = ctrl_grupal.obtener(llamada_id)
                    if llamada is None or llamada.estado != "activa":
                        await websocket.send_json({"tipo": "error", "mensaje": "Llamada inaccesible."})
                        continue
                    if not ctrl_grupal.esta_dentro(llamada_id, user_id):
                        await websocket.send_json({"tipo": "error", "mensaje": "No participas en esta llamada."})
                        continue

                    if tipo == "grupal_colgar":
                        # Equivalente a "salir": cierra el registro del usuario
                        # y avisa al resto. No finaliza la llamada (solo el
                        # iniciador puede vía REST `/finalizar`).
                        ctrl_grupal.salir(llamada, usuario)
                        otros = [
                            p.usuario_id for p in ctrl_grupal.participantes_activos(llamada_id)
                            if p.usuario_id != user_id
                        ]
                        await gestor_conexiones.enviar_a_varios(otros, {
                            "tipo": "grupal_salido",
                            "llamada_id": llamada_id,
                            "usuario_id": user_id,
                        })
                        continue

                    # Resto de tipos requieren destino válido y dentro.
                    if not destino_id:
                        await websocket.send_json({"tipo": "error", "mensaje": "destino_id requerido."})
                        continue
                    if not ctrl_grupal.esta_dentro(llamada_id, destino_id):
                        await websocket.send_json({"tipo": "error", "mensaje": "Destino no está en la llamada."})
                        continue

                    payload = {
                        "tipo": tipo, "llamada_id": llamada_id,
                        "origen_id": user_id, "destino_id": destino_id,
                    }
                    for clave in (
                        "sdp", "type", "candidate", "sdpMid", "sdpMLineIndex",
                        "cam", "mic", "pantalla",
                    ):
                        if clave in data:
                            payload[clave] = data[clave]
                    await gestor_conexiones.enviar_a(destino_id, payload)
                finally:
                    db_local.close()
                continue

            await websocket.send_json({"tipo": "error", "mensaje": f"Tipo desconocido: {tipo}"})
    except WebSocketDisconnect:
        pass
    finally:
        await gestor_conexiones.desconectar(user_id, websocket)
