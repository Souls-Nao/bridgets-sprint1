import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict


class LimitadorPorIP:
    """
    Sliding-window rate limiter sencillo, in-memory y thread-safe.

    Mantiene una cola de timestamps por clave (típicamente IP). Cada llamada a
    `permitir(clave)` purga timestamps fuera de la ventana y, si el conteo
    queda bajo el máximo, registra el actual y devuelve True.

    Pensado para un único proceso uvicorn — para múltiples workers/instancias
    habría que mover el estado a Redis. Para Render free tier (1 instancia),
    es suficiente.
    """

    def __init__(self, max_peticiones: int, ventana_seg: int):
        self.max_peticiones = max_peticiones
        self.ventana_seg = ventana_seg
        self._historial: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, clave: str) -> bool:
        ahora = time.monotonic()
        limite_inicio = ahora - self.ventana_seg
        with self._lock:
            cola = self._historial[clave]
            while cola and cola[0] < limite_inicio:
                cola.popleft()
            if len(cola) >= self.max_peticiones:
                return False
            cola.append(ahora)
            return True


# Instancias por endpoint. Los límites buscan equilibrio entre detener
# fuerza bruta (OWASP recomienda combinar técnicas) y no molestar a
# usuarios reales que se equivocan al teclear.
limitador_disponibilidad = LimitadorPorIP(max_peticiones=30, ventana_seg=60)
limitador_login = LimitadorPorIP(max_peticiones=10, ventana_seg=300)        # 10 / 5min
limitador_registro = LimitadorPorIP(max_peticiones=5, ventana_seg=600)      # 5 / 10min
