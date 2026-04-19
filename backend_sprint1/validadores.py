from pydantic import BaseModel, EmailStr, Field

class RegistroPeticion(BaseModel):
    nombre_completo: str = Field(..., min_length=3, max_length=50)
    # Nuevo: Solo permite números (de 1 a 9 dígitos)
    codigo: str = Field(..., pattern="^\d+$", min_length=1, max_length=9)
    correo_electronico: EmailStr
    usuario_login: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6)
    tipo_cuenta: str = Field(..., pattern="^(estudiante|tutor)$")

class LoginPeticion(BaseModel):
    usuario_login: str
    password: str
