#!/usr/bin/env python3
"""OpenCV viewer for gripper_server.py left/right MJPEG image ports."""
from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import cv2


@dataclass(frozen=True)
class StreamSpec:
    name: str
    url: str


class MjpegViewerWorker:
    def __init__(self, spec: StreamSpec):
        self.spec = spec
        self.capture: cv2.VideoCapture | None = None
        self.frame = None
        self.frame_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.error = ""
        self.frame_count = 0
        self.last_frame_time = 0.0

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def latest_frame(self):
        with self.frame_lock:
            if self.frame is None:
                return None
            return self.frame.copy()

    def close(self) -> None:
        self.stop_event.set()
        if self.capture is not None:
            self.capture.release()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.capture = cv2.VideoCapture(self.spec.url)
            if not self.capture.isOpened():
                self.error = f"failed to open {self.spec.url}"
                time.sleep(1.0)
                continue

            self.error = ""
            while not self.stop_event.is_set():
                ok, frame = self.capture.read()
                if not ok or frame is None:
                    self.error = f"read failed from {self.spec.url}"
                    break
                with self.frame_lock:
                    self.frame = frame
                    self.frame_count += 1
                    self.last_frame_time = time.time()

            self.capture.release()
            self.capture = None
            time.sleep(0.5)


def build_url(host: str, port: int, path: str) -> str:
    path = "/" + path.lstrip("/")
    if host.startswith("http://") or host.startswith("https://"):
        return f"{host.rstrip('/')}:{port}{path}"
    return f"http://{host}:{port}{path}"


def check_stream_server(url: str, timeout_sec: float) -> str:
    health_url = url.rsplit("/", 1)[0] + "/health"
    try:
        with urlopen(health_url, timeout=timeout_sec) as response:
            if response.status < 400:
                return ""
            return f"{health_url} returned HTTP {response.status}"
    except HTTPError as exc:
        return f"{health_url} returned HTTP {exc.code}"
    except URLError as exc:
        return f"{health_url} is not reachable: {exc.reason}"
    except OSError as exc:
        return f"{health_url} is not reachable: {exc}"


def print_start_server_hint(host: str) -> None:
    print(
        "\n[img_client] 8021/8022 image servers are not running.\n"
        "Start gripper_server.py in another terminal with image streams enabled, for example:\n\n"
        "python3 gripper_server.py --host 0.0.0.0 --port 8020 \\\n"
        "  --left-gripper 192.168.14.11:55551 \\\n"
        "  --right-gripper 192.168.14.10:55551 \\\n"
        "  --enable-image-streams \\\n"
        "  --left-camera-host 192.168.14.10 \\\n"
        "  --right-camera-host 192.168.14.11\n\n"
        f"Then run: python3 img_client.py --host {host}\n",
        file=sys.stderr,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="View left/right gripper_server.py MJPEG streams")
    parser.add_argument("--host", default="127.0.0.1", help="gripper_server host")
    parser.add_argument("--left-port", type=int, default=8021)
    parser.add_argument("--right-port", type=int, default=8022)
    parser.add_argument("--path", default="/video")
    parser.add_argument("--left-url", default="", help="override full left stream URL")
    parser.add_argument("--right-url", default="", help="override full right stream URL")
    parser.add_argument("--stats-interval", type=float, default=2.0)
    parser.add_argument("--connect-timeout", type=float, default=1.0)
    parser.add_argument("--no-precheck", action="store_true", help="skip HTTP /health checks before opening OpenCV streams")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    left_url = args.left_url or build_url(args.host, args.left_port, args.path)
    right_url = args.right_url or build_url(args.host, args.right_port, args.path)
    workers = [
        MjpegViewerWorker(StreamSpec("Left Gripper Image", left_url)),
        MjpegViewerWorker(StreamSpec("Right Gripper Image", right_url)),
    ]

    if not args.no_precheck:
        errors = [(worker.spec.name, check_stream_server(worker.spec.url, args.connect_timeout)) for worker in workers]
        errors = [(name, error) for name, error in errors if error]
        if errors:
            for name, error in errors:
                print(f"[img_client] {name}: {error}", file=sys.stderr, flush=True)
            print_start_server_hint(args.host)
            return 2

    try:
        for worker in workers:
            print(f"[img_client] opening {worker.spec.name}: {worker.spec.url}", flush=True)
            cv2.namedWindow(worker.spec.name, cv2.WINDOW_NORMAL)
            worker.start()

        last_stats = time.monotonic()
        while True:
            for worker in workers:
                frame = worker.latest_frame()
                if frame is not None:
                    cv2.imshow(worker.spec.name, frame)

            # now = time.monotonic()
            # if now - last_stats >= args.stats_interval:
            #     for worker in workers:
            #         age = time.time() - worker.last_frame_time if worker.last_frame_time else float("inf")
            #         print(
            #             f"[{worker.spec.name}] frames={worker.frame_count} age={age:.2f}s error={worker.error}",
            #             flush=True,
            #         )
            #     last_stats = now

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    except KeyboardInterrupt:
        pass
    finally:
        for worker in workers:
            worker.close()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
