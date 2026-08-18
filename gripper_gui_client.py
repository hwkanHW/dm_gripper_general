#!/usr/bin/env python3
"""OpenCV GUI client for the Daimon gripper server."""
from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import cv2
import numpy as np
import requests
import websocket

if not hasattr(websocket, "create_connection"):
    raise RuntimeError(
        "gripper_gui_client.py requires the PyPI package 'websocket-client'. "
        "The installed/imported 'websocket' module does not provide create_connection. "
        "Run: python -m pip uninstall -y websocket && python -m pip install websocket-client"
    )

WINDOW_BG = (24, 27, 30)
TEXT = (235, 235, 235)
MUTED = (165, 165, 165)
TRACK = (68, 68, 68)
FILL = (58, 93, 166)
WARN = (78, 105, 210)


@dataclass(frozen=True)
class ControlButton:
    label: str
    action: str
    rect: tuple[int, int, int, int]
    target: str | None = None


def clamp(value: int | float, low: int | float, high: int | float):
    return max(low, min(high, value))


def make_ws_url(server_url: str, path: str, query: str | None = None) -> str:
    http_url = urljoin(server_url.rstrip("/") + "/", path.lstrip("/"))
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=scheme, query=query or ""))


class GripperRemoteClient:
    def __init__(self, server_url: str, timeout_sec: float, rate_hz: float):
        self.server_url = server_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.rate_hz = rate_hz
        self.session = requests.Session()
        self.command_ws: websocket.WebSocket | None = None
        self.state_ws: websocket.WebSocket | None = None
        self.state: dict[str, Any] = {}
        self.state_error = ""
        self.state_lock = threading.Lock()
        self.reader_running = False
        self.reader_thread: threading.Thread | None = None

    def open(self) -> None:
        self._check_server()
        self.command_ws = websocket.create_connection(
            make_ws_url(self.server_url, "ws/command", "ack=true"),
            timeout=self.timeout_sec,
        )
        state_query = f"rate_hz={self.rate_hz:.3f}"
        self.state_ws = websocket.create_connection(
            make_ws_url(self.server_url, "ws/state", state_query),
            timeout=max(self.timeout_sec, 2.0 / max(self.rate_hz, 1.0)),
        )
        self.reader_running = True
        self.reader_thread = threading.Thread(target=self._read_state_loop, daemon=True)
        self.reader_thread.start()

    def _check_server(self) -> None:
        try:
            response = self.session.get(f"{self.server_url}/state", timeout=self.timeout_sec)
        except Exception as exc:
            raise RuntimeError(f"cannot reach gripper server at {self.server_url}: {exc}") from exc
        if response.status_code == 404:
            raise RuntimeError(
                f"{self.server_url} is not serving gripper_server.py. "
                "Start/restart it with: python gripper_server.py --host 0.0.0.0 --port 8020"
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("grippers"), dict):
            raise RuntimeError(
                f"{self.server_url}/state is not a gripper_server.py state response. "
                "Check --server-url and restart the gripper server."
            )

    def close(self) -> None:
        self.reader_running = False
        for ws in (self.state_ws, self.command_ws):
            if ws is not None:
                ws.close()
        if self.reader_thread is not None and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=0.5)

    def _read_state_loop(self) -> None:
        assert self.state_ws is not None
        self.state_ws.settimeout(max(self.timeout_sec, 2.0 / max(self.rate_hz, 1.0)))
        while self.reader_running:
            try:
                payload = json.loads(self.state_ws.recv())
            except Exception as exc:
                with self.state_lock:
                    self.state_error = str(exc)
                time.sleep(0.2)
                continue
            with self.state_lock:
                self.state = payload if isinstance(payload, dict) else {}
                self.state_error = ""

    def snapshot(self) -> tuple[dict[str, Any], str]:
        with self.state_lock:
            return dict(self.state), self.state_error

    def send_command(self, side: str, position: int, speed: int | None = None, torque: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"side": side, "position": int(position)}
        if speed is not None:
            payload["speed"] = int(speed)
        if torque is not None:
            payload["torque"] = int(torque)

        if self.command_ws is not None:
            self.command_ws.send(json.dumps(payload, separators=(",", ":")))
            reply = json.loads(self.command_ws.recv())
            return reply if isinstance(reply, dict) else {"success": False, "error": "non-dict reply"}

        response = self.session.post(f"{self.server_url}/command", json=payload, timeout=self.timeout_sec)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"success": False, "error": "non-dict reply"}


