#!/usr/bin/env python3
"""One-window single-hand dashboard for camera, tactile sensors, and gripper.
    单手版本dashboard样例
Controls:
    Drag the bottom gripper bar to move continuously between closed/open positions
    L: close gripper to the configured minimum position
    P: open gripper
    R: reset selected tactile sensors
    Q/ESC: quit

"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from dm_gripper_cam_py.remote_camera import RemoteCameraCapture  # noqa: E402
from dm_gripper_tac_py.tac import (  # noqa: E402
    SensorManager,
    disable_proxy_for_host,
    make_dashboard as make_tactile_dashboard,
    make_options as make_tactile_options,
)
from gripper_control_common import DirectGripper, GripperEndpoint  # noqa: E402


WINDOW_NAME = "Daimond Pack Single Dashboard"
QUIT_KEYS = {ord("q"), 27}
KEY_GRIP = ord("l")
KEY_RELEASE = ord("p")
KEY_RESET_TACTILE = ord("r")

CAMERA_PANEL_SIZE = (1280, 720)
TACTILE_PANEL_SIZE = (640, 480)
CONTROL_BAR_HEIGHT = 96

# Change this IP in the script, or pass --ip on the command line.
DEFAULT_IP = "192.168.14.10"
DEFAULT_LOCAL_HOST = "192.168.14.123"
DEFAULT_GRIPPER_PORT = 55551

# Frequency defaults exposed for script-level tuning.
DEFAULT_CAMERA_FPS = 60
DEFAULT_TACTILE_MAX_FPS = 120
DEFAULT_CONTROL_SEND_HZ = 10.0


def direct_gripper_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        clamp_pos=args.gripper_clamp_pos,
        open_pos=args.gripper_open_pos,
        max_itinerary=args.gripper_max_itinerary,
        speed_coe=args.gripper_speed_coe,
        calibration_tolerance=args.gripper_calibration_tolerance,
        connect_attempts=args.gripper_connect_attempts,
        connect_timeout_sec=args.gripper_connect_timeout_sec,
        connect_retry_delay_sec=args.gripper_connect_retry_delay_sec,
        allow_homing_fallback=args.gripper_allow_homing_fallback,
        min_pos=args.gripper_min_pos,
        max_pos=args.gripper_max_pos,
        move_speed=args.gripper_release_speed,
        move_torque=args.gripper_release_torque,
        hold_torque=args.gripper_hold_torque,
        release_target=args.gripper_release_target,
        control_send_hz=args.control_send_hz,
    )


def run_direct_gripper_command(
    gripper: DirectGripper,
    command: str,
    args: argparse.Namespace,
) -> str:
    if command == "grip":
        return gripper.move_to(args.gripper_min_pos)
    if command == "release":
        return gripper.move_to(args.gripper_release_target)
    if command.startswith("move "):
        return gripper.move_to(int(command.split(maxsplit=1)[1]))
    raise ValueError(f"Unknown gripper command: {command}")


@dataclass(frozen=True)
class CameraSpec:
    """One remote camera connection tuple."""

    name: str
    host: str
    udp_port: int


@dataclass(frozen=True)
class ControlButton:
    label: str
    action: str
    rect: tuple[int, int, int, int]
    value: int | None = None


def make_camera_dashboard(
    frames: list[np.ndarray | None],
    size: tuple[int, int],
) -> np.ndarray:
    """Build a compact camera panel without depending on old cam_py UI helpers."""

    width, height = size
    images: list[np.ndarray] = []
    for frame in frames:
        if frame is None:
            image = np.zeros((height, width, 3), dtype=np.uint8)
        else:
            image = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        images.append(image)
    if not images:
        return np.zeros((height, width, 3), dtype=np.uint8)
    return np.hstack(images)


class CameraWorker:
    """Read one remote camera in a background thread and keep the newest frame."""

    def __init__(self, spec: CameraSpec, args: argparse.Namespace) -> None:
        self.spec = spec
        self.cap = RemoteCameraCapture(
            host=spec.host,
            port=args.port,
            codec=args.codec,
            width=args.width,
            height=args.height,
            fps=args.fps,
            window_name=spec.name,
            client_ip=args.client_ip,
            udp_port=spec.udp_port,
            bind_host=args.bind_host,
            device=args.device,
        )
        self.frame: np.ndarray | None = None
        self.frame_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.error: str | None = None
        self.frames_this_interval = 0
        self.last_stats_time = time.monotonic()

    def open(self) -> None:
        self.cap.open()
        print(
            f"[{self.spec.name}] opened {self.cap.get('codec')} "
            f"{self.cap.get('width')}x{self.cap.get('height')}@{self.cap.get('fps')} "
            f"host={self.spec.host} device={self.cap.get('device')} "
            f"session={self.cap.get('session_id')}",
            flush=True,
        )

    def start(self, read_timeout: float) -> None:
        self.thread = threading.Thread(
            target=self._run,
            args=(read_timeout,),
            daemon=True,
        )
        self.thread.start()

    def _run(self, read_timeout: float) -> None:
        while not self.stop_event.is_set() and self.cap.isOpened():
            ok, frame = self.cap.read(timeout=read_timeout)
            if not ok:
                self.error = self.cap.get("error")
                continue
            with self.frame_lock:
                self.frame = frame
            self.frames_this_interval += 1

    def latest_frame(self) -> np.ndarray | None:
        with self.frame_lock:
            if self.frame is None:
                return None
            return self.frame.copy()

    def maybe_print_stats(self, interval: float) -> None:
        now = time.monotonic()
        elapsed = now - self.last_stats_time
        if elapsed < interval:
            return
        decode_fps = self.frames_this_interval / elapsed
        print(
            f"[{self.spec.name}] decode_fps={decode_fps:.1f} "
            f"frame_id={self.cap.get('frame_id')} "
            f"decoded={self.cap.get('decoded_frames')} "
            f"dropped={self.cap.get('dropped_frames')} "
            f"drop_ratio={self.cap.get('drop_ratio') * 100:.1f}% "
            f"server_sent={self.cap.get('server_frames_sent')}",
            flush=True,
        )
        self.frames_this_interval = 0
        self.last_stats_time = now

    def close(self) -> None:
        self.stop_event.set()
        self.cap.release()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1.0)


@dataclass(frozen=True)
class TactileSpec:
    """One tactile sensor's fixed connection tuple."""

    sensor_id: int
    remote_addr: str
    dev_id: str
    pc_host: str
    pc_port: int


