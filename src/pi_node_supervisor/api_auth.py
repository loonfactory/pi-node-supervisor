"""Bearer authentication and preview-only signed cookies."""

import hmac
import time

from fastapi import Header, HTTPException, Request, Response


class ApiAuthentication:
    cookie_name = "supervisor-preview"
    session_seconds = 8 * 60 * 60

    def __init__(self, token: str):
        if not token:
            raise ValueError("api_token must not be empty")
        self._token = token

    async def bearer(self, authorization: str | None = Header(None)) -> None:
        expected = f"Bearer {self._token}".encode()
        if authorization is None or not hmac.compare_digest(
            authorization.encode(), expected
        ):
            raise HTTPException(status_code=401, detail="authentication required")

    def _signature(self, expiry: str) -> str:
        return hmac.new(
            self._token.encode(), f"preview:{expiry}".encode(), "sha256"
        ).hexdigest()

    async def preview(self, request: Request) -> None:
        value = request.cookies.get(self.cookie_name, "")
        expiry, _, signature = value.partition(".")
        if (
            not expiry.isascii()
            or not expiry.isdigit()
            or len(expiry) > 12
            or not signature.isascii()
            or not hmac.compare_digest(signature, self._signature(expiry))
            or int(expiry) <= time.time()
        ):
            raise HTTPException(
                status_code=401, detail="preview authentication required"
            )

    def issue_preview_cookie(self, request: Request) -> Response:
        expiry = str(int(time.time()) + self.session_seconds)
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            self.cookie_name,
            f"{expiry}.{self._signature(expiry)}",
            max_age=self.session_seconds,
            path="/api/camera/",
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return response
