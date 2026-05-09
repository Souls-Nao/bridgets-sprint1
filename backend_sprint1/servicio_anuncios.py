from typing import List, Optional

from sqlalchemy.orm import Session

from entidades import AnuncioClaseDB, UsuarioDB
from utilidades_db import cargar_nombres_usuarios
from validadores import AnuncioActualizar, AnuncioCrear, AnuncioResumen


class ControladorAnuncios:
    def __init__(self, db: Session):
        self.db = db

    def obtener(self, anuncio_id: int) -> Optional[AnuncioClaseDB]:
        return self.db.query(AnuncioClaseDB).filter(AnuncioClaseDB.id == anuncio_id).first()

    def listar(self, clase_id: int) -> List[AnuncioResumen]:
        # Anclados arriba, luego por fecha descendente. Importante para el "pin".
        anuncios = (
            self.db.query(AnuncioClaseDB)
            .filter(AnuncioClaseDB.clase_id == clase_id)
            .order_by(AnuncioClaseDB.anclado.desc(), AnuncioClaseDB.publicado_en.desc())
            .all()
        )
        autores = cargar_nombres_usuarios(self.db, [a.autor_id for a in anuncios])
        return [
            AnuncioResumen(
                id=a.id,
                titulo=a.titulo,
                contenido=a.contenido,
                publicado_en=a.publicado_en,
                autor_nombre=autores.get(a.autor_id, "Tutor"),
                anclado=bool(a.anclado),
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
        return self._resumen(anuncio, autor.nombre_completo)

    def actualizar(
        self,
        anuncio: AnuncioClaseDB,
        autor: UsuarioDB,
        datos: AnuncioActualizar,
    ) -> AnuncioResumen:
        # PATCH parcial: solo se aplican los campos que vinieron en el request
        # (FastAPI tutorial — Body Updates).
        cambios = datos.model_dump(exclude_unset=True)
        if "titulo" in cambios and cambios["titulo"] is not None:
            anuncio.titulo = cambios["titulo"].strip()
        if "contenido" in cambios and cambios["contenido"] is not None:
            anuncio.contenido = cambios["contenido"].strip()
        if "anclado" in cambios and cambios["anclado"] is not None:
            anuncio.anclado = bool(cambios["anclado"])
        self.db.commit()
        self.db.refresh(anuncio)
        return self._resumen(anuncio, autor.nombre_completo)

    def eliminar(self, anuncio: AnuncioClaseDB) -> None:
        self.db.delete(anuncio)
        self.db.commit()

    def _resumen(self, anuncio: AnuncioClaseDB, autor_nombre: str) -> AnuncioResumen:
        return AnuncioResumen(
            id=anuncio.id,
            titulo=anuncio.titulo,
            contenido=anuncio.contenido,
            publicado_en=anuncio.publicado_en,
            autor_nombre=autor_nombre,
            anclado=bool(anuncio.anclado),
        )
