#!/usr/bin/env python3
from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass
from typing import Optional, Sequence

import cv2

try:
    from .remote_camera import RemoteCameraCapture
except ImportError:
    if __package__:
        raise
    from remote_camera import RemoteCameraCapture


DEFAULT_LEFT_IP = "192.168.14.10"
DEFAULT_RIGHT_IP = "192.168.14.11"
DEFAULT_LOCAL_HOST = "192.168.14.123"


@dataclass(frozen=True)
class CameraSpec:
    name: str
    host: str
    udp_port: int


@dataclass(frozen=True)
class CameraViewerConfig:
    left_ip: str = DEFAULT_LEFT_IP
    right_ip: str = DEFAULT_RIGHT_IP
    port: int = 50088
    codec: str = "MJPG"
    width: int = 1920
    height: int = 1080
    fps: int = 60
    read_timeout: float = 0.5
    stats_interval: float = 1.0
    client_ip: str = DEFAULT_LOCAL_HOST
    bind_host: str = "0.0.0.0"
    left_udp_port: int = 0
    right_udp_port: int = 0
    device: str = ""
    left_window_name: str = "Left Camera"
    right_window_name: str = "Right Camera"


def camera_specs_from_config(config: CameraViewerConfig) -> list[CameraSpec]:
    return [
        CameraSpec(config.left_window_name, config.left_ip, config.left_udp_port),
        CameraSpec(config.right_window_name, config.right_ip, config.right_udp_port),
    ]


def config_from_args(args: argparse.Namespace) -> CameraViewerConfig:
    width = args.width
    height = args.height
    if args.video_size is not None:
        width, height = args.video_size
    return CameraViewerConfig(
        left_ip=args.left_host,
        right_ip=args.right_host,
        port=args.port,
        codec=args.codec,
        width=width,
        height=height,
        fps=args.fps,
        read_timeout=args.read_timeout,
        stats_interval=args.stats_interval,
        client_ip=args.client_ip,
        bind_host=args.bind_host,
        left_udp_port=args.left_udp_port,
        right_udp_port=args.right_udp_port,
        device=args.device,
        left_window_name=args.left_window_name,
        right_window_name=args.right_window_name,
    )


