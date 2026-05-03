from typing import List

from sqlalchemy.orm import Session, joinedload

from entidades import AnuncioClaseDB, ClaseDB, UsuarioDB
from validadores import AnuncioCrear, AnuncioResumen


class ControladorAnuncios:
    def __init__(self, db: Session):
        self.db = db

    def listar(self, clase_id: int) -> List[AnuncioResumen]:
        anuncios = (
            self.db.query(AnuncioClaseDB)
            .options(joinedload(AnuncioClaseDB.clase).joinedload(ClaseDB.tutor))
            .filter(AnuncioClaseDB.clase_id == clase_id)
            .order_by(AnuncioClaseDB.publicado_en.desc())
            .all()
        )
        autores = self._cargar_autores([a.autor_id for a in anuncios])
        return [
            AnuncioResumen(
                id=a.id,
                titulo=a.titulo,
                contenido=a.contenido,
                publicado_en=a.publicado_en,
                autor_nombre=autores.get(a.autor_id, "Tutor"),
            )
            for a in anuncios
        ]

    def crear(self, clase_id: int, autor: UsuarioDB, datos: AnuncioCrear) -> AnuncioResumen:
        anuncio = AnuncioClaseDB(
            clase_id=clase_id,
            autor_id=autor.id,
            titulo=datos.titulo.strip(),
            contenido=datos.contenido.strip(),
        )
        self.db.add(anuncio)
        self.db.commit()
        self.db.refresh(anuncio)
        return AnuncioResumen(
            id=anuncio.id,
            titulo=anuncio.titulo,
            contenido=anuncio.contenido,
            publicado_en=anuncio.publicado_en,
            autor_nombre=autor.nombre_completo,
        )

    def _cargar_autores(self, ids):
        if not ids:
            return {}
        filas = (
            self.db.query(UsuarioDB.id, UsuarioDB.nombre_completo)
            .filter(UsuarioDB.id.in_(set(ids)))
            .all()
        )
        return {fila.id: fila.nombre_completo for fila in filas}
