from pydantic import BaseModel, EmailStr, Field

class RegistroPeticion(BaseModel):
    nombre_completo: str = Field(..., min_length=3, max_length=50)
    correo_electronico: EmailStr
    password: str = Field(..., min_length=6, description="La contraseña debe tener al menos 6 caracteres")
    tipo_cuenta: str = Field(..., pattern="^(estudiante|tutor)$")

class LoginPeticion(BaseModel):
    correo_electronico: EmailStr
    password: str