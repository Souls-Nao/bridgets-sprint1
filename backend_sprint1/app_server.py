from fastapi import Depends, FastAPI, File, Form, HTTPException, Path, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.orm import Session

from entidades import UsuarioDB
from motor_db import Base, engine, obtener_sesion
from servicio_anuncios import ControladorAnuncios
from servicio_archivos import ControladorArchivos
from servicio_auth import exigir_rol, obtener_usuario_actual
from servicio_clases import ControladorClases
from servicio_notas import ControladorNotas
from servicio_usuarios import ControladorUsuarios
from validadores import (
    AnuncioCrear,
    AnuncioResumen,
    ClaseCrear,
    ClaseDetalle,
    ClaseResumen,
    InscripcionPeticion,
    LoginPeticion,
    NotaActualizar,
    NotaCrear,
    NotaResumen,
    RegistroPeticion,
    SesionRespuesta,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Bridgets API - Sprint 2/3")

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

@app.post("/api/registro", status_code=status.HTTP_201_CREATED)
def endpoint_registro(peticion: RegistroPeticion, db: Session = Depends(obtener_sesion)):
    controlador = ControladorUsuarios(db)
    exito, mensaje = controlador.registrar_nuevo_usuario(peticion)
    if not exito:
        raise HTTPException(status_code=400, detail=mensaje)
    return {"mensaje": mensaje}


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
# Anuncios
# ----------------------------------------------------------------------

def _exigir_acceso_clase(db: Session, clase_id: int, usuario: UsuarioDB):
    ctrl = ControladorClases(db)
    clase = ctrl.obtener_clase(clase_id)
    if clase is None:
        raise HTTPException(status_code=404, detail="Clase no encontrada.")
    if not ctrl.usuario_puede_ver(clase, usuario):
        raise HTTPException(status_code=403, detail="No tienes acceso a esta clase.")
    return clase


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
# Notas (espacio personal del estudiante)
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
