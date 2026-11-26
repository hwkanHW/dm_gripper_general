"""Server and direct SDK adapters; each retains its existing control lifecycle."""
from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass
from typing import Any

from dm_lingkong_grip_sdk import LingkongGrip
from .server.models import GripperCommand
from .units import normalize_reading, percent_to_sdk, position_to_sdk
from .utils import clamp, disable_proxy_for_host


class GripperRuntime:
    def __init__(
        self,
        side: str,
        server_address: str,
        *,
        speed: float,
        torque: float,
        min_pos: float,
        max_pos: float,
    ):
        self.side = side
        self.server_address = server_address
        self.speed = float(speed)
        self.torque = float(torque)
        self.min_pos = float(min_pos)
        self.max_pos = float(max_pos)
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
            self.grip.set_torque_limit(percent_to_sdk(self.torque))
            self.grip.set_speed(percent_to_sdk(self.speed))
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
                self.speed = float(command.speed)
            if command.torque is not None:
                self.torque = float(command.torque)

            position = float(clamp(command.position, self.min_pos, self.max_pos))
            self.grip.set_torque_limit(percent_to_sdk(self.torque))
            self.grip.set_speed(percent_to_sdk(self.speed))
            if not self.grip.move_to_pos(position_to_sdk(position)):
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
                        "position": normalize_reading(grip.read_pos(), 1000),
                        "temperature": grip.read_cur_tempture(),
                        "current": grip.read_cur_current(),
                        "torque_limit": normalize_reading(grip.read_torque_limit(), 100),
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


@dataclass(frozen=True)
class GripperEndpoint:
    name: str
    server: str


class DirectGripper:
    def __init__(self, endpoint: GripperEndpoint, args: argparse.Namespace):
        self.endpoint = endpoint
        self.args = args
        self.grip: LingkongGrip | None = None
        self.last_error = ""

    def open(self) -> str:
        disable_proxy_for_host(self.endpoint.server)
        self.grip = LingkongGrip(server_address=self.endpoint.server)
        if not self.grip.grip_init():
            self.close()
            self.last_error = "grip_init failed"
            return self.last_error
        self.grip.set_torque_limit(percent_to_sdk(self.args.torque))
        self.grip.set_speed(percent_to_sdk(self.args.speed))
        return "opened"

    def close(self) -> None:
        if self.grip is not None:
            self.grip.close()
            self.grip = None

    def move_to(self, pos: float) -> str:
        if self.grip is None:
            return "ERR not connected"
        pos = float(clamp(pos, self.args.min_pos, self.args.max_pos))
        self.grip.set_torque_limit(percent_to_sdk(self.args.torque))
        self.grip.set_speed(percent_to_sdk(self.args.speed))
        if not self.grip.move_to_pos(position_to_sdk(pos)):
            return f"ERR move {pos} failed"
        if self.args.settle > 0:
            time.sleep(self.args.settle)
        return f"move {pos}"


