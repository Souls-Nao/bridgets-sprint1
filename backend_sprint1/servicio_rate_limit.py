import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict


class LimitadorPorIP:
    """
    Sliding-window token bucket sencillo.
    Mantiene una cola de timestamps por IP; permite N peticiones cada
    ventana de tiempo. Implementación in-memory, thread-safe, suficiente
    para un único proceso uvicorn.
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


limitador_disponibilidad = LimitadorPorIP(max_peticiones=30, ventana_seg=60)
