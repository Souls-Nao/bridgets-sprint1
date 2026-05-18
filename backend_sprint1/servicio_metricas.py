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
        # Videollamadas: un evento por transición (iniciar, aceptar, rechazar,
        # finalizar). La duración solo se suma cuando hay timestamps de inicio
        # y fin (motivo `colgada` u otros estados terminales con aceptación previa).
        self.video_eventos: Dict[str, int] = defaultdict(int)
        self.video_motivos_fin: Dict[str, int] = defaultdict(int)
        self.video_duracion_total_seg = 0.0
        self.video_duraciones_n = 0

    def registrar(self, metodo: str, status: int, duracion_seg: float) -> None:
        clase = f"{status // 100}xx"
        with self._lock:
            self.requests_total += 1
            self.requests_por_metodo[metodo] += 1
            self.requests_por_clase[clase] += 1
            self.tiempo_total_seg += duracion_seg

    def registrar_video(
        self,
        accion: str,
        motivo: str | None = None,
        duracion_seg: float | None = None,
    ) -> None:
        """
        `accion`: iniciada | aceptada | rechazada | finalizada.
        `motivo`: opcional, solo aplica a finalizada (colgada / error_ice / etc.).
        `duracion_seg`: si la sesión tuvo aceptada_en y finalizada_en, se suma
        para calcular el promedio de duración real.
        """
        with self._lock:
            self.video_eventos[accion] += 1
            if motivo:
                self.video_motivos_fin[motivo] += 1
            if duracion_seg is not None and duracion_seg >= 0:
                self.video_duracion_total_seg += duracion_seg
                self.video_duraciones_n += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            ahora = time.time()
            promedio_ms = (
                (self.tiempo_total_seg / self.requests_total) * 1000.0
                if self.requests_total else 0.0
            )
            duracion_video_media_s = (
                self.video_duracion_total_seg / self.video_duraciones_n
                if self.video_duraciones_n else 0.0
            )
            return {
                "uptime_segundos": round(ahora - self._inicio_proceso, 1),
                "requests_total": self.requests_total,
                "requests_por_metodo": dict(self.requests_por_metodo),
                "requests_por_clase": dict(self.requests_por_clase),
                "tiempo_promedio_ms": round(promedio_ms, 2),
                "video_eventos": dict(self.video_eventos),
                "video_motivos_fin": dict(self.video_motivos_fin),
                "video_duracion_media_seg": round(duracion_video_media_s, 1),
            }


metricas = _Metricas()
