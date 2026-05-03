import secrets
import string
from typing import List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from entidades import ClaseDB, InscripcionDB, UsuarioDB
from validadores import ClaseCrear, ClaseDetalle, ClaseResumen, TutorResumen


_ALFABETO_CODIGO = string.ascii_uppercase + string.digits


def _generar_codigo_clase(db: Session, longitud: int = 6) -> str:
    for _ in range(20):
        candidato = "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(longitud))
        existe = db.query(ClaseDB).filter(ClaseDB.codigo_clase == candidato).first()
        if not existe:
            return candidato
    raise RuntimeError("No fue posible generar un código único de clase.")


def _construir_resumen(clase: ClaseDB, inscritos: int, inscrito: bool) -> ClaseResumen:
    return ClaseResumen(
        id=clase.id,
        codigo_clase=clase.codigo_clase,
        nombre=clase.nombre,
        materia=clase.materia,
        tutor=TutorResumen(id=clase.tutor.id, nombre=clase.tutor.nombre_completo, usuario=clase.tutor.usuario_login),
        inscritos=inscritos,
        inscrito=inscrito,
    )


class ControladorClases:
    def __init__(self, db: Session):
        self.db = db

    # ---------- Creación ----------
    def crear_clase(self, datos: ClaseCrear, tutor: UsuarioDB) -> ClaseDetalle:
        nueva = ClaseDB(
            codigo_clase=_generar_codigo_clase(self.db),
            nombre=datos.nombre.strip(),
            materia=datos.materia.strip(),
            descripcion=(datos.descripcion or "").strip() or None,
            tutor_id=tutor.id,
        )
        self.db.add(nueva)
        self.db.commit()
        self.db.refresh(nueva)
        return self.detalle_para_usuario(nueva.id, tutor)

    # ---------- Listado del usuario ----------
    def listar_mis_clases(self, usuario: UsuarioDB) -> List[ClaseResumen]:
        if usuario.tipo_cuenta == "tutor":
            clases = (
                self.db.query(ClaseDB)
                .options(joinedload(ClaseDB.tutor))
                .filter(ClaseDB.tutor_id == usuario.id)
                .order_by(ClaseDB.creada_en.desc())
                .all()
            )
        else:
            clases = (
                self.db.query(ClaseDB)
                .options(joinedload(ClaseDB.tutor))
                .join(InscripcionDB, InscripcionDB.clase_id == ClaseDB.id)
                .filter(InscripcionDB.estudiante_id == usuario.id)
                .order_by(InscripcionDB.inscrito_en.desc())
                .all()
            )

        if not clases:
            return []

        ids = [c.id for c in clases]
        conteos = dict(
            self.db.query(InscripcionDB.clase_id, func.count(InscripcionDB.id))
            .filter(InscripcionDB.clase_id.in_(ids))
            .group_by(InscripcionDB.clase_id)
            .all()
        )
        inscripciones_propias = set()
        if usuario.tipo_cuenta == "estudiante":
            inscripciones_propias = {
                fila.clase_id
                for fila in self.db.query(InscripcionDB.clase_id)
                .filter(InscripcionDB.estudiante_id == usuario.id, InscripcionDB.clase_id.in_(ids))
                .all()
            }
        return [
            _construir_resumen(c, conteos.get(c.id, 0), c.id in inscripciones_propias or usuario.tipo_cuenta == "tutor")
            for c in clases
        ]

    # ---------- Búsqueda pública ----------
    def buscar(self, consulta: str, usuario: UsuarioDB, limite: int = 30) -> List[ClaseResumen]:
        consulta_norm = (consulta or "").strip()
        q = self.db.query(ClaseDB).options(joinedload(ClaseDB.tutor))
        if consulta_norm:
            patron = f"%{consulta_norm}%"
            q = q.filter(
                or_(
                    ClaseDB.nombre.ilike(patron),
                    ClaseDB.materia.ilike(patron),
                    ClaseDB.codigo_clase.ilike(patron),
                )
            )
        clases = q.order_by(ClaseDB.creada_en.desc()).limit(limite).all()
        if not clases:
            return []

        ids = [c.id for c in clases]
        conteos = dict(
            self.db.query(InscripcionDB.clase_id, func.count(InscripcionDB.id))
            .filter(InscripcionDB.clase_id.in_(ids))
            .group_by(InscripcionDB.clase_id)
            .all()
        )
        inscripciones_propias = set()
        if usuario.tipo_cuenta == "estudiante":
            inscripciones_propias = {
                fila.clase_id
                for fila in self.db.query(InscripcionDB.clase_id)
                .filter(InscripcionDB.estudiante_id == usuario.id, InscripcionDB.clase_id.in_(ids))
                .all()
            }
        return [
            _construir_resumen(
                c,
                conteos.get(c.id, 0),
                c.id in inscripciones_propias or (usuario.tipo_cuenta == "tutor" and c.tutor_id == usuario.id),
            )
            for c in clases
        ]

    # ---------- Detalle ----------
    def obtener_clase(self, clase_id: int) -> Optional[ClaseDB]:
        return (
            self.db.query(ClaseDB)
            .options(joinedload(ClaseDB.tutor))
            .filter(ClaseDB.id == clase_id)
            .first()
        )

    def detalle_para_usuario(self, clase_id: int, usuario: UsuarioDB) -> Optional[ClaseDetalle]:
        clase = self.obtener_clase(clase_id)
        if clase is None:
            return None
        inscritos = (
            self.db.query(func.count(InscripcionDB.id))
            .filter(InscripcionDB.clase_id == clase.id)
            .scalar()
            or 0
        )
        inscrito = False
        if usuario.tipo_cuenta == "estudiante":
            inscrito = (
                self.db.query(InscripcionDB)
                .filter(
                    InscripcionDB.clase_id == clase.id,
                    InscripcionDB.estudiante_id == usuario.id,
                )
                .first()
                is not None
            )
        es_propietario = clase.tutor_id == usuario.id and usuario.tipo_cuenta == "tutor"
        return ClaseDetalle(
            id=clase.id,
            codigo_clase=clase.codigo_clase,
            nombre=clase.nombre,
            materia=clase.materia,
            tutor=TutorResumen(id=clase.tutor.id, nombre=clase.tutor.nombre_completo, usuario=clase.tutor.usuario_login),
            inscritos=inscritos,
            inscrito=inscrito or es_propietario,
            descripcion=clase.descripcion,
            creada_en=clase.creada_en,
            es_propietario=es_propietario,
        )

    # ---------- Inscripción ----------
    def inscribir_estudiante(
        self,
        estudiante: UsuarioDB,
        clase_id: Optional[int] = None,
        codigo_clase: Optional[str] = None,
    ):
        if estudiante.tipo_cuenta != "estudiante":
            return False, "Solo los estudiantes pueden inscribirse en clases."

        clase: Optional[ClaseDB] = None
        if clase_id is not None:
            clase = self.db.query(ClaseDB).filter(ClaseDB.id == clase_id).first()
        elif codigo_clase:
            clase = self.db.query(ClaseDB).filter(ClaseDB.codigo_clase == codigo_clase.strip().upper()).first()

        if clase is None:
            return False, "Clase no encontrada."

        ya_inscrito = (
            self.db.query(InscripcionDB)
            .filter(
                InscripcionDB.clase_id == clase.id,
                InscripcionDB.estudiante_id == estudiante.id,
            )
            .first()
        )
        if ya_inscrito:
            return False, "Ya estás inscrito en esta clase."

        self.db.add(InscripcionDB(clase_id=clase.id, estudiante_id=estudiante.id))
        self.db.commit()
        return True, clase.id

    # ---------- Autorización ----------
    def usuario_puede_ver(self, clase: ClaseDB, usuario: UsuarioDB) -> bool:
        if clase.tutor_id == usuario.id:
            return True
        if usuario.tipo_cuenta != "estudiante":
            return False
        return (
            self.db.query(InscripcionDB)
            .filter(
                InscripcionDB.clase_id == clase.id,
                InscripcionDB.estudiante_id == usuario.id,
            )
            .first()
            is not None
        )
