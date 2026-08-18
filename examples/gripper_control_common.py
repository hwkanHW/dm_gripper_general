#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRIPPER_SDK_ROOT = PROJECT_ROOT / "dm_gripper_py"
if str(GRIPPER_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(GRIPPER_SDK_ROOT))

from dm_lingkong_grip_sdk import LingkongGrip  # noqa: E402

DEFAULT_LEFT_IP = "192.168.14.11"
DEFAULT_RIGHT_IP = "192.168.14.10"
DEFAULT_SINGLE_IP = DEFAULT_RIGHT_IP
DEFAULT_GRIPPER_PORT = 55551
DEFAULT_CONTROL_SEND_HZ = 10.0

WINDOW_BG = (24, 27, 30)
TEXT = (235, 235, 235)
MUTED = (165, 165, 165)
TRACK = (68, 68, 68)
FILL = (58, 93, 166)


@dataclass(frozen=True)
class GripperEndpoint:
    name: str
    server: str


@dataclass(frozen=True)
class ControlButton:
    label: str
    action: str
    rect: tuple[int, int, int, int]
    target: str | None = None


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


def add_gripper_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--speed", type=int, default=50, help="10..100")
    parser.add_argument("--torque", type=int, default=50, help="10..100")
    parser.add_argument("--settle", type=float, default=0.0, help="seconds after each move")
    parser.add_argument("--min-pos", type=int, default=0, help="SDK position lower bound")
    parser.add_argument("--max-pos", type=int, default=1000, help="SDK position upper bound")
    parser.add_argument("--control-send-hz", type=float, default=DEFAULT_CONTROL_SEND_HZ)


def normalize_common_args(args: argparse.Namespace) -> argparse.Namespace:
    args.min_pos = int(clamp(args.min_pos, 0, 1000))
    args.max_pos = int(clamp(args.max_pos, 0, 1000))
    args.speed = int(clamp(args.speed, 10, 100))
    args.torque = int(clamp(args.torque, 10, 100))
    args.settle = max(float(args.settle), 0.0)
    args.control_send_hz = max(float(args.control_send_hz), 0.1)
    if args.min_pos > args.max_pos:
        raise SystemExit("--min-pos must be <= --max-pos")
    return args


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
        self.grip.set_torque_limit(self.args.torque)
        self.grip.set_speed(self.args.speed)
        return "opened"

    def close(self) -> None:
        if self.grip is not None:
            self.grip.close()
            self.grip = None

    def move_to(self, pos: int) -> str:
        if self.grip is None:
            return "ERR not connected"
        pos = int(clamp(pos, self.args.min_pos, self.args.max_pos))
        self.grip.set_torque_limit(self.args.torque)
        self.grip.set_speed(self.args.speed)
        if not self.grip.move_to_pos(pos):
            return f"ERR move {pos} failed"
        if self.args.settle > 0:
            time.sleep(self.args.settle)
        return f"move {pos}"


