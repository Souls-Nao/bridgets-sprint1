from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from entidades import EntregaDB, TareaDB, UsuarioDB
from utilidades_db import cargar_nombres_usuarios
from validadores import (
    CalificarPeticion,
    EntregaCrear,
    EntregaResumen,
    TareaActualizar,
    TareaCrear,
    TareaResumen,
)


class ControladorTareas:
    """
    Capa de servicio para tareas y entregas.

    Reglas (la autorización vive en los endpoints):
      - Crear / editar / borrar tarea: tutor dueño de la clase.
      - Listar tareas: cualquier miembro de la clase.
      - Entregar / re-entregar: estudiante inscrito en la clase, antes del
        cierre y solo si su entrega aún no está calificada.
      - Calificar: tutor dueño de la clase.
    """

    def __init__(self, db: Session):
        self.db = db

    # -------------------------------------------------- Lookups
    def obtener(self, tarea_id: int) -> Optional[TareaDB]:
        return self.db.query(TareaDB).filter(TareaDB.id == tarea_id).first()

    def obtener_entrega(self, entrega_id: int) -> Optional[EntregaDB]:
        return self.db.query(EntregaDB).filter(EntregaDB.id == entrega_id).first()

    def obtener_entrega_propia(
        self, tarea: TareaDB, estudiante: UsuarioDB
    ) -> Optional[EntregaDB]:
        return (
            self.db.query(EntregaDB)
            .filter(
                EntregaDB.tarea_id == tarea.id,
                EntregaDB.estudiante_id == estudiante.id,
            )
            .first()
        )

    # -------------------------------------------------- CRUD tarea
    def crear(self, clase_id: int, tutor: UsuarioDB, datos: TareaCrear) -> TareaDB:
        tarea = TareaDB(
            clase_id=clase_id,
            autor_id=tutor.id,
            titulo=datos.titulo.strip(),
            descripcion=(datos.descripcion or "").strip() or None,
            fecha_limite=datos.fecha_limite,
            max_puntos=float(datos.max_puntos),
        )
        self.db.add(tarea)
        self.db.commit()
        self.db.refresh(tarea)
        return tarea

    def actualizar(self, tarea: TareaDB, datos: TareaActualizar) -> TareaDB:
        cambios = datos.model_dump(exclude_unset=True)
        if "titulo" in cambios and cambios["titulo"] is not None:
            tarea.titulo = cambios["titulo"].strip()
        if "descripcion" in cambios:
            valor = (cambios["descripcion"] or "").strip()
            tarea.descripcion = valor or None
        if "fecha_limite" in cambios:
            tarea.fecha_limite = cambios["fecha_limite"]  # puede ser None para quitar el límite
        if "max_puntos" in cambios and cambios["max_puntos"] is not None:
            tarea.max_puntos = float(cambios["max_puntos"])
        self.db.commit()
        self.db.refresh(tarea)
        return tarea

    def eliminar(self, tarea: TareaDB) -> None:
        self.db.delete(tarea)
        self.db.commit()

    # -------------------------------------------------- Listados
    def listar_para_usuario(
        self, clase_id: int, usuario: UsuarioDB
    ) -> List[TareaResumen]:
        """
        Tareas de la clase con la información contextual:
          - Si el usuario es el tutor dueño → total_entregas.
          - Si el usuario es estudiante → su propia entrega (si la hay).
        """
        tareas = (
            self.db.query(TareaDB)
            .filter(TareaDB.clase_id == clase_id)
            # Sin fecha_limite primero las que tienen plazo más cercano; luego por creación.
            .order_by(TareaDB.fecha_limite.asc().nullslast(), TareaDB.creada_en.desc())
            .all()
        )
        if not tareas:
            return []
        ids = [t.id for t in tareas]
        autores = cargar_nombres_usuarios(self.db, [t.autor_id for t in tareas])

        es_tutor = usuario.tipo_cuenta == "tutor"
        if es_tutor:
            conteos = dict(
                self.db.query(EntregaDB.tarea_id, func.count(EntregaDB.id))
                .filter(EntregaDB.tarea_id.in_(ids))
                .group_by(EntregaDB.tarea_id)
                .all()
            )
            entregas_propias_map: dict = {}
        else:
            conteos = {}
            entregas_propias = (
                self.db.query(EntregaDB)
                .filter(
                    EntregaDB.tarea_id.in_(ids),
                    EntregaDB.estudiante_id == usuario.id,
                )
                .all()
            )
            entregas_propias_map = {e.tarea_id: e for e in entregas_propias}

        return [
            TareaResumen(
                id=t.id,
                clase_id=t.clase_id,
                titulo=t.titulo,
                descripcion=t.descripcion,
                fecha_limite=t.fecha_limite,
                max_puntos=t.max_puntos,
                creada_en=t.creada_en,
                autor_nombre=autores.get(t.autor_id, "Tutor"),
                entrega_propia=(
                    self._resumen_entrega(entregas_propias_map[t.id], usuario.nombre_completo)
                    if t.id in entregas_propias_map else None
                ),
                total_entregas=conteos.get(t.id, 0) if es_tutor else None,
            )
            for t in tareas
        ]

    def listar_entregas(self, tarea: TareaDB) -> List[EntregaResumen]:
        entregas = (
            self.db.query(EntregaDB)
            .filter(EntregaDB.tarea_id == tarea.id)
            .order_by(EntregaDB.entregada_en.asc())
            .all()
        )
        nombres = cargar_nombres_usuarios(self.db, [e.estudiante_id for e in entregas])
        return [self._resumen_entrega(e, nombres.get(e.estudiante_id, "Estudiante")) for e in entregas]

    # -------------------------------------------------- Entregar
    def esta_cerrada(self, tarea: TareaDB) -> bool:
        if tarea.fecha_limite is None:
            return False
        limite = tarea.fecha_limite
        if limite.tzinfo is None:
            limite = limite.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > limite

    def entregar(
        self,
        tarea: TareaDB,
        estudiante: UsuarioDB,
        datos: EntregaCrear,
    ) -> Tuple[bool, object]:
        """
        Crea o actualiza la entrega del estudiante. Devuelve (True, EntregaResumen)
        o (False, mensaje_error).
        """
        if self.esta_cerrada(tarea):
            return False, "La tarea ya cerró su fecha límite."
        existente = self.obtener_entrega_propia(tarea, estudiante)
        if existente is not None and existente.calificacion is not None:
            return False, "La entrega ya fue calificada y no puede modificarse."
        contenido = datos.contenido.strip()
        if existente is None:
            entrega = EntregaDB(
                tarea_id=tarea.id,
                estudiante_id=estudiante.id,
                contenido=contenido,
            )
            self.db.add(entrega)
        else:
            existente.contenido = contenido
            entrega = existente
        self.db.commit()
        self.db.refresh(entrega)
        return True, self._resumen_entrega(entrega, estudiante.nombre_completo)

    # -------------------------------------------------- Calificar
    def calificar(
        self,
        entrega: EntregaDB,
        tutor: UsuarioDB,
        datos: CalificarPeticion,
        max_puntos: float,
    ) -> Tuple[bool, object]:
        if datos.calificacion > max_puntos:
            return False, f"La calificación excede el máximo ({max_puntos})."
        entrega.calificacion = float(datos.calificacion)
        entrega.feedback = (datos.feedback or "").strip() or None
        entrega.calificada_en = datetime.now(timezone.utc)
        entrega.calificada_por = tutor.id
        self.db.commit()
        self.db.refresh(entrega)
        # Resolvemos el nombre del estudiante para devolver el resumen completo.
        nombres = cargar_nombres_usuarios(self.db, [entrega.estudiante_id])
        return True, self._resumen_entrega(entrega, nombres.get(entrega.estudiante_id, "Estudiante"))

    # -------------------------------------------------- Helpers
    def _resumen_entrega(self, entrega: EntregaDB, estudiante_nombre: str) -> EntregaResumen:
        return EntregaResumen(
            id=entrega.id,
            tarea_id=entrega.tarea_id,
            estudiante_id=entrega.estudiante_id,
            estudiante_nombre=estudiante_nombre,
            contenido=entrega.contenido,
            entregada_en=entrega.entregada_en,
            actualizada_en=entrega.actualizada_en,
            calificacion=entrega.calificacion,
            feedback=entrega.feedback,
            calificada_en=entrega.calificada_en,
        )
