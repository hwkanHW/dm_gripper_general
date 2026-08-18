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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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

DEFAULT_LEFT_GRIPPER = "192.168.14.11:55551"
DEFAULT_RIGHT_GRIPPER = "192.168.14.10:55551"


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    if runtime is None:
        raise RuntimeError("server runtime was not configured")
    runtime.start()
    try:
        yield
    finally:
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
    parser.add_argument("--speed", type=int, default=50, help="initial speed, 10..100")
    parser.add_argument("--torque", type=int, default=50, help="initial torque, 10..100")
    parser.add_argument("--min-pos", type=int, default=0, help="command lower bound, 0..1000")
    parser.add_argument("--max-pos", type=int, default=1000, help="command upper bound, 0..1000")
    return parser


def main(argv=None) -> None:
    global runtime
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
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
