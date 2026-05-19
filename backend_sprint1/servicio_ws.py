import asyncio
from typing import Any, Dict, Iterable

from fastapi import WebSocket


class GestorConexiones:
    """
    Mapa user_id → WebSocket activo. Una conexión por usuario.
    Si un usuario abre una segunda pestaña, la primera se cierra.
    """

    def __init__(self) -> None:
        self._conexiones: Dict[int, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def conectar(self, user_id: int, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            anterior = self._conexiones.get(user_id)
            self._conexiones[user_id] = ws
        if anterior is not None and anterior is not ws:
            try:
                await anterior.close(code=1000)
            except Exception:
                pass

    async def desconectar(self, user_id: int, ws: WebSocket) -> None:
        async with self._lock:
            if self._conexiones.get(user_id) is ws:
                del self._conexiones[user_id]

    async def enviar_a(self, user_id: int, payload: Dict[str, Any]) -> bool:
        ws = self._conexiones.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            await self.desconectar(user_id, ws)
            return False

    async def enviar_a_varios(self, user_ids: Iterable[int], payload: Dict[str, Any]) -> int:
        """
        Broadcast paralelo: en una llamada grupal con 6 peers un envío lento
        (cliente con red mala) NO bloquea a los otros 5. Sin esto, un
        `await ws.send_json` que tarde 200 ms suma 1.2 s en el broadcast,
        notable cuando los offer/answer/ICE viajan en ráfaga durante mesh.
        `return_exceptions=True` para que un fallo individual no propague.
        """
        ids = list(user_ids)
        if not ids:
            return 0
        resultados = await asyncio.gather(
            *(self.enviar_a(uid, payload) for uid in ids),
            return_exceptions=True,
        )
        return sum(1 for r in resultados if r is True)

    def esta_conectado(self, user_id: int) -> bool:
        return user_id in self._conexiones


gestor_conexiones = GestorConexiones()
