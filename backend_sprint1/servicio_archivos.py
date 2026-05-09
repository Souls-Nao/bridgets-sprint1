import os
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from entidades import ArchivoBlobDB, ArchivoClaseDB, UsuarioDB
from utilidades_db import cargar_nombres_usuarios
from validadores import ArchivoActualizar, ArchivoResumen


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

    def listar(
        self,
        clase_id: int,
        categoria: Optional[str] = None,
    ) -> List[ArchivoResumen]:
        q = self.db.query(ArchivoClaseDB).filter(ArchivoClaseDB.clase_id == clase_id)
        if categoria:
            # Coincidencia exacta sin distinguir mayúsculas/espacios al filtrar.
            q = q.filter(ArchivoClaseDB.categoria == categoria)
        archivos = q.order_by(ArchivoClaseDB.subido_en.desc()).all()
        autores = cargar_nombres_usuarios(self.db, [a.autor_id for a in archivos])
        return [
            ArchivoResumen(
                id=a.id,
                nombre_original=a.nombre_original,
                mime=a.mime,
                tamano_bytes=a.tamano_bytes,
                subido_en=a.subido_en,
                autor_nombre=autores.get(a.autor_id, "Tutor"),
                categoria=a.categoria,
            )
            for a in archivos
        ]

    def listar_categorias(self, clase_id: int) -> List[str]:
        """Devuelve las categorías distintas usadas en la clase (orden alfabético, sin nulos)."""
        filas = (
            self.db.query(ArchivoClaseDB.categoria)
            .filter(
                ArchivoClaseDB.clase_id == clase_id,
                ArchivoClaseDB.categoria.isnot(None),
                ArchivoClaseDB.categoria != "",
            )
            .distinct()
            .order_by(ArchivoClaseDB.categoria.asc())
            .all()
        )
        return [f.categoria for f in filas]

    def guardar(
        self,
        clase_id: int,
        autor: UsuarioDB,
        nombre_original: str,
        mime: str,
        contenido: bytes,
        categoria: Optional[str] = None,
    ) -> Tuple[bool, object]:
        if mime not in MIMES_PERMITIDOS:
            return False, f"Tipo de archivo no permitido ({mime})."
        if not contenido:
            return False, "Archivo vacío."
        if len(contenido) > MAX_UPLOAD_BYTES:
            return False, f"El archivo excede el tamaño máximo ({MAX_UPLOAD_BYTES} bytes)."

        nombre_seguro = os.path.basename(nombre_original).strip() or "archivo"
        cat_norm = (categoria or "").strip()[:40] or None
        archivo = ArchivoClaseDB(
            clase_id=clase_id,
            autor_id=autor.id,
            nombre_original=nombre_seguro[:200],
            mime=mime,
            tamano_bytes=len(contenido),
            categoria=cat_norm,
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
            categoria=archivo.categoria,
        )

    def actualizar(
        self,
        archivo: ArchivoClaseDB,
        autor: UsuarioDB,
        datos: ArchivoActualizar,
    ) -> ArchivoResumen:
        cambios = datos.model_dump(exclude_unset=True)
        if "categoria" in cambios:
            valor = (cambios["categoria"] or "").strip()[:40]
            archivo.categoria = valor or None
        self.db.commit()
        self.db.refresh(archivo)
        return ArchivoResumen(
            id=archivo.id,
            nombre_original=archivo.nombre_original,
            mime=archivo.mime,
            tamano_bytes=archivo.tamano_bytes,
            subido_en=archivo.subido_en,
            autor_nombre=autor.nombre_completo,
            categoria=archivo.categoria,
        )

    def obtener(self, archivo_id: int) -> Optional[ArchivoClaseDB]:
        return self.db.query(ArchivoClaseDB).filter(ArchivoClaseDB.id == archivo_id).first()

    def obtener_para_descarga(self, archivo_id: int) -> Optional[ArchivoClaseDB]:
        return (
            self.db.query(ArchivoClaseDB)
            .options(joinedload(ArchivoClaseDB.blob))
            .filter(ArchivoClaseDB.id == archivo_id)
            .first()
        )

    def eliminar(self, archivo: ArchivoClaseDB) -> None:
        # cascade='all, delete-orphan' en la relación blob hace que el blob
        # asociado también se borre (SQLAlchemy ORM cascades).
        self.db.delete(archivo)
        self.db.commit()
