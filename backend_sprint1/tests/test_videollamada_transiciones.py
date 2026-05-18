"""
Tests unitarios del `ControladorVideollamada`: cada transición valida sus
precondiciones (estado origen + rol del usuario) y deja la sesión en el
estado esperado. La sala/usuarios se construyen directamente vía SQLAlchemy
para aislar la lógica del controlador del flujo HTTP.
"""

from datetime import datetime, timedelta, timezone

import pytest

from entidades import ClaseDB, SalaChatDB, UsuarioDB
from servicio_videollamada import ControladorVideollamada


def _crear_trio(db) -> tuple[UsuarioDB, UsuarioDB, SalaChatDB]:
    """Crea tutor + estudiante + clase + sala activa, devuelve (tutor, estudiante, sala)."""
    tutor = UsuarioDB(
        nombre_completo="Tutor X", codigo="1",
        correo_electronico="t@x.com", usuario_login="tutorx",
        hash_acceso="x", tipo_cuenta="tutor",
    )
    estudiante = UsuarioDB(
        nombre_completo="Estu Y", codigo="2",
        correo_electronico="e@y.com", usuario_login="estuy",
        hash_acceso="x", tipo_cuenta="estudiante",
    )
    db.add_all([tutor, estudiante])
    db.flush()
    clase = ClaseDB(
        codigo_clase="ABC123", nombre="Mate", materia="Mate",
        tutor_id=tutor.id,
    )
    db.add(clase)
    db.flush()
    sala = SalaChatDB(
        clase_id=clase.id, estudiante_id=estudiante.id, tutor_id=tutor.id,
        iniciador_id=tutor.id, estado="activa",
    )
    db.add(sala)
    db.commit()
    db.refresh(sala)
    return tutor, estudiante, sala


def test_iniciar_falla_si_sala_no_activa(db):
    tutor, estudiante, sala = _crear_trio(db)
    sala.estado = "pendiente"
    db.commit()
    ctrl = ControladorVideollamada(db)
    with pytest.raises(ValueError):
        ctrl.iniciar(sala, tutor, "camara")


def test_iniciar_falla_si_usuario_no_participa(db):
    tutor, estudiante, sala = _crear_trio(db)
    intruso = UsuarioDB(
        nombre_completo="Z", codigo="3", correo_electronico="z@z.com",
        usuario_login="zz", hash_acceso="x", tipo_cuenta="estudiante",
    )
    db.add(intruso)
    db.commit()
    ctrl = ControladorVideollamada(db)
    with pytest.raises(PermissionError):
        ctrl.iniciar(sala, intruso, "camara")


def test_iniciar_segunda_falla_si_ya_hay_sesion_viva(db):
    tutor, estudiante, sala = _crear_trio(db)
    ctrl = ControladorVideollamada(db)
    primera = ctrl.iniciar(sala, tutor, "camara")
    assert primera.estado == "solicitada"
    assert primera.iniciador_id == tutor.id
    assert primera.receptor_id == estudiante.id
    with pytest.raises(ValueError):
        ctrl.iniciar(sala, estudiante, "camara")


def test_aceptar_solo_por_receptor(db):
    tutor, estudiante, sala = _crear_trio(db)
    ctrl = ControladorVideollamada(db)
    sesion = ctrl.iniciar(sala, tutor, "camara")
    # El iniciador no puede aceptar su propia llamada.
    with pytest.raises(PermissionError):
        ctrl.aceptar(sesion, tutor)
    sesion = ctrl.aceptar(sesion, estudiante)
    assert sesion.estado == "aceptada"
    assert sesion.aceptada_en is not None
    # Reaceptar ya no es estado válido.
    with pytest.raises(ValueError):
        ctrl.aceptar(sesion, estudiante)