def tactile_specs_for_args(args: argparse.Namespace) -> dict[int, TactileSpec]:
    return {
        1: TactileSpec(
            sensor_id=1,
            remote_addr=f"{args.ip}:{args.tactile_remote_port_1}",
            dev_id=str(args.tactile_dev_id_1),
            pc_host=args.tactile_pc_host,
            pc_port=args.tactile_pc_port_1,
        ),
        2: TactileSpec(
            sensor_id=2,
            remote_addr=f"{args.ip}:{args.tactile_remote_port_2}",
            dev_id=str(args.tactile_dev_id_2),
            pc_host=args.tactile_pc_host,
            pc_port=args.tactile_pc_port_2,
        ),
    }


def camera_spec_from_args(args: argparse.Namespace) -> CameraSpec:
    return CameraSpec(args.camera_window_name, args.camera_host, args.camera_udp_port)


def tactile_specs_from_selection(
    all_specs: dict[int, TactileSpec],
    sensor_ids: list[int] | None = None,
    count: int | None = None,
) -> list[TactileSpec]:
    """Return tactile specs by user-facing ids.

    Examples:
        tactile_specs_from_selection(specs, [1]) -> sensor 1
        tactile_specs_from_selection(specs, count=2) -> sensors 1 and 2
        tactile_specs_from_selection(specs) -> both sensors
    """

    if sensor_ids:
        ids = sensor_ids
    elif count is not None:
        ids = list(sorted(all_specs))[: int(count)]
    else:
        ids = list(sorted(all_specs))

    specs: list[TactileSpec] = []
    seen: set[int] = set()
    for sensor_id in ids:
        if sensor_id in seen:
            continue
        seen.add(sensor_id)
        if sensor_id not in all_specs:
            valid = ", ".join(str(key) for key in sorted(all_specs))
            raise ValueError(f"Unknown tactile id {sensor_id}; valid ids: {valid}")
        specs.append(all_specs[sensor_id])
    return specs


