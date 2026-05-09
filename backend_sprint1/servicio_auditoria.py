from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from entidades import EventoAuditoriaDB


def auditar(
    db: Session,
    accion: str,
    actor_id: Optional[int] = None,
    recurso_tipo: Optional[str] = None,
    recurso_id: Optional[int] = None,
    ip: Optional[str] = None,
    detalles: Optional[str] = None,
) -> None:
    """
    Registra un evento en la bitácora. Tolerante: si la escritura falla, se
    loguea pero no se propaga (la auditoría no debe romper la operación
    principal).
    """
    try:
        evento = EventoAuditoriaDB(
            accion=accion,
            actor_id=actor_id,
            recurso_tipo=recurso_tipo,
            recurso_id=recurso_id,
            ip=ip,
            detalles=detalles,
        )
        db.add(evento)
        db.commit()
    except Exception as exc:
        # Best-effort: no propagamos para no tumbar la operación que se está auditando.
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[auditoria] no se pudo registrar evento '{accion}': {exc}")


def listar_eventos(
    db: Session,
    actor_id: Optional[int] = None,
    accion: Optional[str] = None,
    limite: int = 100,
) -> List[EventoAuditoriaDB]:
    q = db.query(EventoAuditoriaDB)
    if actor_id is not None:
        q = q.filter(EventoAuditoriaDB.actor_id == actor_id)
    if accion is not None:
        q = q.filter(EventoAuditoriaDB.accion == accion)
    return q.order_by(EventoAuditoriaDB.creada_en.desc()).limit(limite).all()


def limpiar_eventos_antiguos(db: Session, dias_a_retener: int = 365) -> int:
    """Borra eventos más viejos que el periodo de retención. Devuelve cuántos."""
    corte = datetime.now(timezone.utc) - timedelta(days=dias_a_retener)
    eliminados = (
        db.query(EventoAuditoriaDB)
        .filter(EventoAuditoriaDB.creada_en < corte)
        .delete(synchronize_session=False)
    )
    db.commit()
    return eliminados