def parse_video_size(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected WIDTHxHEIGHT, for example 1280x720") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("video size must be positive")
    return width, height


class CameraWorker:
    def __init__(self, spec: CameraSpec, config: CameraViewerConfig):
        self.spec = spec
        self.cap = RemoteCameraCapture(
            host=spec.host,
            port=config.port,
            codec=config.codec,
            width=config.width,
            height=config.height,
            fps=config.fps,
            window_name=spec.name,
            client_ip=config.client_ip,
            udp_port=spec.udp_port,
            bind_host=config.bind_host,
            device=config.device,
        )
        self.frame = None
        self.frame_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None
        self.opening = False
        self.frames_this_interval = 0
        self.last_stats_time = time.monotonic()

    def open(self) -> None:
        self.opening = True
        self.error = None

    def start(self, read_timeout: float) -> None:
        self.thread = threading.Thread(
            target=self._run,
            args=(read_timeout,),
            daemon=True,
        )
        self.thread.start()

    def _run(self, read_timeout: float) -> None:
        try:
            self.cap.open()
            print(
                f"[{self.spec.name}] opened {self.cap.get('codec')} "
                f"{self.cap.get('width')}x{self.cap.get('height')}@{self.cap.get('fps')} "
                f"host={self.spec.host} device={self.cap.get('device')} "
                f"session={self.cap.get('session_id')}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.opening = False
            print(f"[camera] {self.spec.name} failed to open: {exc}", flush=True)
            return
        self.opening = False

        while not self.stop_event.is_set() and self.cap.isOpened():
            ok, frame = self.cap.read(timeout=read_timeout)
            if not ok:
                self.error = self.cap.get("error")
                continue
            with self.frame_lock:
                self.frame = frame
            self.frames_this_interval += 1

    def latest_frame(self):
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
        state = "opening" if self.opening else (self.error or "waiting")
        print(
            f"[{self.spec.name}] decode_fps={decode_fps:.1f} "
            f"frame_id={self.cap.get('frame_id')} "
            f"decoded={self.cap.get('decoded_frames')} "
            f"dropped={self.cap.get('dropped_frames')} "
            f"drop_ratio={self.cap.get('drop_ratio') * 100:.1f}% "
            f"server_sent={self.cap.get('server_frames_sent')} "
            f"state={state}",
            flush=True,
        )
        self.frames_this_interval = 0
        self.last_stats_time = now

    def close(self) -> None:
        self.stop_event.set()
        self.cap.release()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1.0)


def build_argparser(default_config: CameraViewerConfig | None = None) -> argparse.ArgumentParser:
    if default_config is None:
        default_config = CameraViewerConfig()
    parser = argparse.ArgumentParser(description="Display left and right remote camera streams")
    parser.add_argument("--left-host", "--left-ip", dest="left_host", default=default_config.left_ip)
    parser.add_argument("--right-host", "--right-ip", dest="right_host", default=default_config.right_ip)
    parser.add_argument("--port", type=int, default=default_config.port)
    parser.add_argument("--codec", choices=("HEVC", "MJPG"), default=default_config.codec)
    parser.add_argument("--width", type=int, default=default_config.width)
    parser.add_argument("--height", type=int, default=default_config.height)
    parser.add_argument("--video-size", type=parse_video_size, default=None, metavar="WIDTHxHEIGHT")
    parser.add_argument("--fps", type=int, default=default_config.fps)
    parser.add_argument("--read-timeout", type=float, default=default_config.read_timeout)
    parser.add_argument("--stats-interval", type=float, default=default_config.stats_interval)
    parser.add_argument("--client-ip", default=default_config.client_ip)
    parser.add_argument("--bind-host", default=default_config.bind_host)
    parser.add_argument("--left-udp-port", type=int, default=default_config.left_udp_port)
    parser.add_argument("--right-udp-port", type=int, default=default_config.right_udp_port)
    parser.add_argument("--device", default=default_config.device)
    parser.add_argument("--left-window-name", default=default_config.left_window_name)
    parser.add_argument("--right-window-name", default=default_config.right_window_name)
    return parser


def run_dual_camera_viewer(
    config: CameraViewerConfig | None = None,
    *,
    left_ip: str | None = None,
    right_ip: str | None = None,
    video_size: tuple[int, int] | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
) -> int:
    """Run the dual camera viewer with a small public configuration surface."""

    if config is None:
        config = CameraViewerConfig()
    target_width = config.width
    target_height = config.height
    if video_size is not None:
        target_width, target_height = video_size
    if width is not None:
        target_width = width
    if height is not None:
        target_height = height
    config = CameraViewerConfig(
        left_ip=config.left_ip if left_ip is None else left_ip,
        right_ip=config.right_ip if right_ip is None else right_ip,
        port=config.port,
        codec=config.codec,
        width=target_width,
        height=target_height,
        fps=config.fps if fps is None else fps,
        read_timeout=config.read_timeout,
        stats_interval=config.stats_interval,
        client_ip=config.client_ip,
        bind_host=config.bind_host,
        left_udp_port=config.left_udp_port,
        right_udp_port=config.right_udp_port,
        device=config.device,
        left_window_name=config.left_window_name,
        right_window_name=config.right_window_name,
    )
    workers = [CameraWorker(spec, config) for spec in camera_specs_from_config(config)]

    try:
        for worker in workers:
            worker.open()
            cv2.namedWindow(worker.spec.name, cv2.WINDOW_NORMAL)
            worker.start(config.read_timeout)

        while True:
            for worker in workers:
                frame = worker.latest_frame()
                if frame is not None:
                    cv2.imshow(worker.spec.name, frame)
                worker.maybe_print_stats(config.stats_interval)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q") or key == 27:
                break
    except KeyboardInterrupt:
        pass
    finally:
        for worker in workers:
            worker.close()
        cv2.destroyAllWindows()
        print("[dual-camera] released", flush=True)

    return 0


def main(
    argv: Sequence[str] | None = None,
    default_config: CameraViewerConfig | None = None,
) -> int:
    args = build_argparser(default_config).parse_args(argv)
    return run_dual_camera_viewer(config_from_args(args))


if __name__ == "__main__":
    raise SystemExit(main())
