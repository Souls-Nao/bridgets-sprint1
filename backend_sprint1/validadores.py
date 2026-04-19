from pydantic import BaseModel, EmailStr, Field

class RegistroPeticion(BaseModel):
    nombre_completo: str = Field(..., min_length=3, max_length=50)
    codigo: str = Field(..., min_length=1)
    correo_electronico: EmailStr
    usuario_login: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6)
    tipo_cuenta: str = Field(..., pattern="^(estudiante|tutor)$")

class LoginPeticion(BaseModel):
    usuario_login: str # Cambiado de correo a usuario
    password: str
