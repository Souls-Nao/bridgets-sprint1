import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

# Obtiene la URL de Neon (reemplaza 'postgres://' por 'postgresql://' si es necesario para SQLAlchemy)
URL_BASE_DATOS = os.getenv("DATABASE_URL")

if not URL_BASE_DATOS:
    raise RuntimeError(
        "DATABASE_URL no está definida. Configúrala en el archivo .env "
        "(local) o en las variables de entorno del servicio (Render)."
    )

# SQLAlchemy no acepta el prefijo 'postgres://' que a veces entregan proveedores como Heroku/Neon
if URL_BASE_DATOS.startswith("postgres://"):
    URL_BASE_DATOS = URL_BASE_DATOS.replace("postgres://", "postgresql://", 1)

engine = create_engine(URL_BASE_DATOS, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def obtener_sesion():
    bd = SessionLocal()
    try:
        yield bd
    finally:
        bd.close()