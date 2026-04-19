from sqlalchemy import Column, Integer, String
from motor_db import Base

class UsuarioDB(Base):
    __tablename__ = "cuentas_usuario"

    id = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String, nullable=False)
    correo_electronico = Column(String, unique=True, index=True, nullable=False)
    hash_acceso = Column(String, nullable=False)
    tipo_cuenta = Column(String, nullable=False) # 'estudiante' o 'tutor'
