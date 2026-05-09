from typing import Tuple

from sqlalchemy.orm import Session

from entidades import UsuarioDB
from servicio_auth import emitir_token
from servicio_seguridad import GestorSeguridad
from validadores import (
    CambiarPasswordPeticion,
    PerfilActualizar,
    RegistroPeticion,
)


class ControladorUsuarios:
    def __init__(self, db: Session):
        self.db = db
        self.seguridad = GestorSeguridad()

    def registrar_nuevo_usuario(self, datos: RegistroPeticion) -> Tuple[bool, str]:
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

    # ---------- Edición de perfil ----------
    def actualizar_perfil(
        self,
        usuario: UsuarioDB,
        datos: PerfilActualizar,
    ) -> Tuple[bool, object]:
        """
        PATCH parcial. Si se cambia `correo_electronico` se valida unicidad.
        Devuelve (True, usuario) o (False, mensaje_error).
        """
        cambios = datos.model_dump(exclude_unset=True)

        if "correo_electronico" in cambios and cambios["correo_electronico"] is not None:
            nuevo_correo = cambios["correo_electronico"].lower().strip()
            if nuevo_correo != (usuario.correo_electronico or "").lower():
                colision = (
                    self.db.query(UsuarioDB.id)
                    .filter(
                        UsuarioDB.correo_electronico == nuevo_correo,
                        UsuarioDB.id != usuario.id,
                    )
                    .first()
                )
                if colision is not None:
                    return False, "El correo ya está registrado por otra cuenta."
                usuario.correo_electronico = nuevo_correo

        if "nombre_completo" in cambios and cambios["nombre_completo"] is not None:
            usuario.nombre_completo = cambios["nombre_completo"].strip()

        if "codigo" in cambios and cambios["codigo"] is not None:
            usuario.codigo = cambios["codigo"].strip()

        self.db.commit()
        self.db.refresh(usuario)
        return True, usuario

    def cambiar_password(
        self,
        usuario: UsuarioDB,
        datos: CambiarPasswordPeticion,
    ) -> Tuple[bool, str]:
        if not self.seguridad.validar_clave(datos.password_actual, usuario.hash_acceso):
            return False, "La contraseña actual no es correcta."
        if datos.password_nueva == datos.password_actual:
            return False, "La nueva contraseña debe ser distinta a la actual."
        usuario.hash_acceso = self.seguridad.encriptar_clave(datos.password_nueva)
        self.db.commit()
        return True, "Contraseña actualizada."

    def eliminar_cuenta(self, usuario: UsuarioDB, password: str) -> Tuple[bool, str]:
        """
        Borra la cuenta tras verificar la contraseña. Las cascadas configuradas
        en las relaciones (clases, inscripciones, notas) y los `ondelete=CASCADE`
        de las FKs (salas_chat, mensajes vía cascada de salas) limpian el resto.
        """
        if not self.seguridad.validar_clave(password, usuario.hash_acceso):
            return False, "Contraseña incorrecta."
        self.db.delete(usuario)
        self.db.commit()
        return True, "Cuenta eliminada."
