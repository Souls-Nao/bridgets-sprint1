from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from entidades import NotificacionDB, UsuarioDB
from validadores import NotificacionResumen


class ControladorNotificaciones:
    """
    CRUD básico para el centro de notificaciones in-app.

    El "push" por WebSocket lo hace el endpoint que usa este controlador,
    no el controlador mismo (separación de responsabilidades — la persistencia
    no depende del estado de la conexión WS del usuario).
    """

    def __init__(self, db: Session):
        self.db = db

    # -------------------------------------------------- Crear
    def crear(
        self,
        destinatario_id: int,
        tipo: str,
        titulo: str,
        cuerpo: Optional[str] = None,
        enlace_tipo: Optional[str] = None,
        enlace_id: Optional[int] = None,
    ) -> NotificacionDB:
        notif = NotificacionDB(
            destinatario_id=destinatario_id,
            tipo=tipo,
            titulo=titulo,
            cuerpo=cuerpo,
            enlace_tipo=enlace_tipo,
            enlace_id=enlace_id,
        )
        self.db.add(notif)
        self.db.commit()
        self.db.refresh(notif)
        return notif

    def crear_para_varios(
        self,
        destinatarios: List[int],
        tipo: str,
        titulo: str,
        cuerpo: Optional[str] = None,
        enlace_tipo: Optional[str] = None,
        enlace_id: Optional[int] = None,
    ) -> List[NotificacionDB]:
        creadas: List[NotificacionDB] = []
        for did in destinatarios:
            creadas.append(NotificacionDB(
                destinatario_id=did,
                tipo=tipo,
                titulo=titulo,
                cuerpo=cuerpo,
                enlace_tipo=enlace_tipo,
                enlace_id=enlace_id,
            ))
        if not creadas:
            return []
        self.db.add_all(creadas)
        self.db.commit()
        for n in creadas:
            self.db.refresh(n)
        return creadas

    # -------------------------------------------------- Listar / contar
    def listar_para(
        self,
        usuario: UsuarioDB,
        solo_no_leidas: bool = False,
        limite: int = 50,
    ) -> List[NotificacionResumen]:
        q = self.db.query(NotificacionDB).filter(NotificacionDB.destinatario_id == usuario.id)
        if solo_no_leidas:
            q = q.filter(NotificacionDB.leida_en.is_(None))
        notifs = q.order_by(NotificacionDB.creada_en.desc()).limit(limite).all()
        return [NotificacionResumen.model_validate(n) for n in notifs]

    def total_no_leidas(self, usuario: UsuarioDB) -> int:
        return (
            self.db.query(func.count(NotificacionDB.id))
            .filter(
                NotificacionDB.destinatario_id == usuario.id,
                NotificacionDB.leida_en.is_(None),
            )
            .scalar()
            or 0
        )

    # -------------------------------------------------- Lectura
    def obtener(self, notif_id: int) -> Optional[NotificacionDB]:
        return self.db.query(NotificacionDB).filter(NotificacionDB.id == notif_id).first()

    def marcar_leida(self, notif: NotificacionDB) -> None:
        if notif.leida_en is None:
            notif.leida_en = datetime.now(timezone.utc)
            self.db.commit()

    def marcar_todas_leidas(self, usuario: UsuarioDB) -> int:
        ahora = datetime.now(timezone.utc)
        afectadas = (
            self.db.query(NotificacionDB)
            .filter(
                NotificacionDB.destinatario_id == usuario.id,
                NotificacionDB.leida_en.is_(None),
            )
            .update({NotificacionDB.leida_en: ahora}, synchronize_session=False)
        )
        self.db.commit()
        return afectadas

    # -------------------------------------------------- Mantenimiento
    def limpiar_leidas_antiguas(self, dias: int = 30) -> int:
        """Elimina notificaciones leídas hace más de `dias` días."""
        from datetime import timedelta
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        eliminadas = (
            self.db.query(NotificacionDB)
            .filter(
                NotificacionDB.leida_en.isnot(None),
                NotificacionDB.leida_en < corte,
            )
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return eliminadas
