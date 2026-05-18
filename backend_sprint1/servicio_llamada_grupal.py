"""
Controlador del ciclo de vida de las llamadas grupales 'modo aula' (un tutor
abre, los estudiantes se unen). Espejo de `servicio_videollamada` pero con
participación múltiple.

Reglas:
  - Solo el tutor de la clase puede iniciar/finalizar.
  - Solo miembros de la clase (tutor + inscritos) pueden unirse.
  - Una clase puede tener como máximo una llamada `activa` simultánea.
  - Si el iniciador finaliza, todos los participantes se consideran salidos
    en el mismo timestamp (cascade lógico).

El backend no maneja medios — solo persistencia y enrutamiento de señalización
(la mesh la negocia cada par de peers por sí mismo vía `/ws/chat`).
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from entidades import (
    ClaseDB,
    InscripcionDB,
    LlamadaGrupalDB,
    ParticipanteLlamadaDB,
    UsuarioDB,
)


class ControladorLlamadaGrupal:
    def __init__(self, db: Session):
        self.db = db

    # ---------- Lookups ----------
    def obtener(self, llamada_id: int) -> Optional[LlamadaGrupalDB]:
        return (
            self.db.query(LlamadaGrupalDB)
            .filter(LlamadaGrupalDB.id == llamada_id)
            .first()
        )

    def activa_en_clase(self, clase_id: int) -> Optional[LlamadaGrupalDB]:
        return (
            self.db.query(LlamadaGrupalDB)
            .filter(
                LlamadaGrupalDB.clase_id == clase_id,
                LlamadaGrupalDB.estado == "activa",
            )
            .order_by(LlamadaGrupalDB.creada_en.desc())
            .first()
        )

    def participantes_activos(self, llamada_id: int) -> List[ParticipanteLlamadaDB]:
        return (
            self.db.query(ParticipanteLlamadaDB)
            .filter(
                ParticipanteLlamadaDB.llamada_id == llamada_id,
                ParticipanteLlamadaDB.salio_en.is_(None),
            )
            .order_by(ParticipanteLlamadaDB.unido_en)
            .all()
        )

    def ids_activos(self, llamada_id: int) -> List[int]:
        return [p.usuario_id for p in self.participantes_activos(llamada_id)]

    def es_miembro_clase(self, clase: ClaseDB, usuario: UsuarioDB) -> bool:
        if usuario.id == clase.tutor_id:
            return True
        return (
            self.db.query(InscripcionDB.id)
            .filter(
                InscripcionDB.clase_id == clase.id,
                InscripcionDB.estudiante_id == usuario.id,
            )
            .first()
            is not None
        )

    def esta_dentro(self, llamada_id: int, usuario_id: int) -> bool:
        return any(p.usuario_id == usuario_id for p in self.participantes_activos(llamada_id))

    # ---------- Transiciones ----------
    def iniciar(
        self,
        clase: ClaseDB,
        usuario: UsuarioDB,
        titulo: Optional[str] = None,
    ) -> LlamadaGrupalDB:
        if usuario.id != clase.tutor_id:
            raise PermissionError("Solo el tutor de la clase puede iniciar una llamada grupal.")
        if self.activa_en_clase(clase.id) is not None:
            raise ValueError("Ya hay una llamada grupal activa en esta clase.")
        llamada = LlamadaGrupalDB(
            clase_id=clase.id,
            iniciador_id=usuario.id,
            propietario_id=usuario.id,  # quien inicia es el primer propietario
            estado="activa",
            titulo=(titulo or None),
        )
        self.db.add(llamada)
        self.db.flush()  # necesitamos el id para añadir al participante
        # El iniciador se considera dentro desde el primer momento.
        self.db.add(ParticipanteLlamadaDB(llamada_id=llamada.id, usuario_id=usuario.id))
        self.db.commit()
        self.db.refresh(llamada)
        return llamada

    def unirse(
        self,
        llamada: LlamadaGrupalDB,
        clase: ClaseDB,
        usuario: UsuarioDB,
    ) -> ParticipanteLlamadaDB:
        if llamada.estado != "activa":
            raise ValueError("La llamada ya no está activa.")
        if not self.es_miembro_clase(clase, usuario):
            raise PermissionError("No eres miembro de la clase.")
        # Si ya está dentro, devolvemos su registro actual (idempotente).
        for p in self.participantes_activos(llamada.id):
            if p.usuario_id == usuario.id:
                return p
        participante = ParticipanteLlamadaDB(
            llamada_id=llamada.id, usuario_id=usuario.id,
        )
        self.db.add(participante)
        self.db.commit()
        self.db.refresh(participante)
        return participante

    def salir(
        self,
        llamada: LlamadaGrupalDB,
        usuario: UsuarioDB,
    ) -> tuple[Optional[ParticipanteLlamadaDB], Optional[int]]:
        """
        Cierra el registro del participante. Si el usuario era el propietario y
        quedan otros activos, traspasa la propiedad al más antiguo todavía
        dentro y devuelve su id en el segundo elemento del tuple. Si no queda
        nadie, finaliza la llamada automáticamente (devuelve nuevo_propietario=None).
        Para llamados que NO eran propietario, simplemente sale.
        """
        ahora = datetime.now(timezone.utc)
        registro = (
            self.db.query(ParticipanteLlamadaDB)
            .filter(
                ParticipanteLlamadaDB.llamada_id == llamada.id,
                ParticipanteLlamadaDB.usuario_id == usuario.id,
                ParticipanteLlamadaDB.salio_en.is_(None),
            )
            .order_by(ParticipanteLlamadaDB.unido_en.desc())
            .first()
        )
        if registro is None:
            return None, None
        registro.salio_en = ahora

        nuevo_propietario_id: Optional[int] = None
        if llamada.propietario_id == usuario.id and llamada.estado == "activa":
            # Buscar el siguiente más antiguo todavía dentro (excluyendo al
            # que acaba de salir).
            sucesor = (
                self.db.query(ParticipanteLlamadaDB)
                .filter(
                    ParticipanteLlamadaDB.llamada_id == llamada.id,
                    ParticipanteLlamadaDB.salio_en.is_(None),
                    ParticipanteLlamadaDB.usuario_id != usuario.id,
                )
                .order_by(ParticipanteLlamadaDB.unido_en.asc())
                .first()
            )
            if sucesor is not None:
                llamada.propietario_id = sucesor.usuario_id
                nuevo_propietario_id = sucesor.usuario_id
            else:
                # Nadie más → finalizar automáticamente para no dejar la
                # llamada "huérfana" bloqueando otras en la misma clase.
                llamada.estado = "finalizada"
                llamada.finalizada_en = ahora
                llamada.propietario_id = None
        self.db.commit()
        self.db.refresh(registro)
        self.db.refresh(llamada)
        return registro, nuevo_propietario_id

    def finalizar(
        self,
        llamada: LlamadaGrupalDB,
        usuario: UsuarioDB,
    ) -> LlamadaGrupalDB:
        # Permiso: el propietario actual (que puede haber cambiado desde el
        # inicio si el iniciador salió cediendo el control). Mantenemos al
        # iniciador como permitido por compatibilidad, aunque tras salir
        # normalmente ya no aparezca como participante.
        if usuario.id not in (llamada.propietario_id, llamada.iniciador_id):
            raise PermissionError("Solo el propietario actual de la llamada puede finalizarla.")
        if llamada.estado != "activa":
            return llamada  # idempotente
        ahora = datetime.now(timezone.utc)
        llamada.estado = "finalizada"
        llamada.finalizada_en = ahora
        # Cierre lógico de todos los participantes que seguían dentro.
        self.db.query(ParticipanteLlamadaDB).filter(
            ParticipanteLlamadaDB.llamada_id == llamada.id,
            ParticipanteLlamadaDB.salio_en.is_(None),
        ).update({ParticipanteLlamadaDB.salio_en: ahora}, synchronize_session=False)
        self.db.commit()
        self.db.refresh(llamada)
        return llamada