class GripperControlApp:
    def __init__(self, args: argparse.Namespace, endpoints: list[GripperEndpoint], window_name: str):
        self.args = args
        self.endpoints = endpoints
        self.window_name = window_name
        self.grippers = {endpoint.name: DirectGripper(endpoint, args) for endpoint in endpoints}
        self.target_pos = {endpoint.name: int(args.max_pos) for endpoint in endpoints}
        self.last_send_time = {endpoint.name: 0.0 for endpoint in endpoints}
        self.status = "Ready"
        self.buttons: list[ControlButton] = []
        self.dragging: str | None = None
        self.quit_requested = False

    def open(self) -> None:
        replies = []
        for endpoint in self.endpoints:
            replies.append(f"{endpoint.name}: {self.grippers[endpoint.name].open()}")
        self.status = "; ".join(replies)

    def close(self) -> None:
        for gripper in self.grippers.values():
            gripper.close()
        cv2.destroyAllWindows()

    def position_from_x(self, x: int, rect: tuple[int, int, int, int]) -> int:
        x1, _, x2, _ = rect
        ratio = (x - x1) / max(1, x2 - x1)
        target = self.args.min_pos + ratio * (self.args.max_pos - self.args.min_pos)
        return int(round(clamp(target, self.args.min_pos, self.args.max_pos)))

    def send_to(self, endpoint: GripperEndpoint, pos: int, *, force: bool = False) -> None:
        now = time.monotonic()
        min_interval = 1.0 / max(float(self.args.control_send_hz), 0.1)
        if not force and now - self.last_send_time[endpoint.name] < min_interval:
            return
        self.last_send_time[endpoint.name] = now
        self.target_pos[endpoint.name] = pos
        self.status = f"{endpoint.name}: {self.grippers[endpoint.name].move_to(pos)}"

    def send_all(self, pos: int) -> None:
        replies = []
        for endpoint in self.endpoints:
            self.target_pos[endpoint.name] = pos
            replies.append(f"{endpoint.name}: {self.grippers[endpoint.name].move_to(pos)}")
        self.status = "; ".join(replies)

    def draw(self) -> np.ndarray:
        width = 920
        row_height = 82
        height = 88 + row_height * len(self.endpoints) + 64
        image = np.zeros((height, width, 3), dtype=np.uint8)
        image[:] = WINDOW_BG
        cv2.putText(image, self.window_name, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, TEXT, 1, cv2.LINE_AA)
        cv2.putText(
            image,
            f"range={self.args.min_pos}-{self.args.max_pos} speed={self.args.speed} torque={self.args.torque}",
            (18, 64),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            MUTED,
            1,
            cv2.LINE_AA,
        )

        self.buttons = []
        for index, endpoint in enumerate(self.endpoints):
            self.draw_slider(image, endpoint, 96 + index * row_height)

        y = height - 50
        for label, action, x in (
            ("Close (L)", "close", 18),
            ("Open (P)", "open", 150),
            ("Quit (Q)", "quit", 266),
        ):
            x2 = x + 116
            cv2.rectangle(image, (x, y), (x2, y + 36), (58, 58, 58), -1)
            cv2.rectangle(image, (x, y), (x2, y + 36), (160, 160, 160), 1)
            cv2.putText(image, label, (x + 10, y + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.52, TEXT, 1, cv2.LINE_AA)
            self.buttons.append(ControlButton(label, action, (x, y, x2, y + 36)))

        cv2.putText(image, self.status[:130], (420, height - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.46, TEXT, 1, cv2.LINE_AA)
        return image

    def draw_slider(self, image: np.ndarray, endpoint: GripperEndpoint, y: int) -> None:
        track_x1 = 150
        track_x2 = image.shape[1] - 36
        track_y = y + 26
        cv2.putText(image, endpoint.name, (22, y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.64, TEXT, 1, cv2.LINE_AA)
        cv2.putText(image, f"{self.target_pos[endpoint.name]}", (22, y + 62), cv2.FONT_HERSHEY_SIMPLEX, 0.52, MUTED, 1, cv2.LINE_AA)
        cv2.rectangle(image, (track_x1, track_y), (track_x2, track_y + 16), TRACK, -1)
        cv2.rectangle(image, (track_x1, track_y), (track_x2, track_y + 16), (170, 170, 170), 1)
        span = max(1, self.args.max_pos - self.args.min_pos)
        ratio = (self.target_pos[endpoint.name] - self.args.min_pos) / span
        knob_x = int(track_x1 + ratio * (track_x2 - track_x1))
        cv2.rectangle(image, (track_x1, track_y), (knob_x, track_y + 16), FILL, -1)
        cv2.circle(image, (knob_x, track_y + 8), 13, (245, 245, 245), -1)
        cv2.circle(image, (knob_x, track_y + 8), 13, (40, 40, 40), 1)
        self.buttons.append(ControlButton(endpoint.name, "move", (track_x1, track_y - 18, track_x2, track_y + 34), endpoint.name))

    def endpoint_by_name(self, name: str) -> GripperEndpoint:
        for endpoint in self.endpoints:
            if endpoint.name == name:
                return endpoint
        raise KeyError(name)

    def handle_move(self, x: int, button: ControlButton, *, force: bool = False) -> None:
        if button.target is None:
            return
        endpoint = self.endpoint_by_name(button.target)
        self.send_to(endpoint, self.position_from_x(x, button.rect), force=force)

    def handle_action(self, action: str) -> None:
        if action == "close":
            self.send_all(self.args.min_pos)
        elif action == "open":
            self.send_all(self.args.max_pos)
        elif action == "quit":
            self.quit_requested = True

    def handle_mouse(self, event: int, x: int, y: int, flags: int, param) -> None:
        move_buttons = [button for button in self.buttons if button.action == "move"]
        for button in move_buttons:
            x1, y1, x2, y2 = button.rect
            if event == cv2.EVENT_LBUTTONDOWN and x1 <= x <= x2 and y1 <= y <= y2:
                self.dragging = button.target
                self.handle_move(x, button)
                return
        if event == cv2.EVENT_MOUSEMOVE and self.dragging is not None:
            button = next((item for item in move_buttons if item.target == self.dragging), None)
            if button is not None:
                self.handle_move(x, button)
                return
        if event == cv2.EVENT_LBUTTONUP and self.dragging is not None:
            target = self.dragging
            self.dragging = None
            button = next((item for item in move_buttons if item.target == target), None)
            if button is not None:
                self.handle_move(x, button, force=True)
                return
        if event != cv2.EVENT_LBUTTONUP:
            return
        for button in self.buttons:
            if button.action == "move":
                continue
            x1, y1, x2, y2 = button.rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                self.handle_action(button.action)
                return

    def handle_key(self, key: int) -> bool:
        key &= 0xFF
        if key in {ord("q"), 27}:
            return False
        if key in {ord("l"), ord("L")}:
            self.handle_action("close")
        elif key in {ord("p"), ord("P")}:
            self.handle_action("open")
        return True

    def run(self) -> int:
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.window_name, self.handle_mouse)
        while True:
            cv2.imshow(self.window_name, self.draw())
            if self.quit_requested:
                return 0
            if not self.handle_key(cv2.waitKey(20)):
                return 0