def test_rechazar_solo_por_receptor(db):
    tutor, estudiante, sala = _crear_trio(db)
    ctrl = ControladorVideollamada(db)
    sesion = ctrl.iniciar(sala, tutor, "camara")
    with pytest.raises(PermissionError):
        ctrl.rechazar(sesion, tutor)
    sesion = ctrl.rechazar(sesion, estudiante)
    assert sesion.estado == "rechazada"
    assert sesion.finalizada_en is not None
    assert sesion.motivo_fin == "rechazada"


def test_finalizar_rechazado_a_terceros_y_es_idempotente(db):
    tutor, estudiante, sala = _crear_trio(db)
    intruso = UsuarioDB(
        nombre_completo="Z", codigo="3", correo_electronico="z@z.com",
        usuario_login="zz", hash_acceso="x", tipo_cuenta="estudiante",
    )
    db.add(intruso)
    db.commit()
    ctrl = ControladorVideollamada(db)
    sesion = ctrl.iniciar(sala, tutor, "camara")
    ctrl.aceptar(sesion, estudiante)
    with pytest.raises(PermissionError):
        ctrl.finalizar(sesion, intruso)
    sesion = ctrl.finalizar(sesion, tutor)
    assert sesion.estado == "finalizada"
    primera_finalizacion = sesion.finalizada_en
    # Idempotente: volver a finalizar no cambia nada ni lanza.
    sesion = ctrl.finalizar(sesion, estudiante)
    assert sesion.estado == "finalizada"
    assert sesion.finalizada_en == primera_finalizacion


def test_marcar_activa_solo_desde_aceptada(db):
    tutor, estudiante, sala = _crear_trio(db)
    ctrl = ControladorVideollamada(db)
    sesion = ctrl.iniciar(sala, tutor, "camara")
    # solicitada → marcar_activa no transiciona.
    ctrl.marcar_activa(sesion)
    assert sesion.estado == "solicitada"
    ctrl.aceptar(sesion, estudiante)
    ctrl.marcar_activa(sesion)
    assert sesion.estado == "activa"


def test_expirar_si_corresponde_marca_perdida_tras_timeout(db):
    tutor, estudiante, sala = _crear_trio(db)
    ctrl = ControladorVideollamada(db)
    sesion = ctrl.iniciar(sala, tutor, "camara")
    # Forzamos la creación a más de 45s atrás para disparar el timeout.
    sesion.creada_en = datetime.now(timezone.utc) - timedelta(seconds=60)
    db.commit()
    sesion = ctrl.expirar_si_corresponde(sesion, segundos=45)
    assert sesion.estado == "perdida"
    assert sesion.motivo_fin == "timeout_solicitud"
    assert sesion.finalizada_en is not None


def test_expirar_si_corresponde_no_toca_si_no_paso_tiempo(db):
    tutor, estudiante, sala = _crear_trio(db)
    ctrl = ControladorVideollamada(db)
    sesion = ctrl.iniciar(sala, tutor, "camara")
    sesion = ctrl.expirar_si_corresponde(sesion, segundos=45)
    assert sesion.estado == "solicitada"


def test_expirar_pendientes_recoge_solo_vencidas(db):
    tutor, estudiante, sala = _crear_trio(db)
    ctrl = ControladorVideollamada(db)
    vieja = ctrl.iniciar(sala, tutor, "camara")
    vieja.creada_en = datetime.now(timezone.utc) - timedelta(seconds=120)
    db.commit()
    expiradas = ctrl.expirar_pendientes(segundos=45)
    assert len(expiradas) == 1
    assert expiradas[0].id == vieja.id
    assert expiradas[0].estado == "perdida"


def test_activa_en_sala_devuelve_la_viva(db):
    tutor, estudiante, sala = _crear_trio(db)
    ctrl = ControladorVideollamada(db)
    assert ctrl.activa_en_sala(sala.id) is None
    sesion = ctrl.iniciar(sala, tutor, "camara")
    encontrada = ctrl.activa_en_sala(sala.id)
    assert encontrada is not None
    assert encontrada.id == sesion.id
    # Tras finalizar, ya no aparece como activa.
    ctrl.finalizar(sesion, tutor)
    assert ctrl.activa_en_sala(sala.id) is None
