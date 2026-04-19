import bcrypt

class GestorSeguridad:
    @staticmethod
    def encriptar_clave(clave_plana: str) -> str:
        sal = bcrypt.gensalt()
        clave_encriptada = bcrypt.hashpw(clave_plana.encode('utf-8'), sal)
        return clave_encriptada.decode('utf-8')

    @staticmethod
    def validar_clave(clave_plana: str, clave_hash: str) -> bool:
        return bcrypt.checkpw(
            clave_plana.encode('utf-8'), 
            clave_hash.encode('utf-8')
        )
