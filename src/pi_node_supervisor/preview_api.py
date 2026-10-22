"""Preview HTTP routes: session issuance and authenticated stream forwarding."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from .api_auth import ApiAuthentication
from .preview_stream import PreviewRequestRejected, open_preview_stream, stream_body


def create_preview_router(controller, authentication: ApiAuthentication) -> APIRouter:
    router = APIRouter()

    @router.post("/api/camera-session", dependencies=[Depends(authentication.bearer)])
    async def camera_session(request: Request):
        return authentication.issue_preview_cookie(request)

    def camera_endpoint(device_id: str) -> str:
        if controller.process_manager is None:
            raise HTTPException(status_code=503, detail="CaptureNode is not running")
        endpoint = controller.process_manager.endpoints.get(device_id)
        if endpoint is None:
            raise HTTPException(status_code=404, detail="camera does not exist")
        return endpoint

    async def preview_response(device_id: str, request: Request) -> StreamingResponse:
        endpoint = camera_endpoint(device_id)
        try:
            reader, writer, media_type, chunked = await open_preview_stream(
                endpoint,
                format=request.query_params.get("format"),
                accept=", ".join(request.headers.getlist("accept"))
                if "accept" in request.headers
                else None,
            )
        except PreviewRequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except (
            TimeoutError,
            OSError,
            ValueError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as exc:
            raise HTTPException(
                status_code=502, detail=f"preview stream failed: {exc}"
            ) from exc

        return StreamingResponse(
            stream_body(reader, writer, chunked),
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "Vary": "Accept",
            },
        )

    @router.get(
        "/api/camera/{device_id}", dependencies=[Depends(authentication.preview)]
    )
    async def camera_preview(device_id: str, request: Request) -> StreamingResponse:
        return await preview_response(device_id, request)

    @router.get(
        "/api/nodes/{device_id}/preview",
        dependencies=[Depends(authentication.bearer)],
    )
    async def node_preview(device_id: str, request: Request) -> StreamingResponse:
        return await preview_response(device_id, request)

    @router.get(
        "/api/nodes/{device_id}/preview/stream",
        dependencies=[Depends(authentication.bearer)],
    )
    async def node_preview_stream(
        device_id: str, request: Request
    ) -> StreamingResponse:
        return await preview_response(device_id, request)

    return router
