import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from entidades import UsuarioDB
from motor_db import obtener_sesion

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-no-usar-en-produccion")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "12"))

esquema_bearer = HTTPBearer(auto_error=True)


def emitir_token(usuario: UsuarioDB) -> str:
    expiracion = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(usuario.id),
        "usr": usuario.usuario_login,
        "rol": usuario.tipo_cuenta,
        "exp": expiracion,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def obtener_usuario_actual(
    credenciales: HTTPAuthorizationCredentials = Depends(esquema_bearer),
    db: Session = Depends(obtener_sesion),
) -> UsuarioDB:
    excepcion = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(credenciales.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        id_usuario = payload.get("sub")
        if id_usuario is None:
            raise excepcion
    except InvalidTokenError:
        raise excepcion

    usuario = db.query(UsuarioDB).filter(UsuarioDB.id == int(id_usuario)).first()
    if usuario is None:
        raise excepcion
    return usuario


def exigir_rol(rol_requerido: str):
    def validador(usuario: UsuarioDB = Depends(obtener_usuario_actual)) -> UsuarioDB:
        if usuario.tipo_cuenta != rol_requerido:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acción permitida solo para {rol_requerido}.",
            )
        return usuario

    return validador


def usuario_desde_token(token: str, db: Session) -> Optional[UsuarioDB]:
    """Decodifica un JWT crudo y devuelve el usuario. Útil para WebSockets."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        id_usuario = payload.get("sub")
        if id_usuario is None:
            return None
    except InvalidTokenError:
        return None
    return db.query(UsuarioDB).filter(UsuarioDB.id == int(id_usuario)).first()
