"""HTTP MJPEG service and background camera capture."""
from __future__ import annotations

import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse

from ..utils import clamp

try:
    import cv2
    from ..camera import RemoteCameraCapture
except ImportError:  # pragma: no cover
    cv2 = None
    RemoteCameraCapture = None


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


