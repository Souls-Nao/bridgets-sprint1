import re

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
from fastapi.responses import Response
from sqlalchemy.orm import Session

from entidades import UsuarioDB
from migraciones import aplicar_migraciones_idempotentes
from motor_db import Base, SessionLocal, engine, obtener_sesion
from servicio_anuncios import ControladorAnuncios
from servicio_archivos import ControladorArchivos
from servicio_auth import exigir_rol, obtener_usuario_actual, usuario_desde_token
from servicio_chat import ControladorChat
from servicio_clases import ControladorClases
from servicio_notas import ControladorNotas
from servicio_rate_limit import limitador_disponibilidad
from servicio_usuarios import ControladorUsuarios
from servicio_ws import gestor_conexiones
from validadores import (
    AnuncioCrear,
    AnuncioResumen,
    BirdgtIniciarRespuesta,
    ClaseCrear,
    ClaseDetalle,
    ClaseResumen,
    DisponibilidadRespuesta,
    InscripcionPeticion,
    LoginPeticion,
    MensajeCrear,
    MensajeResumen,
    NotaActualizar,
    NotaCrear,
    NotaResumen,
    RegistroPeticion,
    SalaResumen,
    SesionRespuesta,
)

Base.metadata.create_all(bind=engine)
aplicar_migraciones_idempotentes(engine)

app = FastAPI(title="Bridgets API - Sprint 4 (Birdgt + privadas)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# Salud
# ----------------------------------------------------------------------

@app.get("/")
def estado_servidor():
    return {"estado": "ok", "mensaje": "Servidor activo en Render conectado a Neon"}


# ----------------------------------------------------------------------
# Cuentas
# ----------------------------------------------------------------------

_PATRON_USUARIO = re.compile(r"^[A-Za-z0-9_.-]{3,20}$")
_PATRON_CORREO = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")


@app.post("/api/registro", status_code=status.HTTP_201_CREATED)
def endpoint_registro(peticion: RegistroPeticion, db: Session = Depends(obtener_sesion)):
    controlador = ControladorUsuarios(db)
    exito, mensaje = controlador.registrar_nuevo_usuario(peticion)
    if not exito:
        raise HTTPException(status_code=400, detail=mensaje)
    return {"mensaje": mensaje}


@app.get("/api/registro/disponibilidad", response_model=DisponibilidadRespuesta)
def endpoint_disponibilidad(
    request: Request,
    usuario: str | None = Query(None, max_length=40),
    correo: str | None = Query(None, max_length=120),
    db: Session = Depends(obtener_sesion),
):
    ip = (request.client.host if request.client else "anon") or "anon"
    if not limitador_disponibilidad.permitir(ip):
        raise HTTPException(status_code=429, detail="Demasiadas consultas. Intenta de nuevo en un momento.")

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
def endpoint_login(peticion: LoginPeticion, db: Session = Depends(obtener_sesion)):
    controlador = ControladorUsuarios(db)
    exito, resultado = controlador.autenticar_usuario(peticion.usuario_login, peticion.password)
    if not exito:
        raise HTTPException(status_code=401, detail=resultado)
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
def endpoint_crear_anuncio(
    datos: AnuncioCrear,
    clase_id: int = Path(..., ge=1),
    tutor: UsuarioDB = Depends(exigir_rol("tutor")),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_acceso_clase(db, clase_id, tutor)
    if clase.tutor_id != tutor.id:
        raise HTTPException(status_code=403, detail="Solo el tutor de la clase puede publicar anuncios.")
    return ControladorAnuncios(db).crear(clase_id, tutor, datos)


# ----------------------------------------------------------------------
# Archivos
# ----------------------------------------------------------------------

@app.get("/api/clases/{clase_id}/archivos")
def endpoint_listar_archivos(
    clase_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    _exigir_acceso_clase(db, clase_id, usuario)
    return ControladorArchivos(db).listar(clase_id)


@app.post("/api/clases/{clase_id}/archivos", status_code=status.HTTP_201_CREATED)
async def endpoint_subir_archivo(
    clase_id: int = Path(..., ge=1),
    archivo: UploadFile = File(...),
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
    )
    if not exito:
        raise HTTPException(status_code=400, detail=resultado)
    return resultado


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
# Birdgt — chat
# ----------------------------------------------------------------------

@app.post("/api/clases/{clase_id}/birdgt", response_model=BirdgtIniciarRespuesta, status_code=status.HTTP_201_CREATED)
async def endpoint_iniciar_birdgt(
    clase_id: int = Path(..., ge=1),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    clase = _exigir_acceso_clase(db, clase_id, usuario)
    chat = ControladorChat(db)
    salas, creadas, reutilizadas = chat.iniciar(clase, usuario)

    resumenes = []
    for sala in salas:
        resumen = chat.resumen_sala(sala, usuario)
        if resumen is None:
            continue
        resumenes.append(resumen)

        # Notificar por WS al destinatario (no al iniciador)
        destinatario_id = sala.estudiante_id if usuario.id != sala.estudiante_id else sala.tutor_id
        otro = chat.contraparte_de(sala, usuario)
        if otro is None:
            continue
        otro_resumen = chat.resumen_sala(sala, otro)
        if otro_resumen is None:
            continue
        if sala.estado == "pendiente":
            await gestor_conexiones.enviar_a(destinatario_id, {
                "tipo": "solicitud_nueva",
                "sala": otro_resumen.model_dump(mode="json"),
            })

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

    resumen_iniciador = chat.resumen_sala(
        sala,
        db.query(UsuarioDB).filter(UsuarioDB.id == sala.iniciador_id).first(),
    )
    if resumen_iniciador is not None:
        await gestor_conexiones.enviar_a(sala.iniciador_id, {
            "tipo": "sala_aceptada",
            "sala": resumen_iniciador.model_dump(mode="json"),
        })
    resumen_propio = chat.resumen_sala(sala, usuario)
    return resumen_propio


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
    desde: int = Query(0, ge=0),
    usuario: UsuarioDB = Depends(obtener_usuario_actual),
    db: Session = Depends(obtener_sesion),
):
    chat, sala = _cargar_sala_o_404(db, sala_id, usuario)
    return chat.historial(sala, desde_id=desde)


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
            await websocket.send_json({"tipo": "error", "mensaje": f"Tipo desconocido: {tipo}"})
    except WebSocketDisconnect:
        pass
    finally:
        await gestor_conexiones.desconectar(user_id, websocket)