class GripperGui:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.client = GripperRemoteClient(args.server_url, args.timeout_sec, args.rate_hz)
        self.window_name = "Daimon Gripper Remote"
        self.target_pos = {side: args.max_pos for side in args.sides}
        self.last_send_time = {side: 0.0 for side in args.sides}
        self.buttons: list[ControlButton] = []
        self.dragging: str | None = None
        self.status = "Ready"
        self.quit_requested = False

    def open(self) -> None:
        self.client.open()
        self.status = f"connected: {self.args.server_url}"

    def close(self) -> None:
        self.client.close()
        cv2.destroyAllWindows()

    def position_from_x(self, x: int, rect: tuple[int, int, int, int]) -> int:
        x1, _, x2, _ = rect
        ratio = (x - x1) / max(1, x2 - x1)
        target = self.args.min_pos + ratio * (self.args.max_pos - self.args.min_pos)
        return int(round(clamp(target, self.args.min_pos, self.args.max_pos)))

    def send_to(self, side: str, pos: int, *, force: bool = False) -> None:
        now = time.monotonic()
        min_interval = 1.0 / max(float(self.args.control_send_hz), 0.1)
        if not force and now - self.last_send_time[side] < min_interval:
            return
        self.last_send_time[side] = now
        self.target_pos[side] = int(pos)
        try:
            result = self.client.send_command(side, pos, self.args.speed, self.args.torque)
        except Exception as exc:
            self.status = f"{side}: ERR {exc}"
            return
        if result.get("success", False):
            self.status = f"{side}: move {pos}"
        else:
            self.status = f"{side}: ERR {result.get('error') or result}"

    def send_all(self, pos: int) -> None:
        for side in self.args.sides:
            self.target_pos[side] = int(pos)
        try:
            result = self.client.send_command("all", pos, self.args.speed, self.args.torque)
        except Exception as exc:
            self.status = f"all: ERR {exc}"
            return
        self.status = f"all: move {pos}" if result.get("success", False) else f"all: ERR {result}"

    def draw(self) -> np.ndarray:
        state, state_error = self.client.snapshot()
        width = 920
        row_height = 92
        height = 88 + row_height * len(self.args.sides) + 64
        image = np.zeros((height, width, 3), dtype=np.uint8)
        image[:] = WINDOW_BG
        cv2.putText(image, self.window_name, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, TEXT, 1, cv2.LINE_AA)
        cv2.putText(
            image,
            f"{self.args.server_url}  range={self.args.min_pos}-{self.args.max_pos} speed={self.args.speed} torque={self.args.torque}",
            (18, 64),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            MUTED,
            1,
            cv2.LINE_AA,
        )

        self.buttons = []
        grippers = state.get("grippers") if isinstance(state.get("grippers"), dict) else {}
        for index, side in enumerate(self.args.sides):
            side_state = grippers.get(side) if isinstance(grippers.get(side), dict) else {}
            self.draw_slider(image, side, side_state, 96 + index * row_height)

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

        footer = state_error or self.status
        cv2.putText(image, footer[:130], (420, height - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.46, TEXT, 1, cv2.LINE_AA)
        return image

    def draw_slider(self, image: np.ndarray, side: str, side_state: dict[str, Any], y: int) -> None:
        track_x1 = 150
        track_x2 = image.shape[1] - 36
        track_y = y + 28
        connected = bool(side_state.get("connected"))
        position = side_state.get("position")
        shown_position = self.target_pos[side]

        label_color = TEXT if connected else WARN
        cv2.putText(image, side, (22, y + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.64, label_color, 1, cv2.LINE_AA)
        cv2.putText(
            image,
            f"target {shown_position}  actual {position}",
            (22, y + 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            MUTED,
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(image, (track_x1, track_y), (track_x2, track_y + 16), TRACK, -1)
        cv2.rectangle(image, (track_x1, track_y), (track_x2, track_y + 16), (170, 170, 170), 1)
        span = max(1, self.args.max_pos - self.args.min_pos)
        ratio = (shown_position - self.args.min_pos) / span
        knob_x = int(track_x1 + ratio * (track_x2 - track_x1))
        cv2.rectangle(image, (track_x1, track_y), (knob_x, track_y + 16), FILL, -1)
        cv2.circle(image, (knob_x, track_y + 8), 13, (245, 245, 245), -1)
        cv2.circle(image, (knob_x, track_y + 8), 13, (40, 40, 40), 1)
        self.buttons.append(ControlButton(side, "move", (track_x1, track_y - 18, track_x2, track_y + 34), side))

    def handle_move(self, x: int, button: ControlButton, *, force: bool = False) -> None:
        if button.target is None:
            return
        self.send_to(button.target, self.position_from_x(x, button.rect), force=force)

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


def parse_sides(value: str) -> list[str]:
    sides = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [side for side in sides if side not in {"left", "right"}]
    if invalid:
        raise argparse.ArgumentTypeError(f"invalid side(s): {', '.join(invalid)}")
    return sides or ["left", "right"]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control a gripper_server.py instance with an OpenCV GUI")
    parser.add_argument("--server-url", default="http://127.0.0.1:8020")
    parser.add_argument("--sides", type=parse_sides, default=["left", "right"], help="left,right or one side")
    parser.add_argument("--rate-hz", type=float, default=20.0)
    parser.add_argument("--timeout-sec", type=float, default=2.0)
    parser.add_argument("--speed", type=int, default=50, help="10..100")
    parser.add_argument("--torque", type=int, default=50, help="10..100")
    parser.add_argument("--min-pos", type=int, default=0)
    parser.add_argument("--max-pos", type=int, default=1000)
    parser.add_argument("--control-send-hz", type=float, default=10.0)
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.speed = int(clamp(args.speed, 10, 100))
    args.torque = int(clamp(args.torque, 10, 100))
    args.min_pos = int(clamp(args.min_pos, 0, 1000))
    args.max_pos = int(clamp(args.max_pos, 0, 1000))
    args.rate_hz = max(float(args.rate_hz), 0.1)
    args.control_send_hz = max(float(args.control_send_hz), 0.1)
    if args.min_pos > args.max_pos:
        parser.error("--min-pos must be <= --max-pos")

    gui = GripperGui(args)
    try:
        gui.open()
        return gui.run()
    except KeyboardInterrupt:
        return 0
    finally:
        gui.close()


if __name__ == "__main__":
    raise SystemExit(main())