def make_tactile_args(spec: TactileSpec, args: argparse.Namespace) -> argparse.Namespace:
    """Build the small args object expected by tactile helpers."""

    return argparse.Namespace(
        dev_id=spec.dev_id,
        backend=args.backend,
        remote_addr=spec.remote_addr,
        pc_host=spec.pc_host,
        pc_port=spec.pc_port,
        max_fps=args.max_fps,
        force=args.force,
    )


def clamp(value: int | float, low: int | float, high: int | float):
    return max(low, min(high, value))


def normalize_key(key: int) -> int:
    key &= 0xFF
    if ord("A") <= key <= ord("Z"):
        return key + 32
    return key


def draw_label(image: np.ndarray, text: str) -> np.ndarray:
    canvas = image.copy()
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 30), (0, 0, 0), -1)
    cv2.putText(
        canvas,
        text,
        (10, 21),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    return canvas


def make_blank(size: tuple[int, int], text: str) -> np.ndarray:
    width, height = size
    image = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        image,
        text,
        (18, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        (180, 180, 180),
        1,
        cv2.LINE_AA,
    )
    return image


def make_control_bar(
    width: int,
    status_text: str,
    busy: bool,
    y_offset: int,
    target_pos: int,
    min_pos: int,
    max_pos: int,
) -> tuple[np.ndarray, list[ControlButton]]:
    panel = np.zeros((CONTROL_BAR_HEIGHT, width, 3), dtype=np.uint8)
    panel[:] = (28, 31, 34)
    cv2.line(panel, (0, 0), (width, 0), (75, 75, 75), 1)

    hitboxes: list[ControlButton] = []
    track_x1 = 16
    track_x2 = max(track_x1 + 180, width - 284)
    track_y = 38
    track_h = 14
    cv2.putText(
        panel,
        f"Gripper position {target_pos}",
        (track_x1, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (240, 240, 240),
        1,
        cv2.LINE_AA,
    )
    cv2.rectangle(panel, (track_x1, track_y), (track_x2, track_y + track_h), (70, 70, 70), -1)
    cv2.rectangle(panel, (track_x1, track_y), (track_x2, track_y + track_h), (170, 170, 170), 1)
    span = max(1, max_pos - min_pos)
    ratio = (int(clamp(target_pos, min_pos, max_pos)) - min_pos) / span
    knob_x = int(track_x1 + ratio * (track_x2 - track_x1))
    cv2.rectangle(panel, (track_x1, track_y), (knob_x, track_y + track_h), (58, 93, 166), -1)
    cv2.circle(panel, (knob_x, track_y + track_h // 2), 13, (245, 245, 245), -1)
    cv2.circle(panel, (knob_x, track_y + track_h // 2), 13, (40, 40, 40), 1)
    cv2.putText(
        panel,
        f"{min_pos}",
        (track_x1, 76),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (190, 190, 190),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        panel,
        f"{max_pos}",
        (max(track_x2 - 46, track_x1 + 40), 76),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (190, 190, 190),
        1,
        cv2.LINE_AA,
    )
    hitboxes.append(ControlButton("Position", "move", (track_x1, y_offset + 14, track_x2, y_offset + 72)))

    buttons = [
        ("Reset (R)", "reset", (84, 84, 84)),
        ("Quit (Q)", "quit", (48, 48, 48)),
    ]
    button_width = 118
    button_height = 42
    gap = 10
    x = track_x2 + 18
    y = 18
    for label, action, color in buttons:
        x2 = min(x + button_width, width - 8)
        enabled = not busy or action == "quit"
        fill = color if enabled else (62, 62, 62)
        cv2.rectangle(panel, (x, y), (x2, y + button_height), fill, -1)
        cv2.rectangle(panel, (x, y), (x2, y + button_height), (180, 180, 180), 1)
        cv2.putText(
            panel,
            label,
            (x + 10, y + 27),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (245, 245, 245) if enabled else (165, 165, 165),
            1,
            cv2.LINE_AA,
        )
        hitboxes.append(
            ControlButton(label, action, (x, y_offset + y, x2, y_offset + y + button_height))
        )
        x = x2 + gap

    status_x = 16
    status_y = 91
    cv2.putText(
        panel,
        status_text[:150],
        (status_x, status_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.46,
        (230, 230, 230),
        1,
        cv2.LINE_AA,
    )
    return panel, hitboxes


def resize_to_width(image: np.ndarray, width: int) -> np.ndarray:
    height = max(1, int(image.shape[0] * width / image.shape[1]))
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


class TactileWorker:
    """Read tactile frames in the background and keep the newest dashboard data."""

    def __init__(self, spec: TactileSpec, args: argparse.Namespace) -> None:
        self.spec = spec
        self.args = args
        self.sensor: SensorManager | None = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.latest_data: dict | None = None
        self.error: str | None = None
        self.opening = False
        self.frames_read = 0

    def open(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        with self.lock:
            self.opening = True
            self.error = None
        print(
            "[tactile] opening "
            f"id={self.spec.sensor_id} remote={self.args.remote_addr} "
            f"dev_id={self.args.dev_id} pc={self.args.pc_host}:{self.args.pc_port} "
            f"backend={self.args.backend}",
            flush=True,
        )
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _open_sensor(self) -> None:
        disable_proxy_for_host(self.args.remote_addr)
        self.sensor = SensorManager(make_tactile_options(self.args))
        with self.lock:
            self.opening = False
            self.error = None
        print(f"[tactile] Tactile {self.spec.sensor_id} opened", flush=True)

    def _run(self) -> None:
        try:
            self._open_sensor()
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.opening = False
                self.error = str(exc)
            print(
                f"[tactile] Tactile {self.spec.sensor_id} failed to open: {exc}",
                flush=True,
            )
            return

        while not self.stop_event.is_set():
            assert self.sensor is not None
            try:
                if str(self.args.backend).strip().lower() == "flux":
                    self.sensor.sensor.getEvents()
                if not self.sensor.update():
                    with self.lock:
                        self.error = "no new frame"
                    continue
                data = self.sensor.read()
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    self.error = str(exc)
                time.sleep(0.2)
                continue
            with self.lock:
                self.latest_data = data
                self.error = None
                self.frames_read += 1

    def latest_dashboard(self) -> np.ndarray:
        with self.lock:
            data = dict(self.latest_data) if self.latest_data is not None else None
            error = self.error
            opening = self.opening
            frames_read = self.frames_read
        if data is None:
            if opening:
                text = f"Tactile {self.spec.sensor_id} opening"
            elif error:
                text = f"Tactile {self.spec.sensor_id} waiting: {error}"
            else:
                text = f"Tactile {self.spec.sensor_id} waiting"
            return make_blank(TACTILE_PANEL_SIZE, text)
        dashboard = make_tactile_dashboard(data)
        if error and frames_read == 0:
            return draw_label(dashboard, f"Tactile {self.spec.sensor_id}: {error}")
        return dashboard

    def reset(self) -> str:
        if self.opening:
            return f"Tactile {self.spec.sensor_id} is opening"
        if self.sensor is None:
            return f"Tactile {self.spec.sensor_id} is not open"
        try:
            self.sensor.reset()
        except Exception as exc:  # noqa: BLE001
            return f"Tactile {self.spec.sensor_id} reset failed: {exc}"
        return f"Tactile {self.spec.sensor_id} reset"

    def close(self) -> None:
        self.stop_event.set()
        if (
            self.thread is not None
            and self.thread.is_alive()
            and threading.current_thread() is not self.thread
        ):
            self.thread.join(timeout=1.0)
        if self.sensor is not None:
            self.sensor.close()


def make_tactile_grid(
    dashboards: list[tuple[TactileSpec, np.ndarray]],
    tile_size: tuple[int, int] = (480, 360),
) -> np.ndarray:
    """Arrange the selected tactile dashboards in a compact 1/2/3/4 grid."""

    if not dashboards:
        return make_blank(TACTILE_PANEL_SIZE, "No tactile sensors selected")

    tiles: list[np.ndarray] = []
    for spec, dashboard in dashboards:
        tile = cv2.resize(dashboard, tile_size, interpolation=cv2.INTER_AREA)
        tiles.append(draw_label(tile, f"Tactile {spec.sensor_id}"))

    columns = 1 if len(tiles) == 1 else 2
    rows: list[np.ndarray] = []
    blank = make_blank(tile_size, "")
    for row_start in range(0, len(tiles), columns):
        row_tiles = tiles[row_start : row_start + columns]
        while len(row_tiles) < columns:
            row_tiles.append(blank.copy())
        rows.append(np.hstack(row_tiles))
    return np.vstack(rows)


class DaimondPackSingleApp:
    """Coordinates one remote camera, tactile visualization, and gripper commands."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.camera_workers = [CameraWorker(camera_spec_from_args(args), args)]
        self.tactile_specs = args.tactile_specs
        self.tactile_workers = [
            TactileWorker(spec, make_tactile_args(spec, args))
            for spec in self.tactile_specs
        ]
        self.gripper = DirectGripper(
            GripperEndpoint("gripper", args.gripper_server),
            direct_gripper_args(args),
        )
        self.command_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="gripper-command",
        )
        self.gripper_future: Future | None = None
        self.status = "Ready"
        self.control_buttons: list[ControlButton] = []
        self.quit_requested = False
        self.gripper_target_pos = int(args.gripper_release_target)
        self.dragging_gripper_bar = False
        self.last_control_send_time = 0.0

    def open(self) -> None:
        for worker in self.camera_workers:
            try:
                worker.open()
                worker.start(self.args.read_timeout)
            except Exception as exc:  # noqa: BLE001
                worker.error = str(exc)
                print(f"[camera] {worker.spec.name} failed to open: {exc}", flush=True)

        for worker in self.tactile_workers:
            worker.open()
            time.sleep(self.args.tactile_startup_stagger_sec)

        gripper_status = self.gripper.open()
        ids = ",".join(str(spec.sensor_id) for spec in self.tactile_specs) or "none"
        self.status = (
            f"Ready: ip={self.args.ip} gripper={gripper_status} tactile={ids}; "
            "L close, P open, R reset tactile, Q quit"
        )

    def close(self) -> None:
        for worker in self.camera_workers:
            worker.close()
        for worker in self.tactile_workers:
            worker.close()
        self.gripper.close()
        self.command_executor.shutdown(wait=True, cancel_futures=True)
        cv2.destroyAllWindows()

    def _collect_gripper_result(self) -> None:
        if self.gripper_future is None or not self.gripper_future.done():
            return
        try:
            self.status = self.gripper_future.result()
        except Exception as exc:  # noqa: BLE001
            self.status = f"Gripper command failed: {exc}"
        self.gripper_future = None

    def send_gripper(self, command: str) -> None:
        if self.gripper_future is not None and not self.gripper_future.done():
            self.status = "Gripper command already running"
            return
        self.status = f"Gripper {command} running"
        self.gripper_future = self.command_executor.submit(
            run_direct_gripper_command,
            self.gripper,
            command,
            self.args,
        )

    def reset_tactile(self) -> None:
        results = [worker.reset() for worker in self.tactile_workers]
        self.status = "; ".join(results) if results else "No tactile sensors selected"

    def position_from_control_x(self, x: int, rect: tuple[int, int, int, int]) -> int:
        x1, _, x2, _ = rect
        ratio = (x - x1) / max(1, x2 - x1)
        target = self.args.gripper_min_pos + ratio * (
            self.args.gripper_release_target - self.args.gripper_min_pos
        )
        return int(round(clamp(target, self.args.gripper_min_pos, self.args.gripper_release_target)))

    def send_gripper_position(self, target_pos: int, *, force: bool = False) -> None:
        now = time.monotonic()
        min_interval = 1.0 / max(float(self.args.control_send_hz), 0.1)
        if not force and now - self.last_control_send_time < min_interval:
            return
        self.gripper_target_pos = target_pos
        self.last_control_send_time = now
        self.send_gripper(f"move {target_pos}")

    def render(self) -> np.ndarray:
        frames = []
        any_open = False
        for worker in self.camera_workers:
            if worker.cap.isOpened():
                any_open = True
            frames.append(worker.latest_frame())
            worker.maybe_print_stats(self.args.stats_interval)

        camera_panel = make_camera_dashboard(frames, CAMERA_PANEL_SIZE)
        camera_panel = draw_label(camera_panel, f"Camera {self.args.camera_host}")

        tactile_panel = make_tactile_grid(
            [
                (worker.spec, worker.latest_dashboard())
                for worker in self.tactile_workers
            ]
        )

        width = max(camera_panel.shape[1], tactile_panel.shape[1])
        camera_panel = resize_to_width(camera_panel, width)
        tactile_panel = resize_to_width(tactile_panel, width)

        camera_state = "camera:on" if any_open else "camera:waiting"
        status_text = (
            f"{self.status} | {camera_state} | "
            f"gripper={self.args.gripper_server}"
        )
        body = np.vstack([camera_panel, tactile_panel])
        control_bar, self.control_buttons = make_control_bar(
            body.shape[1],
            status_text,
            self.gripper_future is not None and not self.gripper_future.done(),
            body.shape[0],
            self.gripper_target_pos,
            self.args.gripper_min_pos,
            self.args.gripper_release_target,
        )
        return np.vstack([body, control_bar])

    def handle_control_action(self, action: str) -> None:
        if (
            action != "quit"
            and self.gripper_future is not None
            and not self.gripper_future.done()
        ):
            self.status = "Gripper command already running"
            return
        if action == "grip":
            self.send_gripper("grip")
        elif action == "release":
            self.send_gripper("release")
        elif action == "reset":
            self.reset_tactile()
        elif action == "quit":
            self.quit_requested = True

    def handle_mouse(self, event: int, x: int, y: int, flags: int, param) -> None:
        move_button = next((button for button in self.control_buttons if button.action == "move"), None)
        if move_button is not None:
            x1, y1, x2, y2 = move_button.rect
            if event == cv2.EVENT_LBUTTONDOWN and x1 <= x <= x2 and y1 <= y <= y2:
                self.dragging_gripper_bar = True
                self.gripper_target_pos = self.position_from_control_x(x, move_button.rect)
                self.send_gripper_position(self.gripper_target_pos)
                return
            if event == cv2.EVENT_MOUSEMOVE and self.dragging_gripper_bar:
                self.gripper_target_pos = self.position_from_control_x(x, move_button.rect)
                self.send_gripper_position(self.gripper_target_pos)
                return
            if event == cv2.EVENT_LBUTTONUP and self.dragging_gripper_bar:
                self.dragging_gripper_bar = False
                self.gripper_target_pos = self.position_from_control_x(x, move_button.rect)
                self.send_gripper_position(self.gripper_target_pos, force=True)
                return
        if event != cv2.EVENT_LBUTTONUP:
            return
        for button in self.control_buttons:
            if button.action == "move":
                continue
            x1, y1, x2, y2 = button.rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                self.handle_control_action(button.action)
                return

    def handle_key(self, key: int) -> bool:
        key = normalize_key(key)
        if key in QUIT_KEYS:
            return False
        if key == KEY_GRIP:
            self.gripper_target_pos = self.args.gripper_min_pos
            self.handle_control_action("grip")
        elif key == KEY_RELEASE:
            self.gripper_target_pos = self.args.gripper_release_target
            self.handle_control_action("release")
        elif key == KEY_RESET_TACTILE:
            self.handle_control_action("reset")
        return True

    def run(self) -> int:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(WINDOW_NAME, self.handle_mouse)
        while True:
            self._collect_gripper_result()
            cv2.imshow(WINDOW_NAME, self.render())
            if self.quit_requested:
                return 0
            if not self.handle_key(cv2.waitKey(1)):
                return 0


def add_gripper_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gripper-server", default=None)
    parser.add_argument("--gripper-clamp-pos", type=int, default=-52525)
    parser.add_argument("--gripper-open-pos", type=int, default=-142525)
    parser.add_argument("--gripper-max-itinerary", type=int, default=90000)
    parser.add_argument("--gripper-speed-coe", type=int, default=3600)
    parser.add_argument("--gripper-calibration-tolerance", type=int, default=150)
    parser.add_argument("--gripper-connect-attempts", type=int, default=3)
    parser.add_argument("--gripper-connect-timeout-sec", type=float, default=5.0)
    parser.add_argument("--gripper-connect-retry-delay-sec", type=float, default=0.5)
    parser.add_argument(
        "--gripper-allow-homing-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Allow SDK grip_init() if known calibration init fails. "
            "This performs a homing motion."
        ),
    )
    parser.add_argument("--gripper-min-pos", type=int, default=200)
    parser.add_argument("--gripper-max-pos", type=int, default=1000)
    parser.add_argument("--gripper-grip-speed", type=int, default=60)
    parser.add_argument("--gripper-grip-torque", type=int, default=30)
    parser.add_argument("--gripper-hold-torque", type=int, default=10)
    parser.add_argument("--gripper-current-threshold", type=int, default=120)
    parser.add_argument("--gripper-poll-interval", type=float, default=0.05)
    parser.add_argument("--gripper-contact-grace", type=float, default=0.4)
    parser.add_argument("--gripper-progress-epsilon", type=int, default=2)
    parser.add_argument("--gripper-stall-samples", type=int, default=5)
    parser.add_argument("--gripper-timeout", type=float, default=20.0)
    parser.add_argument("--gripper-release-target", type=int, default=1000)
    parser.add_argument("--gripper-release-speed", type=int, default=40)
    parser.add_argument("--gripper-release-torque", type=int, default=20)
    parser.add_argument("--gripper-release-wait", type=float, default=0.5)


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-window single-hand camera + tactile + direct gripper dashboard"
    )

    parser.add_argument(
        "--ip",
        default=DEFAULT_IP,
        help=(
            "Device IP for this single gripper pack. Camera uses this host, "
            "gripper defaults to IP:55551, tactile defaults to IP:50052 and IP:50051."
        ),
    )

    # Remote single camera options mirror dm_gripper_cam_py.
    parser.add_argument("--camera-host", default=None)
    parser.add_argument("--port", type=int, default=50088)
    parser.add_argument("--codec", choices=("HEVC", "MJPG"), default="MJPG")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=DEFAULT_CAMERA_FPS)
    parser.add_argument("--read-timeout", type=float, default=0.5)
    parser.add_argument("--stats-interval", type=float, default=1.0)
    parser.add_argument(
        "--client-ip",
        default=DEFAULT_LOCAL_HOST,
        help="Client IP sent to the camera server for UDP return traffic.",
    )
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--camera-udp-port", type=int, default=None)
    parser.add_argument("--device", default="")
    parser.add_argument("--camera-window-name", default=None)

    # Tactile options: ids 1 and 2 are derived from --ip by default.
    parser.add_argument(
        "--tactile-ids",
        nargs="+",
        type=int,
        default=None,
        metavar="ID",
        help="Tactile sensor ids to show. Valid ids are 1 2. Default: both.",
    )
    parser.add_argument(
        "--tactile-count",
        type=int,
        default=None,
        help="Show the first N tactile sensors by id. Ignored when --tactile-ids is set.",
    )
    parser.add_argument("--backend", default="Flux")
    parser.add_argument("--max-fps", type=int, default=DEFAULT_TACTILE_MAX_FPS)
    parser.add_argument(
        "--control-send-hz",
        type=float,
        default=DEFAULT_CONTROL_SEND_HZ,
        help="Maximum gripper position commands sent per second while dragging the control bar.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--tactile-remote-port-1", type=int, default=50052)
    parser.add_argument("--tactile-remote-port-2", type=int, default=50051)
    parser.add_argument("--tactile-dev-id-1", default="2")
    parser.add_argument("--tactile-dev-id-2", default="0")
    parser.add_argument(
        "--tactile-pc-host",
        default=DEFAULT_LOCAL_HOST,
        help="Local PC host reported to tactile sensors.",
    )
    parser.add_argument("--tactile-pc-port-1", type=int, default=60031)
    parser.add_argument("--tactile-pc-port-2", type=int, default=60030)
    parser.add_argument(
        "--tactile-startup-stagger-sec",
        type=float,
        default=0.25,
        help="Delay between starting tactile workers to avoid overloading sensor servers.",
    )

    add_gripper_args(parser)
    return parser


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    args.ip = str(args.ip).strip()
    if not args.ip:
        raise SystemExit("--ip must not be empty")
    if args.camera_host is None:
        args.camera_host = args.ip
    if args.camera_window_name is None:
        args.camera_window_name = f"Camera {args.ip}"
    if args.camera_udp_port is None:
        args.camera_udp_port = 0
    if args.gripper_server is None:
        args.gripper_server = f"{args.ip}:{DEFAULT_GRIPPER_PORT}"

    if args.tactile_count is not None:
        args.tactile_count = max(int(args.tactile_count), 0)
    args.backend = "Flux" if str(args.backend).strip().lower() == "flux" else args.backend
    args.tactile_startup_stagger_sec = max(
        float(args.tactile_startup_stagger_sec),
        0.0,
    )
    all_tactile_specs = tactile_specs_for_args(args)
    try:
        args.tactile_specs = tactile_specs_from_selection(
            all_tactile_specs,
            args.tactile_ids,
            args.tactile_count,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    args.gripper_min_pos = int(clamp(args.gripper_min_pos, 0, 1000))
    args.gripper_max_pos = int(clamp(args.gripper_max_pos, 0, 1000))
    args.gripper_release_target = int(clamp(args.gripper_release_target, 0, 1000))
    args.gripper_grip_speed = int(clamp(args.gripper_grip_speed, 10, 100))
    args.gripper_release_speed = int(clamp(args.gripper_release_speed, 10, 100))
    args.gripper_grip_torque = int(clamp(args.gripper_grip_torque, 10, 100))
    args.gripper_hold_torque = int(clamp(args.gripper_hold_torque, 10, 100))
    args.gripper_release_torque = int(clamp(args.gripper_release_torque, 10, 100))
    args.camera_host = str(args.camera_host)
    args.camera_window_name = str(args.camera_window_name)
    args.camera_udp_port = int(args.camera_udp_port)
    args.gripper_server = str(args.gripper_server)
    args.gripper_poll_interval = max(float(args.gripper_poll_interval), 0.02)
    args.gripper_contact_grace = max(float(args.gripper_contact_grace), 0.0)
    args.gripper_progress_epsilon = max(int(args.gripper_progress_epsilon), 0)
    args.gripper_stall_samples = max(int(args.gripper_stall_samples), 1)
    args.gripper_timeout = max(float(args.gripper_timeout), 0.1)
    args.gripper_release_wait = max(float(args.gripper_release_wait), 0.0)
    args.control_send_hz = max(float(args.control_send_hz), 0.1)
    args.gripper_calibration_tolerance = max(
        int(args.gripper_calibration_tolerance),
        0,
    )
    args.gripper_allow_homing_fallback = bool(args.gripper_allow_homing_fallback)
    args.gripper_connect_attempts = max(int(args.gripper_connect_attempts), 1)
    args.gripper_connect_timeout_sec = max(
        float(args.gripper_connect_timeout_sec),
        0.1,
    )
    args.gripper_connect_retry_delay_sec = max(
        float(args.gripper_connect_retry_delay_sec),
        0.0,
    )
    if args.gripper_min_pos > args.gripper_max_pos:
        raise SystemExit("--gripper-min-pos must be <= --gripper-max-pos")
    return args


def main() -> int:
    args = normalize_args(build_argparser().parse_args())
    app = DaimondPackSingleApp(args)
    try:
        app.open()
        return app.run()
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
