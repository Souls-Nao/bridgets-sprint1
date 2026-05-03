from typing import List, Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from entidades import ClaseDB, InscripcionDB, MensajeChatDB, SalaChatDB, UsuarioDB
from validadores import (
    ContraparteResumen,
    MensajeResumen,
    SalaResumen,
)


def _resumen_sala(sala: SalaChatDB, usuario_actual: UsuarioDB, contraparte: UsuarioDB, clase_nombre: str) -> SalaResumen:
    return SalaResumen(
        id=sala.id,
        clase_id=sala.clase_id,
        clase_nombre=clase_nombre,
        estado=sala.estado,
        iniciador_id=sala.iniciador_id,
        contraparte=ContraparteResumen(
            id=contraparte.id,
            nombre=contraparte.nombre_completo,
            rol=contraparte.tipo_cuenta,
        ),
        creada_en=sala.creada_en,
        actualizada_en=sala.actualizada_en,
    )


class ControladorChat:
    def __init__(self, db: Session):
        self.db = db

    # ---------- Inicio (tutor o estudiante) ----------
    def iniciar(self, clase: ClaseDB, usuario: UsuarioDB) -> Tuple[List[SalaChatDB], int, int]:
        """
        Devuelve (lista de salas afectadas, creadas, reutilizadas).
        Tutor: crea/reutiliza una sala con cada estudiante inscrito.
        Estudiante: crea/reutiliza la sala con el tutor de la clase.
        """
        creadas = 0
        reutilizadas = 0
        salas: List[SalaChatDB] = []

        if usuario.tipo_cuenta == "tutor":
            estudiantes_ids = [
                fila.estudiante_id
                for fila in self.db.query(InscripcionDB.estudiante_id)
                .filter(InscripcionDB.clase_id == clase.id)
                .all()
            ]
            for est_id in estudiantes_ids:
                sala, fue_creada = self._upsert_sala(clase, est_id, clase.tutor_id, iniciador_id=usuario.id)
                salas.append(sala)
                creadas += int(fue_creada)
                reutilizadas += int(not fue_creada)
        else:
            sala, fue_creada = self._upsert_sala(clase, usuario.id, clase.tutor_id, iniciador_id=usuario.id)
            salas.append(sala)
            creadas += int(fue_creada)
            reutilizadas += int(not fue_creada)

        self.db.commit()
        for sala in salas:
            self.db.refresh(sala)
        return salas, creadas, reutilizadas

    def _upsert_sala(
        self,
        clase: ClaseDB,
        estudiante_id: int,
        tutor_id: int,
        iniciador_id: int,
    ) -> Tuple[SalaChatDB, bool]:
        existente = (
            self.db.query(SalaChatDB)
            .filter(SalaChatDB.clase_id == clase.id, SalaChatDB.estudiante_id == estudiante_id)
            .first()
        )
        if existente:
            if existente.estado == "cerrada":
                existente.estado = "pendiente"
                existente.iniciador_id = iniciador_id
                self.db.flush()
            return existente, False
        nueva = SalaChatDB(
            clase_id=clase.id,
            estudiante_id=estudiante_id,
            tutor_id=tutor_id,
            iniciador_id=iniciador_id,
            estado="pendiente",
        )
        self.db.add(nueva)
        self.db.flush()
        return nueva, True

    # ---------- Lookups ----------
    def obtener_sala(self, sala_id: int) -> Optional[SalaChatDB]:
        return (
            self.db.query(SalaChatDB)
            .options(joinedload(SalaChatDB.clase).joinedload(ClaseDB.tutor))
            .filter(SalaChatDB.id == sala_id)
            .first()
        )

    def participa(self, sala: SalaChatDB, usuario: UsuarioDB) -> bool:
        return usuario.id in (sala.estudiante_id, sala.tutor_id)

    def contraparte_de(self, sala: SalaChatDB, usuario: UsuarioDB) -> Optional[UsuarioDB]:
        otro_id = sala.tutor_id if usuario.id == sala.estudiante_id else sala.estudiante_id
        return self.db.query(UsuarioDB).filter(UsuarioDB.id == otro_id).first()

    # ---------- Solicitudes pendientes ----------
    def solicitudes_pendientes_para(self, usuario: UsuarioDB) -> List[SalaResumen]:
        # Una solicitud pendiente para mí es aquella en la que NO soy el iniciador.
        salas = (
            self.db.query(SalaChatDB)
            .options(joinedload(SalaChatDB.clase))
            .filter(
                SalaChatDB.estado == "pendiente",
                SalaChatDB.iniciador_id != usuario.id,
                ((SalaChatDB.estudiante_id == usuario.id) | (SalaChatDB.tutor_id == usuario.id)),
            )
            .order_by(SalaChatDB.creada_en.desc())
            .all()
        )
        resultado = []
        for sala in salas:
            contraparte = self.contraparte_de(sala, usuario)
            if contraparte is None:
                continue
            resultado.append(_resumen_sala(sala, usuario, contraparte, sala.clase.nombre))
        return resultado

    def salas_activas_para(self, usuario: UsuarioDB) -> List[SalaResumen]:
        salas = (
            self.db.query(SalaChatDB)
            .options(joinedload(SalaChatDB.clase))
            .filter(
                SalaChatDB.estado == "activa",
                ((SalaChatDB.estudiante_id == usuario.id) | (SalaChatDB.tutor_id == usuario.id)),
            )
            .order_by(SalaChatDB.actualizada_en.desc())
            .all()
        )
        resultado = []
        for sala in salas:
            contraparte = self.contraparte_de(sala, usuario)
            if contraparte is None:
                continue
            resultado.append(_resumen_sala(sala, usuario, contraparte, sala.clase.nombre))
        return resultado

    # ---------- Acciones de estado ----------
    def aceptar(self, sala: SalaChatDB) -> SalaChatDB:
        sala.estado = "activa"
        self.db.commit()
        self.db.refresh(sala)
        return sala

    def cerrar(self, sala: SalaChatDB) -> SalaChatDB:
        sala.estado = "cerrada"
        self.db.commit()
        self.db.refresh(sala)
        return sala

    # ---------- Mensajes ----------
    def enviar_mensaje(self, sala: SalaChatDB, autor: UsuarioDB, contenido: str) -> MensajeChatDB:
        mensaje = MensajeChatDB(
            sala_id=sala.id,
            autor_id=autor.id,
            contenido=contenido.strip(),
        )
        self.db.add(mensaje)
        self.db.flush()
        sala.actualizada_en = mensaje.enviado_en
        self.db.commit()
        self.db.refresh(mensaje)
        return mensaje

    def historial(self, sala: SalaChatDB, desde_id: int = 0, limite: int = 200) -> List[MensajeResumen]:
        q = self.db.query(MensajeChatDB).filter(MensajeChatDB.sala_id == sala.id)
        if desde_id > 0:
            q = q.filter(MensajeChatDB.id > desde_id)
        mensajes = q.order_by(MensajeChatDB.id.asc()).limit(limite).all()
        return [MensajeResumen.model_validate(m) for m in mensajes]

    def resumen_sala(self, sala: SalaChatDB, usuario: UsuarioDB) -> Optional[SalaResumen]:
        contraparte = self.contraparte_de(sala, usuario)
        if contraparte is None:
            return None
        nombre_clase = sala.clase.nombre if sala.clase else "Clase"
        return _resumen_sala(sala, usuario, contraparte, nombre_clase)
