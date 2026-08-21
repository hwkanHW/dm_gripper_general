#!/usr/bin/env python3
"""FastAPI server for controlling Daimon grippers only.

The network shape follows the vlahost server/client style:
- HTTP endpoints for health, state snapshots, and low-frequency commands.
- WebSocket endpoints for state streaming and continuous command streaming.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

try:
    import websockets  # noqa: F401
except ImportError:  # pragma: no cover
    websockets = None

PROJECT_ROOT = Path(__file__).resolve().parent
GRIPPER_SDK_ROOT = PROJECT_ROOT / "dm_gripper_py"
if str(GRIPPER_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(GRIPPER_SDK_ROOT))

from dm_lingkong_grip_sdk import LingkongGrip  # noqa: E402

CAMERA_SDK_ROOT = PROJECT_ROOT / "dm_gripper_cam_py"
if str(CAMERA_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(CAMERA_SDK_ROOT))

try:
    import cv2  # noqa: E402
    from remote_camera import RemoteCameraCapture  # noqa: E402
except ImportError:  # pragma: no cover
    cv2 = None
    RemoteCameraCapture = None

DEFAULT_LEFT_GRIPPER = "192.168.14.11:55551"
DEFAULT_RIGHT_GRIPPER = "192.168.14.10:55551"
DEFAULT_LEFT_CAMERA = "192.168.14.10"
DEFAULT_RIGHT_CAMERA = "192.168.14.11"


def clamp(value: int | float, low: int | float, high: int | float):
    return max(low, min(high, value))


def disable_proxy_for_host(addr: str) -> None:
    host = urlsplit(addr if "://" in addr else "//" + addr).hostname
    if not host:
        return
    for key in ("NO_PROXY", "no_proxy"):
        values = os.environ.get(key, "")
        hosts = [item.strip() for item in values.split(",") if item.strip()]
        if host not in hosts:
            hosts.append(host)
        os.environ[key] = ",".join(hosts)


class GripperCommand(BaseModel):
    side: Literal["left", "right", "all"] = "all"
    position: int = Field(..., ge=0, le=1000)
    speed: int | None = Field(default=None, ge=10, le=100)
    torque: int | None = Field(default=None, ge=10, le=100)


class GripperRuntime:
    def __init__(
        self,
        side: str,
        server_address: str,
        *,
        speed: int,
        torque: int,
        min_pos: int,
        max_pos: int,
    ):
        self.side = side
        self.server_address = server_address
        self.speed = int(speed)
        self.torque = int(torque)
        self.min_pos = int(min_pos)
        self.max_pos = int(max_pos)
        self.grip: LingkongGrip | None = None
        self.connected = False
        self.last_error = ""
        self.target_position = self.max_pos
        self.last_command_time: float | None = None
        self.lock = threading.Lock()

    def open(self) -> None:
        with self.lock:
            disable_proxy_for_host(self.server_address)
            self.grip = LingkongGrip(server_address=self.server_address)
            if not self.grip.grip_init():
                self.grip.close()
                self.grip = None
                self.connected = False
                self.last_error = "grip_init failed"
                return
            self.grip.set_torque_limit(self.torque)
            self.grip.set_speed(self.speed)
            self.connected = True
            self.last_error = ""

    def close(self) -> None:
        with self.lock:
            if self.grip is not None:
                self.grip.close()
            self.grip = None
            self.connected = False

    def apply(self, command: GripperCommand) -> dict[str, Any]:
        with self.lock:
            if self.grip is None or not self.connected:
                self.last_error = "not connected"
                return {"success": False, "side": self.side, "error": self.last_error}

            if command.speed is not None:
                self.speed = int(command.speed)
            if command.torque is not None:
                self.torque = int(command.torque)

            position = int(clamp(command.position, self.min_pos, self.max_pos))
            self.grip.set_torque_limit(self.torque)
            self.grip.set_speed(self.speed)
            if not self.grip.move_to_pos(position):
                self.last_error = f"move {position} failed"
                return {"success": False, "side": self.side, "error": self.last_error}

            self.target_position = position
            self.last_command_time = time.time()
            self.last_error = ""
            return {"success": True, "side": self.side, "position": position}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            grip = self.grip
            connected = self.connected and grip is not None
            state: dict[str, Any] = {
                "side": self.side,
                "server_address": self.server_address,
                "connected": connected,
                "target_position": self.target_position,
                "speed": self.speed,
                "torque": self.torque,
                "last_command_time": self.last_command_time,
                "error": self.last_error,
            }
            if not connected:
                state["position"] = None
                return state

            try:
                state.update(
                    {
                        "position": grip.read_pos(),
                        "temperature": grip.read_cur_tempture(),
                        "current": grip.read_cur_current(),
                        "torque_limit": grip.read_torque_limit(),
                    }
                )
            except Exception as exc:
                self.last_error = str(exc)
                state["error"] = self.last_error
            return state


class GripperServer:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.grippers: dict[str, GripperRuntime] = {}
        if args.left_gripper:
            self.grippers["left"] = GripperRuntime(
                "left",
                args.left_gripper,
                speed=args.speed,
                torque=args.torque,
                min_pos=args.min_pos,
                max_pos=args.max_pos,
            )
        if args.right_gripper:
            self.grippers["right"] = GripperRuntime(
                "right",
                args.right_gripper,
                speed=args.speed,
                torque=args.torque,
                min_pos=args.min_pos,
                max_pos=args.max_pos,
            )
        if not self.grippers:
            raise ValueError("at least one gripper address is required")

    def start(self) -> None:
        for gripper in self.grippers.values():
            gripper.open()

    def stop(self) -> None:
        for gripper in self.grippers.values():
            gripper.close()

    def snapshot(self) -> dict[str, Any]:
        return {
            "stamp": time.time(),
            "grippers": {side: gripper.snapshot() for side, gripper in self.grippers.items()},
        }

    def apply_command(self, command: GripperCommand) -> dict[str, Any]:
        if command.side == "all":
            results = {side: gripper.apply(command) for side, gripper in self.grippers.items()}
            return {
                "success": all(result.get("success", False) for result in results.values()),
                "results": results,
            }
        gripper = self.grippers.get(command.side)
        if gripper is None:
            return {"success": False, "error": f"unknown side: {command.side}"}
        return gripper.apply(command)


runtime: GripperServer | None = None
camera_servers: list["CameraMjpegServer"] = []


class CameraMjpegServer:
    def __init__(
        self,
        *,
        side: str,
        host: str,
        port: int,
        camera_host: str,
        camera_port: int,
        codec: str,
        width: int,
        height: int,
        fps: int,
        jpeg_quality: int,
        client_ip: str,
        bind_host: str,
        device: str,
    ):
        self.side = side
        self.host = host
        self.port = int(port)
        self.camera_host = camera_host
        self.camera_port = int(camera_port)
        self.codec = codec
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.jpeg_quality = int(clamp(jpeg_quality, 1, 100))
        self.client_ip = client_ip
        self.bind_host = bind_host
        self.device = device

        self.capture = None
        self.latest_jpeg: bytes | None = None
        self.latest_error = ""
        self.frame_count = 0
        self.last_frame_time = 0.0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.capture_thread: threading.Thread | None = None
        self.server_thread: threading.Thread | None = None
        self.server: uvicorn.Server | None = None
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI(title=f"Daimon {self.side} camera stream")

        @app.get("/health")
        def health():
            return self.snapshot()

        @app.get("/snapshot.jpg")
        def snapshot_jpg():
            with self.lock:
                data = self.latest_jpeg
            if data is None:
                return Response(status_code=503, content=b"no frame")
            return Response(content=data, media_type="image/jpeg")

        @app.get("/video")
        @app.get("/stream.mjpg")
        def video():
            return StreamingResponse(
                self._mjpeg_generator(),
                media_type="multipart/x-mixed-replace; boundary=frame",
            )

        return app

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            has_frame = self.latest_jpeg is not None
            last_age = time.time() - self.last_frame_time if self.last_frame_time else None
            error = self.latest_error
            frame_count = self.frame_count
        return {
            "side": self.side,
            "camera_host": self.camera_host,
            "camera_port": self.camera_port,
            "has_frame": has_frame,
            "frame_count": frame_count,
            "last_frame_age": last_age,
            "error": error,
        }

    def start(self) -> None:
        self.stop_event.clear()
        config = uvicorn.Config(self.app, host=self.host, port=self.port, access_log=False, log_level="warning")
        self.server = uvicorn.Server(config)
        self.server_thread = threading.Thread(target=self.server.run, daemon=True)
        self.server_thread.start()
        if RemoteCameraCapture is None or cv2 is None:
            self.latest_error = "dm_gripper_cam_py and opencv-python are required"
            return
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.server is not None:
            self.server.should_exit = True
        if self.capture is not None:
            self.capture.release()
        if self.capture_thread is not None and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=1.0)
        if self.server_thread is not None and self.server_thread.is_alive():
            self.server_thread.join(timeout=1.0)

    def _capture_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.capture = RemoteCameraCapture(
                    host=self.camera_host,
                    port=self.camera_port,
                    codec=self.codec,
                    width=self.width,
                    height=self.height,
                    fps=self.fps,
                    client_ip=self.client_ip,
                    bind_host=self.bind_host,
                    device=self.device,
                )
                self.capture.open()
                while not self.stop_event.is_set() and self.capture.isOpened():
                    ok, frame = self.capture.read(timeout=1.0)
                    if not ok or frame is None:
                        continue
                    ok, encoded = cv2.imencode(
                        ".jpg",
                        frame,
                        [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
                    )
                    if not ok:
                        continue
                    with self.lock:
                        self.latest_jpeg = encoded.tobytes()
                        self.latest_error = ""
                        self.frame_count += 1
                        self.last_frame_time = time.time()
            except Exception as exc:
                with self.lock:
                    self.latest_error = str(exc)
                time.sleep(1.0)
            finally:
                if self.capture is not None:
                    self.capture.release()
                    self.capture = None

    def _mjpeg_generator(self):
        while not self.stop_event.is_set():
            with self.lock:
                data = self.latest_jpeg
            if data is None:
                time.sleep(0.05)
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(data)).encode("ascii") + b"\r\n\r\n" +
                data +
                b"\r\n"
            )
            time.sleep(1.0 / max(self.fps, 1))


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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Daimon gripper-only FastAPI server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--left-gripper", default=DEFAULT_LEFT_GRIPPER, help="left gripper host:port, empty to disable")
    parser.add_argument("--right-gripper", default=DEFAULT_RIGHT_GRIPPER, help="right gripper host:port, empty to disable")
    parser.add_argument("--speed", type=int, default=100, help="initial speed, 10..100")
    parser.add_argument("--torque", type=int, default=50, help="initial torque, 10..100")
    parser.add_argument("--min-pos", type=int, default=0, help="command lower bound, 0..1000")
    parser.add_argument("--max-pos", type=int, default=1000, help="command upper bound, 0..1000")
    parser.add_argument(
        "--enable-image-streams",
        dest="enable_image_streams",
        action="store_true",
        default=True,
        help="serve left/right camera MJPEG streams (enabled by default)",
    )
    parser.add_argument(
        "--disable-image-streams",
        dest="enable_image_streams",
        action="store_false",
        help="do not serve left/right camera MJPEG streams",
    )
    parser.add_argument("--image-host", default="0.0.0.0", help="bind address for MJPEG image servers")
    parser.add_argument("--left-image-port", type=int, default=8021)
    parser.add_argument("--right-image-port", type=int, default=8022)
    parser.add_argument("--left-camera-host", default=DEFAULT_LEFT_CAMERA)
    parser.add_argument("--right-camera-host", default=DEFAULT_RIGHT_CAMERA)
    parser.add_argument("--camera-port", type=int, default=50088)
    parser.add_argument("--camera-codec", choices=("MJPG", "HEVC"), default="MJPG")
    parser.add_argument("--camera-width", type=int, default=1280)
    parser.add_argument("--camera-height", type=int, default=720)
    parser.add_argument("--camera-fps", type=int, default=60)
    parser.add_argument("--camera-jpeg-quality", type=int, default=80)
    parser.add_argument("--camera-client-ip", default="")
    parser.add_argument("--camera-bind-host", default="0.0.0.0")
    parser.add_argument("--camera-device", default="")
    return parser


def main(argv=None) -> None:
    global runtime, camera_servers
    if websockets is None:
        raise RuntimeError(
            "gripper_server.py requires a uvicorn WebSocket protocol backend. "
            "Run: python -m pip install websockets"
        )
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.left_gripper = args.left_gripper.strip() or None
    args.right_gripper = args.right_gripper.strip() or None
    args.speed = int(clamp(args.speed, 10, 100))
    args.torque = int(clamp(args.torque, 10, 100))
    args.min_pos = int(clamp(args.min_pos, 0, 1000))
    args.max_pos = int(clamp(args.max_pos, 0, 1000))
    if args.min_pos > args.max_pos:
        parser.error("--min-pos must be <= --max-pos")

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
