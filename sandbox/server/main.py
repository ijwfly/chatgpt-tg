"""Authenticated sandbox HTTP server, initialized before accepting requests."""

import hmac
import json
import os

from starlette.applications import Starlette

from common import (
    IdentityError, PORT, current_user, ensure_user, initialize_sandbox,
    logger, sanitize_username,
)
from http_api import build_routes

USER_ID_HEADER = b"x-user-id"


class UserIdMiddleware:
    def __init__(self, app, api_token=None):
        self.app = app
        token = os.environ.get("SANDBOX_API_TOKEN", "") if api_token is None else api_token
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("SANDBOX_API_TOKEN must be nonempty")
        try:
            self.authorization = ("Bearer " + token).encode("ascii")
        except UnicodeEncodeError as exc:
            raise RuntimeError("SANDBOX_API_TOKEN must contain ASCII characters") from exc

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("path", "") == "/health":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        authorization = [value for name, value in headers if name == b"authorization"]
        if len(authorization) != 1 or not hmac.compare_digest(authorization[0], self.authorization):
            await self._send_error(send, 401, "Unauthorized")
            return
        user_ids = [value for name, value in headers if name == USER_ID_HEADER]
        try:
            raw_user_id = user_ids[0].decode("ascii") if len(user_ids) == 1 else ""
        except UnicodeDecodeError:
            raw_user_id = ""
        linux_user = sanitize_username(raw_user_id) if raw_user_id else None
        if linux_user is None:
            await self._send_error(send, 400, "Missing or invalid X-User-Id header")
            return

        try:
            ensure_user(linux_user)
        except IdentityError:
            logger.error("Sandbox user provisioning rejected")
            await self._send_error(send, 503, "Sandbox user provisioning failed")
            return

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


def create_app():
    app = UserIdMiddleware(Starlette(routes=build_routes()))
    initialize_sandbox()
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=PORT)
