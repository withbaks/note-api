import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from note_core.security import decode_token

router = APIRouter(tags=["realtime"])

connections: dict[str, list[WebSocket]] = {}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id: str | None = None
    try:
        auth_msg = await websocket.receive_text()
        data = json.loads(auth_msg)
        token = data.get("token")
        if not token:
            await websocket.close(code=4001)
            return
        payload = decode_token(token)
        if payload.get("type") != "access":
            await websocket.close(code=4001)
            return
        user_id = payload["sub"]
        connections.setdefault(user_id, []).append(websocket)

        await websocket.send_json({"type": "connected", "user_id": user_id})

        while True:
            msg = await websocket.receive_text()
            parsed = json.loads(msg)
            if parsed.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if user_id and user_id in connections:
            connections[user_id] = [ws for ws in connections[user_id] if ws != websocket]
            if not connections[user_id]:
                del connections[user_id]


async def broadcast_to_user(user_id: str, message: dict) -> None:
    for ws in connections.get(user_id, []):
        try:
            await ws.send_json(message)
        except Exception:
            pass
