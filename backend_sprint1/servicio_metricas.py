"""
Métricas in-memory mínimas para `/metrics`. Cubren:
  - tiempo de inicio del proceso (uptime calculable)
  - total de requests procesadas
  - desglose por método HTTP
  - desglose por clase de status (2xx / 3xx / 4xx / 5xx)
  - tiempo total acumulado de respuesta (para promedio)

Sin dependencia de Prometheus para no añadir librerías; el formato JSON es
suficiente para uptime checks / dashboards simples. Si en el futuro se quiere
exponer en formato Prometheus, basta con añadir un endpoint que serialice las
mismas métricas.
"""

import threading
import time
from collections import defaultdict
from typing import Any, Dict


class _Metricas:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inicio_proceso = time.time()
        self.requests_total = 0
        self.requests_por_metodo: Dict[str, int] = defaultdict(int)
        self.requests_por_clase: Dict[str, int] = defaultdict(int)
        self.tiempo_total_seg = 0.0

    def registrar(self, metodo: str, status: int, duracion_seg: float) -> None:
        clase = f"{status // 100}xx"
        with self._lock:
            self.requests_total += 1
            self.requests_por_metodo[metodo] += 1
            self.requests_por_clase[clase] += 1
            self.tiempo_total_seg += duracion_seg

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            ahora = time.time()
            promedio_ms = (
                (self.tiempo_total_seg / self.requests_total) * 1000.0
                if self.requests_total else 0.0
            )
            return {
                "uptime_segundos": round(ahora - self._inicio_proceso, 1),
                "requests_total": self.requests_total,
                "requests_por_metodo": dict(self.requests_por_metodo),
                "requests_por_clase": dict(self.requests_por_clase),
                "tiempo_promedio_ms": round(promedio_ms, 2),
            }


metricas = _Metricas()
