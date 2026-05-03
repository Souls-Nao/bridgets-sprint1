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
        entregados = 0
        for uid in user_ids:
            if await self.enviar_a(uid, payload):
                entregados += 1
        return entregados

    def esta_conectado(self, user_id: int) -> bool:
        return user_id in self._conexiones


gestor_conexiones = GestorConexiones()
