"""Sandbox server entry point: Starlette app + user identification middleware.

The service runs on the internal docker network only. Every request except
/health must carry an X-User-Id header (the bot passes the telegram user id);
the middleware sanitizes it into a linux username, lazily provisions the OS
user + workspace, and stores it in the current_user contextvar for handlers.
"""

import json

from starlette.applications import Starlette

from common import PORT, current_user, ensure_user, sanitize_username
from http_api import build_routes

USER_ID_HEADER = b"x-user-id"


class UserIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("path", "") == "/health":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_user_id = headers.get(USER_ID_HEADER, b"").decode()
        linux_user = sanitize_username(raw_user_id) if raw_user_id else None
        if linux_user is None:
            await self._send_error(send, 400, "Missing or invalid X-User-Id header")
            return

        # Lazy provisioning on every request: the workspace volume persists
        # across container recreations while OS users do not.
        ensure_user(linux_user)

        ctx_token = current_user.set(linux_user)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user.reset(ctx_token)

    @staticmethod
    async def _send_error(send, status: int, detail: str):
        body = json.dumps({"error": detail}).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })


if __name__ == "__main__":
    import uvicorn

    app = Starlette(routes=build_routes())
    app = UserIdMiddleware(app)

    uvicorn.run(app, host="0.0.0.0", port=PORT)
