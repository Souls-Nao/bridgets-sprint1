from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from motor_db import engine, Base, obtener_sesion
from validadores import RegistroPeticion, LoginPeticion
from servicio_usuarios import ControladorUsuarios

# Crea las tablas en Neon al iniciar
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Servidor Sprint 1 - POO")

@app.get("/")
def estado_servidor():
    return {"estado": "ok", "mensaje": "Servidor activo en Render conectado a Neon"}

@app.post("/api/registro", status_code=status.HTTP_201_CREATED)
def endpoint_registro(peticion: RegistroPeticion, db: Session = Depends(obtener_sesion)):
    controlador = ControladorUsuarios(db)
    exito, mensaje = controlador.registrar_nuevo_usuario(peticion)
    
    if not exito:
        raise HTTPException(status_code=400, detail=mensaje)
    return {"mensaje": mensaje}

@app.post("/api/login")
def endpoint_login(peticion: LoginPeticion, db: Session = Depends(obtener_sesion)):
    controlador = ControladorUsuarios(db)
    # Pasamos usuario_login en lugar de correo
    exito, resultado = controlador.autenticar_usuario(peticion.usuario_login, peticion.password)
    
    if not exito:
        raise HTTPException(status_code=401, detail=resultado)
    return {"mensaje": "Acceso concedido", "datos_sesion": resultado}
