from sqlalchemy import Column, Integer, String
from motor_db import Base

class UsuarioDB(Base):
    __tablename__ = "cuentas_v2" # Nueva versión para forzar creación de campos

    id = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String, nullable=False)
    codigo = Column(String, nullable=False) # Nuevo
    correo_electronico = Column(String, unique=True, index=True, nullable=False)
    usuario_login = Column(String, unique=True, index=True, nullable=False) # Nuevo
    hash_acceso = Column(String, nullable=False)
    tipo_cuenta = Column(String, nullable=False) # 'estudiante' o 'tutor'
