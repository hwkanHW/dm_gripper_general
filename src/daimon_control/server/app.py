"""FastAPI routes and startup for gripper control and camera streams."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..config import DEFAULT_CONFIG, build_arg_parser, parse_server_args
from ..gripper import GripperRuntime, GripperServer
from .image_stream import CameraMjpegServer
from .models import GripperCommand

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None

runtime: GripperServer | None = None
camera_servers: list[CameraMjpegServer] = []


@asynccontextmanager
async def lifespan(_: FastAPI):
    if runtime is None:
        raise RuntimeError("server runtime was not configured")
    runtime.start()
    for camera_server in camera_servers:
        camera_server.start()
    try:
        yield
    finally:
        for camera_server in camera_servers:
            camera_server.stop()
        runtime.stop()


app = FastAPI(title="Daimon gripper server", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/state")
def get_state():
    return runtime.snapshot()


@app.post("/command")
def post_command(command: GripperCommand):
    return runtime.apply_command(command)


@app.websocket("/ws/state")
async def state_ws(websocket: WebSocket, rate_hz: float = 20.0):
    await websocket.accept()
    target_hz = rate_hz if rate_hz > 0.0 else 20.0
    period = 1.0 / target_hz
    try:
        while True:
            await websocket.send_json(runtime.snapshot())
            await asyncio.sleep(period)
    except WebSocketDisconnect:
        return


@app.websocket("/ws/command")
async def command_ws(websocket: WebSocket, ack: bool = True):
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            try:
                command = GripperCommand(**payload)
            except ValidationError as exc:
                result = {"success": False, "error": str(exc)}
            else:
                result = runtime.apply_command(command)
            if ack:
                await websocket.send_json(result)
    except WebSocketDisconnect:
        return


def main(argv=None) -> None:
    global runtime, camera_servers
    if websockets is None:
        raise RuntimeError(
            "gripper_server.py requires a uvicorn WebSocket protocol backend. "
            "Run: python -m pip install websockets"
        )
    args = parse_server_args(argv)
    args.left_gripper = args.left_gripper.strip() or None
    args.right_gripper = args.right_gripper.strip() or None
    if args.min_pos > args.max_pos:
        build_arg_parser().error("--min-pos must be <= --max-pos")

    runtime = GripperServer(args)
    camera_servers = []
    if args.enable_image_streams:
        camera_servers = [
            CameraMjpegServer(
                side="left",
                host=args.image_host,
                port=args.left_image_port,
                camera_host=args.left_camera_host,
                camera_port=args.camera_port,
                codec=args.camera_codec,
                width=args.camera_width,
                height=args.camera_height,
                fps=args.camera_fps,
                jpeg_quality=args.camera_jpeg_quality,
                client_ip=args.camera_client_ip,
                bind_host=args.camera_bind_host,
                device=args.camera_device,
            ),
            CameraMjpegServer(
                side="right",
                host=args.image_host,
                port=args.right_image_port,
                camera_host=args.right_camera_host,
                camera_port=args.camera_port,
                codec=args.camera_codec,
                width=args.camera_width,
                height=args.camera_height,
                fps=args.camera_fps,
                jpeg_quality=args.camera_jpeg_quality,
                client_ip=args.camera_client_ip,
                bind_host=args.camera_bind_host,
                device=args.camera_device,
            ),
        ]
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
