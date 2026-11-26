#!/usr/bin/env python3
from __future__ import annotations

import socket
import os
import threading
import time
from typing import Optional, Tuple

import grpc
import numpy as np

try:
    from . import camera_proxy_pb2, camera_proxy_pb2_grpc
    from .hevc_ffmpeg_decoder import HevcMpegTsUdpCapture
    from .udp_frame import DEFAULT_MAX_DATAGRAM, FrameReassembler, UdpFrameError, normalize_codec
except ImportError:
    if __package__:
        raise
    import camera_proxy_pb2
    import camera_proxy_pb2_grpc
    from hevc_ffmpeg_decoder import HevcMpegTsUdpCapture
    from udp_frame import DEFAULT_MAX_DATAGRAM, FrameReassembler, UdpFrameError, normalize_codec

try:
    import cv2
except ImportError:
    cv2 = None


class RemoteCameraCapture:
    def __init__(
        self,
        host: str,
        port: int = 50088,
        codec: str = "MJPG",
        width: int = 1920,
        height: int = 1080,
        fps: int = 60,
        window_name: str = "Remote Camera",
        ffmpeg_bin: str = "ffmpeg",
        udp_port: int = 0,
        bind_host: str = "0.0.0.0",
        client_ip: str = "",
        device: str = "",
        max_datagram: int = DEFAULT_MAX_DATAGRAM,
        reassembly_timeout: float = 0.5,
    ):
        self.host = host
        self.port = int(port)
        self.codec = normalize_codec(codec)
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.window_name = window_name
        self.ffmpeg_bin = ffmpeg_bin
        self.udp_port = int(udp_port)
        self.bind_host = bind_host
        self.client_ip = client_ip
        self.device = device
        self.max_datagram = int(max_datagram)
        self.reassembly_timeout = float(reassembly_timeout)

        self.channel: Optional[grpc.Channel] = None
        self.stub: Optional[camera_proxy_pb2_grpc.CameraProxyStub] = None
        self.call = None
        self.event_thread: Optional[threading.Thread] = None
        self.receiver_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.frame_ready = threading.Condition()
        self.frame_lock = threading.Lock()

        self.udp_sock: Optional[socket.socket] = None
        self.hevc_capture: Optional[HevcMpegTsUdpCapture] = None
        self.latest_frame: Optional[np.ndarray] = None
        self.session_id: Optional[str] = None
        self.opened = False
        self.start_time = 0.0
        self.last_frame_time = 0.0
        self.last_error: Optional[str] = None
        self.server_message: Optional[str] = None
        self.negotiated_device = ""

        self.frame_id = 0
        self.last_read_id = 0
        self.frames_decoded = 0
        self.frames_returned = 0
        self.frames_dropped = 0
        self.bytes_received = 0
        self.server_frames_sent = 0
        self.server_bytes_sent = 0

    @classmethod
    def list_capabilities(cls, host: str, port: int = 50088, device: str = ""):
        cls._ensure_direct_grpc_target_for_host(host)
        with grpc.insecure_channel(f"{host}:{port}", options=(("grpc.enable_http_proxy", 0),)) as channel:
            stub = camera_proxy_pb2_grpc.CameraProxyStub(channel)
            return stub.ListCapabilities(camera_proxy_pb2.CapabilityRequest(device=device or ""))

    @staticmethod
    def _format_intrinsics_response(response):
        matrix = list(response.camera_matrix)
        if len(matrix) == 9:
            camera_matrix = [matrix[0:3], matrix[3:6], matrix[6:9]]
        else:
            camera_matrix = []
        return {
            "intrinsics": list(response.intrinsics),
            "camera_matrix": camera_matrix,
            "distortion_coeffs": list(response.distortion_coeffs),
        }

    def _calibration_device(self) -> str:
        return self.device or self.negotiated_device or ""

    def get_intrinsics(self, timeout: Optional[float] = None):
        self._ensure_direct_grpc_target()
        with grpc.insecure_channel(f"{self.host}:{self.port}", options=(("grpc.enable_http_proxy", 0),)) as channel:
            stub = camera_proxy_pb2_grpc.CameraProxyStub(channel)
            response = stub.GetIntrinsics(camera_proxy_pb2.CalibrationRequest(device=self._calibration_device()), timeout=timeout)
            return self._format_intrinsics_response(response)

    def get_sn(self, timeout: Optional[float] = None) -> str:
        self._ensure_direct_grpc_target()
        with grpc.insecure_channel(f"{self.host}:{self.port}", options=(("grpc.enable_http_proxy", 0),)) as channel:
            stub = camera_proxy_pb2_grpc.CameraProxyStub(channel)
            response = stub.GetSN(camera_proxy_pb2.CalibrationRequest(device=self._calibration_device()), timeout=timeout)
            return response.sn

    def get_SN(self, timeout: Optional[float] = None) -> str:
        return self.get_sn(timeout=timeout)

    @staticmethod
    def _ensure_direct_grpc_target_for_host(host: str):
        for key in ("no_proxy", "NO_PROXY"):
            current = os.environ.get(key, "")
            hosts = [item.strip() for item in current.split(",") if item.strip()]
            if host not in hosts:
                hosts.append(host)
            os.environ[key] = ",".join(hosts)

    def _pick_udp_port(self) -> int:
        if self.udp_port:
            return self.udp_port
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.bind_host, 0))
            return int(sock.getsockname()[1])
        finally:
            sock.close()

    def _build_request(self, udp_port: int):
        return camera_proxy_pb2.StreamRequest(
            codec=self.codec,
            width=self.width,
            height=self.height,
            fps=self.fps,
            udp_port=udp_port,
            client_ip=self.client_ip or "",
            device=self.device or "",
            max_datagram=self.max_datagram,
        )

    def _ensure_direct_grpc_target(self):
        self._ensure_direct_grpc_target_for_host(self.host)

    def _apply_event(self, event):
        self.session_id = event.session_id or self.session_id
        self.server_message = event.message or self.server_message
        if event.width:
            self.width = int(event.width)
        if event.height:
            self.height = int(event.height)
        if event.fps:
            self.fps = int(event.fps)
        if event.device:
            self.negotiated_device = event.device
        self.server_frames_sent = int(event.frames_sent)
        self.server_bytes_sent = int(event.bytes_sent)

    def _start_control_stream(self, udp_port: int):
        assert self.stub is not None
        self.call = self.stub.OpenStream(self._build_request(udp_port))
        first_event = next(self.call)
        self._apply_event(first_event)
        if first_event.type == camera_proxy_pb2.StreamEvent.ERROR:
            raise RuntimeError(first_event.message)
        if first_event.type != camera_proxy_pb2.StreamEvent.STARTED:
            raise RuntimeError(f"unexpected first stream event: {first_event.type}")
        if (
            self.codec == "HEVC"
            and self.hevc_capture is not None
            and (self.hevc_capture.width != self.width or self.hevc_capture.height != self.height or self.hevc_capture.fps != self.fps)
        ):
            self.hevc_capture.close()
            self.hevc_capture = HevcMpegTsUdpCapture(
                udp_port=udp_port,
                width=self.width,
                height=self.height,
                fps=self.fps,
                ffmpeg_bin=self.ffmpeg_bin,
            )
            self.hevc_capture.open()
        self.event_thread = threading.Thread(target=self._event_loop, daemon=True)
        self.event_thread.start()

    def _event_loop(self):
        try:
            for event in self.call:
                self._apply_event(event)
                if event.type == camera_proxy_pb2.StreamEvent.ERROR:
                    self.last_error = event.message
                    self.stop_event.set()
                    break
                if event.type == camera_proxy_pb2.StreamEvent.STOPPED:
                    self.stop_event.set()
                    break
        except grpc.RpcError as exc:
            if not self.stop_event.is_set():
                self.last_error = f"{exc.code().name}: {exc.details()}"
                self.stop_event.set()
        finally:
            self.opened = False
            with self.frame_ready:
                self.frame_ready.notify_all()

    def open(self) -> bool:
        if self.opened:
            return True
        self.stop_event.clear()
        self.latest_frame = None
        self.session_id = None
        self.last_error = None
        self.server_message = None
        self.frame_id = 0
        self.last_read_id = 0
        self.frames_decoded = 0
        self.frames_returned = 0
        self.frames_dropped = 0
        self.bytes_received = 0
        self.start_time = time.time()
        udp_port = self._pick_udp_port()

        self._ensure_direct_grpc_target()
        self.channel = grpc.insecure_channel(
            f"{self.host}:{self.port}",
            options=(("grpc.enable_http_proxy", 0),),
        )
        self.stub = camera_proxy_pb2_grpc.CameraProxyStub(self.channel)

        if self.codec == "HEVC":
            self.hevc_capture = HevcMpegTsUdpCapture(
                udp_port=udp_port,
                width=self.width,
                height=self.height,
                fps=self.fps,
                ffmpeg_bin=self.ffmpeg_bin,
            )
            self.hevc_capture.open()
        else:
            if cv2 is None:
                raise RuntimeError("opencv-python is required for MJPG decoding")
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.bind((self.bind_host, udp_port))
            self.udp_sock.settimeout(0.2)

        try:
            self._start_control_stream(udp_port)
            if self.codec == "MJPG":
                self.receiver_thread = threading.Thread(target=self._mjpg_receiver_loop, daemon=True)
                self.receiver_thread.start()
            self.opened = True
            return True
        except Exception:
            self.release()
            raise

    def isOpened(self) -> bool:
        if self.codec == "HEVC":
            return self.opened and self.hevc_capture is not None and self.hevc_capture.is_opened()
        return self.opened and not self.stop_event.is_set()

    def _publish_frame(self, frame: np.ndarray, payload_size: int = 0):
        with self.frame_lock:
            previous = self.latest_frame
            if previous is not None:
                self.frames_dropped += 1
            self.latest_frame = frame
            self.frame_id += 1
            self.frames_decoded += 1
            self.bytes_received += int(payload_size)
            self.last_frame_time = time.time()
        with self.frame_ready:
            self.frame_ready.notify_all()

    def _mjpg_receiver_loop(self):
        assert self.udp_sock is not None
        reassembler = FrameReassembler(timeout_sec=self.reassembly_timeout)
        try:
            while not self.stop_event.is_set():
                try:
                    datagram, _ = self.udp_sock.recvfrom(self.max_datagram + 512)
                except socket.timeout:
                    reassembler.expire()
                    continue
                except OSError:
                    break
                try:
                    completed = reassembler.push(datagram)
                except UdpFrameError as exc:
                    self.last_error = str(exc)
                    continue
                if completed is None:
                    continue
                if self.session_id and str(completed.session_id) != self.session_id:
                    continue
                array = np.frombuffer(completed.payload, dtype=np.uint8)
                frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
                if frame is None:
                    self.last_error = "cv2.imdecode returned None for MJPG frame"
                    continue
                self._publish_frame(frame, payload_size=len(completed.payload))
        finally:
            self.opened = False
            with self.frame_ready:
                self.frame_ready.notify_all()

    def read(self, timeout: Optional[float] = None) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.isOpened() and not self.opened:
            self.open()
        if self.codec == "HEVC":
            if self.hevc_capture is None:
                return False, None
            ok, frame = self.hevc_capture.read(timeout=timeout)
            if ok:
                self.frames_decoded = self.hevc_capture.frames_decoded
                self.frames_returned = self.hevc_capture.frames_returned
                self.frames_dropped = self.hevc_capture.frames_dropped
                self.frame_id = self.hevc_capture.frame_id
                self.last_frame_time = time.time()
            return ok, frame

        deadline = None if timeout is None else time.time() + timeout
        while True:
            with self.frame_lock:
                if self.latest_frame is not None and self.frame_id != self.last_read_id:
                    self.last_read_id = self.frame_id
                    self.frames_returned += 1
                    return True, self.latest_frame.copy()
            if not self.isOpened():
                return False, None
            with self.frame_ready:
                remaining = None if deadline is None else max(0.0, deadline - time.time())
                if remaining == 0.0:
                    return False, None
                self.frame_ready.wait(timeout=remaining)


    def get_latest_frame(self) -> Optional[np.ndarray]:
        if self.codec == "HEVC" and self.hevc_capture is not None:
            with self.hevc_capture.frame_lock:
                if self.hevc_capture.latest_frame is None:
                    return None
                return self.hevc_capture.latest_frame.copy()
        with self.frame_lock:
            if self.latest_frame is None:
                return None
            return self.latest_frame.copy()

    def get(self, name: str):
        key = name.lower()
        if key == "width":
            return self.width
        if key == "height":
            return self.height
        if key == "fps":
            return self.fps
        if key == "codec":
            return self.codec
        if key == "frame_id":
            return self.frame_id
        if key == "uptime":
            return time.time() - self.start_time if self.start_time else 0.0
        if key == "last_frame_age":
            return time.time() - self.last_frame_time if self.last_frame_time else float("inf")
        if key == "decoded_frames":
            return self.frames_decoded
        if key == "returned_frames":
            return self.frames_returned
        if key == "dropped_frames":
            return self.frames_dropped
        if key == "drop_ratio":
            return (self.frames_dropped / self.frames_decoded) if self.frames_decoded else 0.0
        if key == "session_id":
            return self.session_id
        if key == "device":
            return self.negotiated_device
        if key == "error":
            return self.last_error
        if key == "server_message":
            return self.server_message
        if key == "server_frames_sent":
            return self.server_frames_sent
        if key == "server_bytes_sent":
            return self.server_bytes_sent
        if key == "udp_port":
            return self.udp_port
        return None


    def release(self):
        self.stop_event.set()
        with self.frame_ready:
            self.frame_ready.notify_all()
        if self.stub is not None and self.session_id:
            try:
                self.stub.StopStream(camera_proxy_pb2.StopStreamRequest(session_id=self.session_id), timeout=2.0)
            except KeyboardInterrupt:
                pass
            except Exception:
                pass
        if self.call is not None:
            try:
                self.call.cancel()
            except Exception:
                pass
        if self.udp_sock is not None:
            try:
                self.udp_sock.close()
            except Exception:
                pass
        self.udp_sock = None
        if self.hevc_capture is not None:
            self.hevc_capture.close()
        self.hevc_capture = None
        if self.receiver_thread is not None and self.receiver_thread.is_alive():
            self.receiver_thread.join(timeout=1.0)
        if self.event_thread is not None and self.event_thread.is_alive():
            self.event_thread.join(timeout=1.0)
        if self.channel is not None:
            try:
                self.channel.close()
            except Exception:
                pass
        self.channel = None
        self.stub = None
        self.call = None
        self.receiver_thread = None
        self.event_thread = None
        self.opened = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
