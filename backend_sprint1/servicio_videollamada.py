"""
Controlador del ciclo de vida de una videollamada 1-a-1 anidada en una sala
de chat. El backend solo gestiona el estado (solicitada → aceptada → activa →
finalizada/rechazada/perdida); los medios viajan P2P entre clientes vía
WebRTC. La señalización SDP/ICE se reenvía por el WebSocket de chat existente
(ver `app_server.py`, bucle de `/ws/chat`).

Diseñado en espejo de `ControladorChat`: el caller (endpoint REST o handler
WS) le inyecta `db: Session` y consume métodos de transición que ya hacen
`commit()` + `refresh()`.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from entidades import SalaChatDB, SesionVideoDB, UsuarioDB


# Estados terminales: la sesión no puede salir de aquí.
_ESTADOS_FINALES = ("rechazada", "finalizada", "perdida")
# Estados en los que se considera que la sesión "ocupa" la sala (impiden iniciar otra).
_ESTADOS_OCUPA_SALA = ("solicitada", "aceptada", "activa")
# Estados en los que se puede reenviar señalización WebRTC (offer/answer/ice).
ESTADOS_SENALIZABLES = ("aceptada", "activa")


class ControladorVideollamada:
    def __init__(self, db: Session):
        self.db = db

    # ---------- Lookups ----------
    def obtener(self, sesion_id: int) -> Optional[SesionVideoDB]:
        return (
            self.db.query(SesionVideoDB)
            .filter(SesionVideoDB.id == sesion_id)
            .first()
        )

    def activa_en_sala(self, sala_id: int) -> Optional[SesionVideoDB]:
        """Devuelve la sesión "viva" para una sala, si existe."""
        return (
            self.db.query(SesionVideoDB)
            .filter(
                SesionVideoDB.sala_id == sala_id,
                SesionVideoDB.estado.in_(_ESTADOS_OCUPA_SALA),
            )
            .order_by(SesionVideoDB.creada_en.desc())
            .first()
        )

    def participa(self, sesion: SesionVideoDB, usuario: UsuarioDB) -> bool:
        return usuario.id in (sesion.iniciador_id, sesion.receptor_id)

    def contraparte_id(self, sesion: SesionVideoDB, usuario: UsuarioDB) -> int:
        if usuario.id == sesion.iniciador_id:
            return sesion.receptor_id
        return sesion.iniciador_id

    # ---------- Transiciones ----------
    def iniciar(self, sala: SalaChatDB, usuario: UsuarioDB, modo: str) -> SesionVideoDB:
        if sala.estado != "activa":
            raise ValueError("La sala no está activa")
        if self.activa_en_sala(sala.id) is not None:
            raise ValueError("Ya hay una videollamada en curso para esta sala")
        if usuario.id not in (sala.estudiante_id, sala.tutor_id):
            raise PermissionError("No participas en esta sala")

        receptor_id = sala.tutor_id if usuario.id == sala.estudiante_id else sala.estudiante_id
        sesion = SesionVideoDB(
            sala_id=sala.id,
            iniciador_id=usuario.id,
            receptor_id=receptor_id,
            estado="solicitada",
            modo=modo,
        )
        self.db.add(sesion)
        self.db.commit()
        self.db.refresh(sesion)
        return sesion

    def aceptar(self, sesion: SesionVideoDB, usuario: UsuarioDB) -> SesionVideoDB:
        if usuario.id != sesion.receptor_id:
            raise PermissionError("Solo el receptor puede aceptar la llamada")
        if sesion.estado != "solicitada":
            raise ValueError(f"No se puede aceptar una sesión en estado '{sesion.estado}'")
        sesion.estado = "aceptada"
        sesion.aceptada_en = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(sesion)
        return sesion

    def rechazar(
        self,
        sesion: SesionVideoDB,
        usuario: UsuarioDB,
        motivo: str = "rechazada",
    ) -> SesionVideoDB:
        if usuario.id != sesion.receptor_id:
            raise PermissionError("Solo el receptor puede rechazar la llamada")
        if sesion.estado != "solicitada":
            raise ValueError(f"No se puede rechazar una sesión en estado '{sesion.estado}'")
        sesion.estado = "rechazada"
        sesion.finalizada_en = datetime.now(timezone.utc)
        sesion.motivo_fin = motivo
        self.db.commit()
        self.db.refresh(sesion)
        return sesion

    def marcar_activa(self, sesion: SesionVideoDB) -> SesionVideoDB:
        """Lo llama el backend al ver el primer `webrtc_offer` válido sobre el WS."""
        if sesion.estado == "aceptada":
            sesion.estado = "activa"
            self.db.commit()
            self.db.refresh(sesion)
        return sesion

    def finalizar(
        self,
        sesion: SesionVideoDB,
        usuario: UsuarioDB,
        motivo: str = "colgada",
    ) -> SesionVideoDB:
        if not self.participa(sesion, usuario):
            raise PermissionError("No participas en esta sesión")
        if sesion.estado in _ESTADOS_FINALES:
            return sesion  # idempotente
        sesion.estado = "finalizada"
        sesion.finalizada_en = datetime.now(timezone.utc)
        sesion.motivo_fin = motivo
        self.db.commit()
        self.db.refresh(sesion)
        return sesion

    def expirar_si_corresponde(
        self,
        sesion: SesionVideoDB,
        segundos: int = 45,
    ) -> SesionVideoDB:
        """
        Si la sesión lleva más de `segundos` en estado `solicitada` sin que el
        receptor haya respondido, la marca como `perdida`. Idempotente: si no
        cumple la condición, no hace nada.
        """
        if sesion.estado != "solicitada":
            return sesion
        creada = sesion.creada_en
        if creada.tzinfo is None:
            creada = creada.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - creada < timedelta(seconds=segundos):
            return sesion
        sesion.estado = "perdida"
        sesion.finalizada_en = datetime.now(timezone.utc)
        sesion.motivo_fin = "timeout_solicitud"
        self.db.commit()
        self.db.refresh(sesion)
        return sesion

    # ---------- Mantenimiento ----------
    def expirar_pendientes(self, segundos: int = 45) -> List[SesionVideoDB]:
        """
        Recorre todas las sesiones `solicitada` que ya cumplieron el timeout y
        las marca como `perdida`. Pensado para el job de limpieza periódica.
        """
        corte = datetime.now(timezone.utc) - timedelta(seconds=segundos)
        candidatas = (
            self.db.query(SesionVideoDB)
            .filter(
                SesionVideoDB.estado == "solicitada",
                SesionVideoDB.creada_en < corte,
            )
            .all()
        )
        if not candidatas:
            return []
        ahora = datetime.now(timezone.utc)
        for s in candidatas:
            s.estado = "perdida"
            s.finalizada_en = ahora
            s.motivo_fin = "timeout_solicitud"
        self.db.commit()
        return candidatas
