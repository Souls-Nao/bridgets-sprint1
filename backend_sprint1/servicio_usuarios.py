from sqlalchemy.orm import Session

from entidades import UsuarioDB
from servicio_auth import emitir_token
from servicio_seguridad import GestorSeguridad
from validadores import RegistroPeticion


class ControladorUsuarios:
    def __init__(self, db: Session):
        self.db = db
        self.seguridad = GestorSeguridad()

    def registrar_nuevo_usuario(self, datos: RegistroPeticion):
        if self.db.query(UsuarioDB).filter(UsuarioDB.correo_electronico == datos.correo_electronico).first():
            return False, "El correo ya está registrado."

        if self.db.query(UsuarioDB).filter(UsuarioDB.usuario_login == datos.usuario_login).first():
            return False, "El nombre de usuario ya está en uso."

        nuevo_usuario = UsuarioDB(
            nombre_completo=datos.nombre_completo,
            codigo=datos.codigo,
            correo_electronico=datos.correo_electronico,
            usuario_login=datos.usuario_login,
            hash_acceso=self.seguridad.encriptar_clave(datos.password),
            tipo_cuenta=datos.tipo_cuenta,
        )
        self.db.add(nuevo_usuario)
        self.db.commit()
        return True, "Registro exitoso."

    def autenticar_usuario(self, usuario_str: str, clave_plana: str):
        usuario = self.db.query(UsuarioDB).filter(UsuarioDB.usuario_login == usuario_str).first()

        if not usuario:
            return False, "Usuario no encontrado."

        if not self.seguridad.validar_clave(clave_plana, usuario.hash_acceso):
            return False, "Contraseña incorrecta."

        token = emitir_token(usuario)
        return True, {
            "token": token,
            "tipo_token": "Bearer",
            "id": usuario.id,
            "nombre": usuario.nombre_completo,
            "rol": usuario.tipo_cuenta,
            "usuario": usuario.usuario_login,
        }
