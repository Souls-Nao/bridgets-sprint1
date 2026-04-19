import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

# Obtiene la URL de Neon (reemplaza 'postgres://' por 'postgresql://' si es necesario para SQLAlchemy)
URL_BASE_DATOS = os.getenv("DATABASE_URL")

engine = create_engine(URL_BASE_DATOS, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def obtener_sesion():
    bd = SessionLocal()
    try:
        yield bd
    finally:
        bd.close()