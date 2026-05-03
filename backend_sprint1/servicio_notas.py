from typing import List, Optional

from sqlalchemy.orm import Session

from entidades import NotaEstudianteDB, UsuarioDB
from validadores import NotaActualizar, NotaCrear, NotaResumen


class ControladorNotas:
    def __init__(self, db: Session):
        self.db = db

    def listar(self, clase_id: int, estudiante: UsuarioDB) -> List[NotaResumen]:
        notas = (
            self.db.query(NotaEstudianteDB)
            .filter(
                NotaEstudianteDB.clase_id == clase_id,
                NotaEstudianteDB.estudiante_id == estudiante.id,
            )
            .order_by(NotaEstudianteDB.actualizada_en.desc())
            .all()
        )
        return [NotaResumen.model_validate(n) for n in notas]

    def crear(self, clase_id: int, estudiante: UsuarioDB, datos: NotaCrear) -> NotaResumen:
        nota = NotaEstudianteDB(
            clase_id=clase_id,
            estudiante_id=estudiante.id,
            titulo=datos.titulo.strip(),
            contenido=datos.contenido or "",
        )
        self.db.add(nota)
        self.db.commit()
        self.db.refresh(nota)
        return NotaResumen.model_validate(nota)

    def obtener(self, nota_id: int, estudiante: UsuarioDB) -> Optional[NotaEstudianteDB]:
        return (
            self.db.query(NotaEstudianteDB)
            .filter(
                NotaEstudianteDB.id == nota_id,
                NotaEstudianteDB.estudiante_id == estudiante.id,
            )
            .first()
        )

    def actualizar(self, nota_id: int, estudiante: UsuarioDB, datos: NotaActualizar) -> Optional[NotaResumen]:
        nota = self.obtener(nota_id, estudiante)
        if nota is None:
            return None
        if datos.titulo is not None:
            nota.titulo = datos.titulo.strip()
        if datos.contenido is not None:
            nota.contenido = datos.contenido
        self.db.commit()
        self.db.refresh(nota)
        return NotaResumen.model_validate(nota)

    def eliminar(self, nota_id: int, estudiante: UsuarioDB) -> bool:
        nota = self.obtener(nota_id, estudiante)
        if nota is None:
            return False
        self.db.delete(nota)
        self.db.commit()
        return True
