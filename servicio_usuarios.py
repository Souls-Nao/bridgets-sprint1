from sqlalchemy.orm import Session
from entidades import UsuarioDB
from validadores import RegistroPeticion
from servicio_seguridad import GestorSeguridad

class ControladorUsuarios:
    def __init__(self, db: Session):
        self.db = db
        self.seguridad = GestorSeguridad()

    def registrar_nuevo_usuario(self, datos: RegistroPeticion):
        # Verificar si el correo ya existe
        usuario_existente = self.db.query(UsuarioDB).filter(UsuarioDB.correo_electronico == datos.correo_electronico).first()
        if usuario_existente:
            return False, "El correo electrónico ya está registrado."

        # Crear instancia del objeto UsuarioDB
        nuevo_usuario = UsuarioDB(
            nombre_completo=datos.nombre_completo,
            correo_electronico=datos.correo_electronico,
            hash_acceso=self.seguridad.encriptar_clave(datos.password),
            tipo_cuenta=datos.tipo_cuenta
        )
        self.db.add(nuevo_usuario)
        self.db.commit()
        return True, "Registro exitoso."

    def autenticar_usuario(self, correo: str, clave_plana: str):
        usuario = self.db.query(UsuarioDB).filter(UsuarioDB.correo_electronico == correo).first()
        if not usuario:
            return False, "Credenciales incorrectas."
        
        es_valido = self.seguridad.validar_clave(clave_plana, usuario.hash_acceso)
        if es_valido:
            return True, {"id": usuario.id, "nombre": usuario.nombre_completo, "rol": usuario.tipo_cuenta}
        return False, "Credenciales incorrectas."