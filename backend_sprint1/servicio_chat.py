from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from entidades import ClaseDB, InscripcionDB, MensajeChatDB, SalaChatDB, UsuarioDB
from validadores import (
    ContraparteResumen,
    MensajeResumen,
    SalaResumen,
)


# Ventana en la que el autor puede editar o borrar su propio mensaje.
VENTANA_EDICION_MENSAJE = timedelta(minutes=10)


def _resumen_sala(
    sala: SalaChatDB,
    usuario_actual: UsuarioDB,
    contraparte: UsuarioDB,
    clase_nombre: str,
    mensajes_no_leidos: int = 0,
) -> SalaResumen:
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
        mensajes_no_leidos=mensajes_no_leidos,
    )


class ControladorChat:
    def __init__(self, db: Session):
        self.db = db

    # ---------- Inicio (tutor o estudiante) ----------
    def iniciar(
        self,
        clase: ClaseDB,
        usuario: UsuarioDB,
        estudiante_id: Optional[int] = None,
    ) -> Tuple[List[SalaChatDB], int, int]:
        """
        Devuelve (lista de salas afectadas, creadas, reutilizadas).

        Reglas:
          - Estudiante: crea/reutiliza la sala con el tutor de la clase. El
            parámetro `estudiante_id` se ignora (no le aplica).
          - Tutor sin `estudiante_id`: crea/reutiliza una sala con CADA
            estudiante inscrito (comportamiento histórico, sigue valiendo para
            "saludar a toda la clase").
          - Tutor con `estudiante_id`: solo crea/reutiliza la sala con ese
            estudiante en particular, siempre que esté inscrito. Si no lo
            está, lanza ValueError.
        """
        creadas = 0
        reutilizadas = 0
        salas: List[SalaChatDB] = []

        if usuario.tipo_cuenta == "tutor":
            if estudiante_id is not None:
                # Verificar inscripción para no abrir chats a usuarios externos.
                pertenece = (
                    self.db.query(InscripcionDB.id)
                    .filter(
                        InscripcionDB.clase_id == clase.id,
                        InscripcionDB.estudiante_id == estudiante_id,
                    )
                    .first()
                )
                if pertenece is None:
                    raise ValueError("Ese estudiante no está inscrito en la clase.")
                estudiantes_ids = [estudiante_id]
            else:
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

    # ---------- No leídos ----------
    def _ultimo_visto(self, sala: SalaChatDB, usuario: UsuarioDB) -> int:
        """Devuelve el último mensaje_id que el usuario marcó como leído en esa sala."""
        if usuario.id == sala.estudiante_id:
            return sala.ultimo_visto_estudiante or 0
        return sala.ultimo_visto_tutor or 0

    def no_leidos(self, sala: SalaChatDB, usuario: UsuarioDB) -> int:
        """Cuenta mensajes con id > ultimo_visto del usuario; 0 si la sala no está activa."""
        if sala.estado != "activa":
            return 0
        ultimo = self._ultimo_visto(sala, usuario)
        return (
            self.db.query(func.count(MensajeChatDB.id))
            .filter(MensajeChatDB.sala_id == sala.id, MensajeChatDB.id > ultimo)
            .scalar()
            or 0
        )

    def total_no_leidos_en_clase(self, usuario: UsuarioDB, clase_id: int) -> int:
        """Suma de no leídos del usuario en todas sus salas activas de la clase."""
        salas = (
            self.db.query(SalaChatDB)
            .filter(
                SalaChatDB.clase_id == clase_id,
                SalaChatDB.estado == "activa",
                (SalaChatDB.estudiante_id == usuario.id) | (SalaChatDB.tutor_id == usuario.id),
            )
            .all()
        )
        return sum(self.no_leidos(s, usuario) for s in salas)

    def marcar_leido(self, sala: SalaChatDB, usuario: UsuarioDB) -> int:
        """
        Avanza el cursor `ultimo_visto_*` del usuario al MAX(id) actual de la sala.
        Devuelve el id al que se movió (útil para que el cliente sepa el corte).
        """
        max_id = (
            self.db.query(func.max(MensajeChatDB.id))
            .filter(MensajeChatDB.sala_id == sala.id)
            .scalar()
        ) or 0
        if usuario.id == sala.estudiante_id:
            if max_id > (sala.ultimo_visto_estudiante or 0):
                sala.ultimo_visto_estudiante = max_id
                self.db.commit()
        elif usuario.id == sala.tutor_id:
            if max_id > (sala.ultimo_visto_tutor or 0):
                sala.ultimo_visto_tutor = max_id
                self.db.commit()
        return max_id

    # ---------- Listados por estado ----------
    def _listar_salas_para(
        self,
        usuario: UsuarioDB,
        estado: str,
        *,
        solo_recibidas: bool,
        orden,
    ) -> List[SalaResumen]:
        """
        Devuelve los SalaResumen del usuario con un estado dado.
        Si `solo_recibidas` es True, excluye salas que el propio usuario inició
        (útil para mostrar solicitudes pendientes que aún no he aceptado).
        """
        q = (
            self.db.query(SalaChatDB)
            .options(joinedload(SalaChatDB.clase))
            .filter(
                SalaChatDB.estado == estado,
                (SalaChatDB.estudiante_id == usuario.id) | (SalaChatDB.tutor_id == usuario.id),
            )
        )
        if solo_recibidas:
            q = q.filter(SalaChatDB.iniciador_id != usuario.id)
        salas = q.order_by(orden).all()

        resultado: List[SalaResumen] = []
        for sala in salas:
            contraparte = self.contraparte_de(sala, usuario)
            if contraparte is None:
                continue
            resultado.append(
                _resumen_sala(
                    sala, usuario, contraparte, sala.clase.nombre,
                    mensajes_no_leidos=self.no_leidos(sala, usuario),
                )
            )
        return resultado

    def solicitudes_pendientes_para(self, usuario: UsuarioDB) -> List[SalaResumen]:
        return self._listar_salas_para(
            usuario,
            estado="pendiente",
            solo_recibidas=True,
            orden=SalaChatDB.creada_en.desc(),
        )

    def salas_activas_para(self, usuario: UsuarioDB) -> List[SalaResumen]:
        return self._listar_salas_para(
            usuario,
            estado="activa",
            solo_recibidas=False,
            orden=SalaChatDB.actualizada_en.desc(),
        )

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

    def cerrar_por_clase_y_estudiante(
        self,
        clase_id: int,
        estudiante_id: int,
    ) -> List[SalaChatDB]:
        """
        Cierra todas las salas no cerradas de un estudiante en una clase.
        Útil cuando el estudiante se desinscribe o es expulsado: el dato del
        chat se conserva (historial), pero la sala deja de estar activa.
        Devuelve las salas afectadas para que el caller pueda notificar por WS.
        """
        salas = (
            self.db.query(SalaChatDB)
            .filter(
                SalaChatDB.clase_id == clase_id,
                SalaChatDB.estudiante_id == estudiante_id,
                SalaChatDB.estado != "cerrada",
            )
            .all()
        )
        if not salas:
            return []
        for sala in salas:
            sala.estado = "cerrada"
        self.db.commit()
        for sala in salas:
            self.db.refresh(sala)
        return salas

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

    def historial(
        self,
        sala: SalaChatDB,
        desde_id: int = 0,
        hasta_id: Optional[int] = None,
        limite: int = 50,
    ) -> List[MensajeResumen]:
        """
        Devuelve mensajes de la sala en orden ascendente por id.
        - `desde_id` (>0): "desde aquí en adelante" (usado para sync incremental).
        - `hasta_id` (>0): "los `limite` previos a este id" (paginación hacia atrás).
        Cuando se piden mensajes "hacia atrás", se ordenan desc internamente y
        se invierten antes de responder para entregar siempre orden ascendente.
        """
        if hasta_id is not None and hasta_id > 0:
            mensajes = (
                self.db.query(MensajeChatDB)
                .filter(MensajeChatDB.sala_id == sala.id, MensajeChatDB.id < hasta_id)
                .order_by(MensajeChatDB.id.desc())
                .limit(limite)
                .all()
            )
            mensajes.reverse()
        else:
            q = self.db.query(MensajeChatDB).filter(MensajeChatDB.sala_id == sala.id)
            if desde_id > 0:
                q = q.filter(MensajeChatDB.id > desde_id)
            # Para la carga inicial conviene los más recientes; tomamos los últimos
            # `limite` por id desc y los devolvemos en orden ascendente.
            mensajes = q.order_by(MensajeChatDB.id.desc()).limit(limite).all()
            mensajes.reverse()
        return [MensajeResumen.model_validate(m) for m in mensajes]

    def resumen_sala(self, sala: SalaChatDB, usuario: UsuarioDB) -> Optional[SalaResumen]:
        contraparte = self.contraparte_de(sala, usuario)
        if contraparte is None:
            return None
        nombre_clase = sala.clase.nombre if sala.clase else "Clase"
        return _resumen_sala(
            sala, usuario, contraparte, nombre_clase,
            mensajes_no_leidos=self.no_leidos(sala, usuario),
        )

    def limpiar_mensajes_de_salas_cerradas_antiguas(self, dias: int = 90) -> int:
        """
        Borra mensajes de salas cerradas hace más de `dias` días.
        El historial reciente se conserva; solo se purga lo que ya no aporta.
        Devuelve el número de mensajes eliminados.
        """
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        sub_salas = (
            self.db.query(SalaChatDB.id)
            .filter(
                SalaChatDB.estado == "cerrada",
                SalaChatDB.actualizada_en < corte,
            )
            .subquery()
        )
        eliminados = (
            self.db.query(MensajeChatDB)
            .filter(MensajeChatDB.sala_id.in_(self.db.query(sub_salas)))
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return eliminados

    # ---------- Editar / borrar mensaje propio ----------
    def obtener_mensaje(self, mensaje_id: int) -> Optional[MensajeChatDB]:
        return (
            self.db.query(MensajeChatDB)
            .filter(MensajeChatDB.id == mensaje_id)
            .first()
        )

    def es_editable(self, mensaje: MensajeChatDB) -> bool:
        """True si el mensaje sigue dentro de la ventana de edición/borrado."""
        enviado = mensaje.enviado_en
        if enviado.tzinfo is None:
            enviado = enviado.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - enviado) <= VENTANA_EDICION_MENSAJE

    def editar_mensaje(self, mensaje: MensajeChatDB, contenido: str) -> MensajeChatDB:
        mensaje.contenido = contenido.strip()
        mensaje.editado_en = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(mensaje)
        return mensaje

    def eliminar_mensaje(self, mensaje: MensajeChatDB) -> None:
        self.db.delete(mensaje)
        self.db.commit()
