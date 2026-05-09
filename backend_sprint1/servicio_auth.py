import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from entidades import TokenRevocadoDB, UsuarioDB
from motor_db import obtener_sesion

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY no está definida. Configúrala en .env (local) o en las "
        "variables de entorno del servicio (Render). "
        "Genera una segura con: openssl rand -hex 32"
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "12"))

esquema_bearer = HTTPBearer(auto_error=True)


def emitir_token(usuario: UsuarioDB) -> str:
    """
    Emite un JWT con claims estándar (sub, exp, iat) más `jti` (RFC 7519)
    para permitir revocación individual del token.
    """
    ahora = datetime.now(timezone.utc)
    expiracion = ahora + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(usuario.id),
        "usr": usuario.usuario_login,
        "rol": usuario.tipo_cuenta,
        "iat": ahora,
        "exp": expiracion,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decodificar(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except InvalidTokenError:
        return None


def _esta_revocado(db: Session, jti: Optional[str]) -> bool:
    if not jti:
        return False
    return db.query(TokenRevocadoDB.jti).filter(TokenRevocadoDB.jti == jti).first() is not None


def revocar_token(db: Session, payload: dict) -> None:
    """
    Marca el JWT como revocado guardando su jti hasta que expire.
    Idempotente: si el jti ya estaba registrado no falla.
    """
    jti = payload.get("jti")
    if not jti:
        return
    exp_ts = payload.get("exp")
    expira_en = datetime.fromtimestamp(exp_ts, tz=timezone.utc) if exp_ts else (
        datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    )
    if db.query(TokenRevocadoDB.jti).filter(TokenRevocadoDB.jti == jti).first() is not None:
        return
    db.add(TokenRevocadoDB(jti=jti, expira_en=expira_en))
    db.commit()


def _usuario_y_payload_desde_credenciales(
    credenciales: HTTPAuthorizationCredentials,
    db: Session,
) -> Tuple[UsuarioDB, dict]:
    excepcion = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = _decodificar(credenciales.credentials)
    if payload is None:
        raise excepcion
    id_usuario = payload.get("sub")
    if id_usuario is None or _esta_revocado(db, payload.get("jti")):
        raise excepcion
    usuario = db.query(UsuarioDB).filter(UsuarioDB.id == int(id_usuario)).first()
    if usuario is None:
        raise excepcion
    return usuario, payload


def obtener_usuario_actual(
    credenciales: HTTPAuthorizationCredentials = Depends(esquema_bearer),
    db: Session = Depends(obtener_sesion),
) -> UsuarioDB:
    usuario, _ = _usuario_y_payload_desde_credenciales(credenciales, db)
    return usuario


def obtener_usuario_y_payload(
    credenciales: HTTPAuthorizationCredentials = Depends(esquema_bearer),
    db: Session = Depends(obtener_sesion),
) -> Tuple[UsuarioDB, dict]:
    """Igual que obtener_usuario_actual pero también devuelve el payload (para revocar)."""
    return _usuario_y_payload_desde_credenciales(credenciales, db)


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
    payload = _decodificar(token)
    if payload is None:
        return None
    id_usuario = payload.get("sub")
    if id_usuario is None or _esta_revocado(db, payload.get("jti")):
        return None
    return db.query(UsuarioDB).filter(UsuarioDB.id == int(id_usuario)).first()


def limpiar_revocaciones_expiradas(db: Session) -> int:
    """
    Borra de la blacklist los tokens cuyo `exp` ya pasó (no podrían usarse aunque
    no estuvieran revocados). Devuelve cuántas filas eliminó. Útil como tarea de
    mantenimiento; no se llama automáticamente.
    """
    ahora = datetime.now(timezone.utc)
    eliminados = (
        db.query(TokenRevocadoDB)
        .filter(TokenRevocadoDB.expira_en < ahora)
        .delete(synchronize_session=False)
    )
    db.commit()
    return eliminados
