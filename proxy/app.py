import contextlib

import anyio
import fastapi
import httpx
import starlette.background
import starlette.responses
import uvicorn
import websockets

client = httpx.AsyncClient()


@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    yield
    await client.aclose()


service_mapping = {
    "klipper1": "klipper1:7125",
    "klipper2": "klipper2:7125",
}

app = fastapi.FastAPI(lifespan=lifespan)


def strip_connection_headers(headers: dict[str, str]) -> dict[str, str]:
    lower_to_original_map = {k.strip().lower(): k for k in headers.keys()}
    result = headers.copy()

    if "connection" not in lower_to_original_map:
        return result

    connection_header_value = result[lower_to_original_map["connection"]]
    del result[lower_to_original_map["connection"]]

    for c in connection_header_value.split(","):
        c = lower_to_original_map.get(c.strip().lower())
        if c:
            del result[c]

    return result


@app.api_route(
    "/{service_name}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def forward_http_request(
    request_from_client: fastapi.Request,
    service_name: str,
    path: str,
):
    if service_name not in service_mapping:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_name} not found",
        )

    base_url = f"http://{service_mapping[service_name]}"

    request_to_backend = client.build_request(
        method=request_from_client.method,
        url=f"{base_url}/{path}",
        headers=strip_connection_headers(dict(request_from_client.headers)),
        content=request_from_client.stream(),
        params=request_from_client.query_params,
    )

    response_from_backend = await client.send(request_to_backend, stream=True)

    return starlette.responses.StreamingResponse(
        content=response_from_backend.aiter_raw(),
        status_code=response_from_backend.status_code,
        headers=strip_connection_headers(dict(response_from_backend.headers)),
        background=starlette.background.BackgroundTask(response_from_backend.aclose),
    )


@app.websocket("/{service_name}/{path:path}")
async def forward_websocket_connection(
    websocket_with_client: fastapi.WebSocket,
    service_name: str,
    path: str,
):
    if service_name not in service_mapping:
        await websocket_with_client.close(
            code=fastapi.status.WS_1008_POLICY_VIOLATION,
            reason=f"service '{service_name}' unknown",
        )
        return

    await websocket_with_client.accept()

    base_url = f"ws://{service_mapping[service_name]}/{path}"

    websocket_with_backend = await websockets.connect(uri=base_url)

    async def forward_to_backend():
        while True:
            message = await websocket_with_client.receive()
            if message["type"] == "websocket.receive":
                message_content: str | bytes
                if "text" in message:
                    message_content = message["text"]
                else:
                    assert "bytes" in message
                    message_content = message["bytes"]
                await websocket_with_backend.send(message_content)
            else:
                assert message["type"] == "websocket.disconnect"
                task_group.cancel_scope.cancel()
                with anyio.CancelScope(shield=True):
                    await websocket_with_backend.close(
                        code=message["code"],
                        reason=message.get("reason", ""),
                    )
                    return

    async def forward_to_client():
        while True:
            try:
                message = await websocket_with_backend.recv()
            except websockets.exceptions.ConnectionClosed as e:
                task_group.cancel_scope.cancel()
                with anyio.CancelScope(shield=True):
                    await websocket_with_client.close(
                        code=e.code,
                        reason=e.reason,
                    )
                    return
            if isinstance(message, str):
                await websocket_with_client.send_text(message)
            else:
                assert isinstance(message, bytes)
                await websocket_with_client.send_bytes(message)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(forward_to_backend)
        task_group.start_soon(forward_to_client)


def main():
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
        reload=True,
    )


if __name__ == "__main__":
    main()
