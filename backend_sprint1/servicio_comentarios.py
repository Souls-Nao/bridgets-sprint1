from typing import List, Optional

from sqlalchemy.orm import Session

from entidades import ComentarioAnuncioDB, UsuarioDB
from utilidades_db import cargar_nombres_usuarios
from validadores import ComentarioCrear, ComentarioResumen


class ControladorComentarios:
    """
    CRUD mínimo para comentarios sobre un anuncio.

    Reglas de autorización (las aplica el endpoint, no este controlador):
      - Crear: cualquier usuario con acceso a la clase del anuncio.
      - Borrar: el autor del comentario o el tutor de la clase.
    """

    def __init__(self, db: Session):
        self.db = db

    def listar(self, anuncio_id: int) -> List[ComentarioResumen]:
        comentarios = (
            self.db.query(ComentarioAnuncioDB)
            .filter(ComentarioAnuncioDB.anuncio_id == anuncio_id)
            .order_by(ComentarioAnuncioDB.publicado_en.asc())
            .all()
        )
        autores = cargar_nombres_usuarios(self.db, [c.autor_id for c in comentarios])
        return [
            ComentarioResumen(
                id=c.id,
                anuncio_id=c.anuncio_id,
                autor_id=c.autor_id,
                autor_nombre=autores.get(c.autor_id, "Usuario"),
                contenido=c.contenido,
                publicado_en=c.publicado_en,
            )
            for c in comentarios
        ]

    def crear(
        self,
        anuncio_id: int,
        autor: UsuarioDB,
        datos: ComentarioCrear,
    ) -> ComentarioResumen:
        comentario = ComentarioAnuncioDB(
            anuncio_id=anuncio_id,
            autor_id=autor.id,
            contenido=datos.contenido.strip(),
        )
        self.db.add(comentario)
        self.db.commit()
        self.db.refresh(comentario)
        return ComentarioResumen(
            id=comentario.id,
            anuncio_id=comentario.anuncio_id,
            autor_id=comentario.autor_id,
            autor_nombre=autor.nombre_completo,
            contenido=comentario.contenido,
            publicado_en=comentario.publicado_en,
        )

    def obtener(self, comentario_id: int) -> Optional[ComentarioAnuncioDB]:
        return (
            self.db.query(ComentarioAnuncioDB)
            .filter(ComentarioAnuncioDB.id == comentario_id)
            .first()
        )

    def eliminar(self, comentario: ComentarioAnuncioDB) -> None:
        self.db.delete(comentario)
        self.db.commit()
