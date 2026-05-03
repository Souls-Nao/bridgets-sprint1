import os
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from entidades import ArchivoBlobDB, ArchivoClaseDB, UsuarioDB
from validadores import ArchivoResumen


MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

MIMES_PERMITIDOS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
}


class ControladorArchivos:
    def __init__(self, db: Session):
        self.db = db

    def listar(self, clase_id: int) -> List[ArchivoResumen]:
        archivos = (
            self.db.query(ArchivoClaseDB)
            .filter(ArchivoClaseDB.clase_id == clase_id)
            .order_by(ArchivoClaseDB.subido_en.desc())
            .all()
        )
        autores = self._cargar_autores([a.autor_id for a in archivos])
        return [
            ArchivoResumen(
                id=a.id,
                nombre_original=a.nombre_original,
                mime=a.mime,
                tamano_bytes=a.tamano_bytes,
                subido_en=a.subido_en,
                autor_nombre=autores.get(a.autor_id, "Tutor"),
            )
            for a in archivos
        ]

    def guardar(
        self,
        clase_id: int,
        autor: UsuarioDB,
        nombre_original: str,
        mime: str,
        contenido: bytes,
    ) -> Tuple[bool, object]:
        if mime not in MIMES_PERMITIDOS:
            return False, f"Tipo de archivo no permitido ({mime})."
        if not contenido:
            return False, "Archivo vacío."
        if len(contenido) > MAX_UPLOAD_BYTES:
            return False, f"El archivo excede el tamaño máximo ({MAX_UPLOAD_BYTES} bytes)."

        nombre_seguro = os.path.basename(nombre_original).strip() or "archivo"
        archivo = ArchivoClaseDB(
            clase_id=clase_id,
            autor_id=autor.id,
            nombre_original=nombre_seguro[:200],
            mime=mime,
            tamano_bytes=len(contenido),
        )
        self.db.add(archivo)
        self.db.flush()  # asignar archivo.id
        self.db.add(ArchivoBlobDB(archivo_id=archivo.id, contenido=contenido))
        self.db.commit()
        self.db.refresh(archivo)
        return True, ArchivoResumen(
            id=archivo.id,
            nombre_original=archivo.nombre_original,
            mime=archivo.mime,
            tamano_bytes=archivo.tamano_bytes,
            subido_en=archivo.subido_en,
            autor_nombre=autor.nombre_completo,
        )

    def obtener_para_descarga(self, archivo_id: int) -> Optional[ArchivoClaseDB]:
        return (
            self.db.query(ArchivoClaseDB)
            .options(joinedload(ArchivoClaseDB.blob))
            .filter(ArchivoClaseDB.id == archivo_id)
            .first()
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
