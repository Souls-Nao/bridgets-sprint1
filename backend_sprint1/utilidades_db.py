from typing import Dict, Iterable

from sqlalchemy.orm import Session

from entidades import UsuarioDB


def cargar_nombres_usuarios(db: Session, ids: Iterable[int]) -> Dict[int, str]:
    """
    Devuelve un mapa {usuario_id: nombre_completo} para los ids indicados.
    Útil para resolver nombres de autores en listados (anuncios, archivos, etc.)
    sin disparar consultas N+1.
    """
    ids_unicos = {i for i in ids if i is not None}
    if not ids_unicos:
        return {}
    filas = (
        db.query(UsuarioDB.id, UsuarioDB.nombre_completo)
        .filter(UsuarioDB.id.in_(ids_unicos))
        .all()
    )
    return {fila.id: fila.nombre_completo for fila in filas}
