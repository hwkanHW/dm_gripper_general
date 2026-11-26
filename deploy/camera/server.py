#!/usr/bin/env python3
"""Fish camera gRPC server matching the legacy FCP1/MPEG-TS client.

MJPG transport:
    V4L2 MJPG -> GStreamer appsink -> FCP1 UDP frame chunks

HEVC transport:
    V4L2 HEVC -> h265parse -> MPEG-TS -> UDP

This intentionally does not use RTP.  The companion client expects FCP1 for
MJPG and an MPEG-TS UDP stream for HEVC.
"""
from __future__ import annotations

import argparse
import math
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from concurrent import futures
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import grpc


THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import camera_proxy_pb2  # noqa: E402
import camera_proxy_pb2_grpc  # noqa: E402
from camera_calibration import CameraCalibration, read_calibration  # noqa: E402
from camera_selector import find_camera_for_request, iter_cameras  # noqa: E402


DEFAULT_UDP_PORT = 5004
DEFAULT_MAX_DATAGRAM = 1200
DEFAULT_HEVC_TS_ALIGNMENT = 7  # 7 * 188 = 1316 bytes per MPEG-TS UDP payload.

FCP1_MAGIC = b"FCP1"
FCP1_VERSION = 1
FCP1_CODEC_MJPG = 1
FCP1_FLAG_KEYFRAME = 1
FCP1_HEADER = struct.Struct("!4sB16sQdIHHHBB")


def normalize_codec(codec: str) -> str:
    name = str(codec or "HEVC").upper()
    if name == "MJPEG":
        name = "MJPG"
    if name not in ("MJPG", "HEVC"):
        raise ValueError(f"unsupported codec: {codec}")
    return name


def positive_int(value: int, name: str) -> int:
    result = int(value)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def optional_positive_int(value: int, default: int, name: str) -> int:
    return positive_int(int(value or default), name)


def peer_ip(context) -> str:
    peer = context.peer() or ""
    if peer.startswith("ipv4:"):
        return peer[len("ipv4:"):].rsplit(":", 1)[0]
    if peer.startswith("ipv6:"):
        return peer[len("ipv6:"):].rsplit(":", 1)[0].strip("[]")
    return "127.0.0.1"


def _set_if_present(message, field_name: str, value) -> None:
    """Populate optional/newer protobuf fields without requiring them."""
    if field_name in message.DESCRIPTOR.fields_by_name:
        setattr(message, field_name, value)


def iter_fcp1_mjpg_datagrams(
    session_id: uuid.UUID,
    frame_id: int,
    jpeg_payload: bytes,
    timestamp: Optional[float] = None,
    max_datagram: int = DEFAULT_MAX_DATAGRAM,
) -> Iterable[bytes]:
    """Generate packets byte-for-byte compatible with udp_frame.iter_datagrams."""
    max_payload = int(max_datagram) - FCP1_HEADER.size
    if max_payload <= 0:
        raise ValueError(
            f"max_datagram must be greater than FCP1 header size {FCP1_HEADER.size}"
        )

    payload_view = memoryview(jpeg_payload)
    total_len = len(payload_view)
    chunk_count = max(1, int(math.ceil(total_len / float(max_payload))))
    if chunk_count > 0xFFFF:
        payload_view.release()
        raise ValueError(
            f"JPEG frame is too large for max_datagram={max_datagram}: "
            f"{chunk_count} chunks"
        )

    frame_timestamp = time.time() if timestamp is None else float(timestamp)
    try:
        for chunk_index in range(chunk_count):
            start = chunk_index * max_payload
            chunk = payload_view[start:start + max_payload]
            header = FCP1_HEADER.pack(
                FCP1_MAGIC,
                FCP1_VERSION,
                session_id.bytes,
                int(frame_id),
                frame_timestamp,
                total_len,
                chunk_index,
                chunk_count,
                len(chunk),
                FCP1_CODEC_MJPG,
                FCP1_FLAG_KEYFRAME,
            )
            yield header + bytes(chunk)
    finally:
        payload_view.release()


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 50088
    device: Optional[str] = None
    gst_launch: str = "gst-launch-1.0"
    stat_interval: float = 2.0
    default_udp_port: int = DEFAULT_UDP_PORT
    default_max_datagram: int = DEFAULT_MAX_DATAGRAM


class GStreamerFcp1MjpgSender:
    """Pull native complete JPEG frames from GStreamer and send FCP1 chunks."""

    def __init__(
        self,
        device: str,
        width: int,
        height: int,
        fps: int,
        client_ip: str,
        udp_port: int,
        max_datagram: int,
        session_id: uuid.UUID,
    ):
        self.device = device
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.client_ip = client_ip
        self.udp_port = int(udp_port)
        self.max_datagram = int(max_datagram)
        self.session_id = session_id

        self.Gst = None
        self.pipeline = None
        self.appsink = None
        self.sock: Optional[socket.socket] = None
        self.bus_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.stats_lock = threading.Lock()
        self.frame_id = 0
        self.frames_sent = 0
        self.bytes_sent = 0
        self.error = ""

    @staticmethod
    def _load_gstreamer():
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError) as exc:
            raise RuntimeError(
                "missing Python GStreamer bindings; install python3-gi, "
                "python3-gst-1.0 and gir1.2-gstreamer-1.0, then ensure the "
                "Python interpreter running this server can import gi"
            ) from exc
        return Gst

    def start(self):
        self.stop_event.clear()
        self.error = ""
        Gst = self._load_gstreamer()
        self.Gst = Gst
        Gst.init(None)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)

        pipeline_description = (
            f"v4l2src device={self.device} io-mode=mmap do-timestamp=true "
            f"! image/jpeg,width={self.width},height={self.height},"
            f"framerate={self.fps}/1 "
            "! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 "
            "leaky=downstream "
            "! jpegparse "
            "! appsink name=frames emit-signals=true sync=false "
            "max-buffers=2 drop=true"
        )

        try:
            self.pipeline = Gst.parse_launch(pipeline_description)
            self.appsink = self.pipeline.get_by_name("frames")
            if self.appsink is None:
                raise RuntimeError("GStreamer pipeline has no appsink named frames")
            self.appsink.connect("new-sample", self._on_new_sample)

            state_result = self.pipeline.set_state(Gst.State.PLAYING)
            if state_result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("failed to put MJPG GStreamer pipeline in PLAYING")

            self.bus_thread = threading.Thread(
                target=self._monitor_bus,
                name="mjpg-fcp1-gstreamer-bus",
                daemon=True,
            )
            self.bus_thread.start()
        except Exception:
            self.stop()
            raise

        print(
            f"[fcp1] started device={self.device} "
            f"{self.width}x{self.height}@{self.fps} "
            f"dst={self.client_ip}:{self.udp_port} "
            f"max_datagram={self.max_datagram}",
            flush=True,
        )

    def _on_new_sample(self, appsink):
        Gst = self.Gst
        if Gst is None:
            return None
        if self.stop_event.is_set() or self.sock is None:
            return Gst.FlowReturn.EOS

        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS

        buffer = sample.get_buffer()
        mapped, map_info = buffer.map(Gst.MapFlags.READ)
        if not mapped:
            self.error = "failed to map MJPG GStreamer buffer"
            return Gst.FlowReturn.ERROR

        try:
            jpeg_payload = bytes(map_info.data)
            if len(jpeg_payload) < 4 or not jpeg_payload.startswith(b"\xff\xd8"):
                raise RuntimeError(
                    "camera buffer is not a JPEG frame: "
                    f"first16={jpeg_payload[:16].hex()}"
                )

            with self.stats_lock:
                next_frame_id = self.frame_id + 1

            sent_bytes = 0
            for datagram in iter_fcp1_mjpg_datagrams(
                session_id=self.session_id,
                frame_id=next_frame_id,
                jpeg_payload=jpeg_payload,
                timestamp=time.time(),
                max_datagram=self.max_datagram,
            ):
                self.sock.sendto(datagram, (self.client_ip, self.udp_port))
                sent_bytes += len(datagram)

            with self.stats_lock:
                self.frame_id = next_frame_id
                self.frames_sent += 1
                self.bytes_sent += sent_bytes
        except Exception as exc:
            self.error = str(exc)
            print(f"[fcp1] send failed: {exc}", flush=True)
            return Gst.FlowReturn.ERROR
        finally:
            buffer.unmap(map_info)

        return Gst.FlowReturn.OK

    def _monitor_bus(self):
        Gst = self.Gst
        pipeline = self.pipeline
        if Gst is None or pipeline is None:
            return
        bus = pipeline.get_bus()
        while not self.stop_event.is_set():
            message = bus.timed_pop_filtered(
                200 * Gst.MSECOND,
                Gst.MessageType.ERROR | Gst.MessageType.EOS,
            )
            if message is None:
                continue
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                self.error = str(error)
                if debug:
                    self.error += f": {debug}"
            else:
                self.error = "MJPG GStreamer pipeline reached EOS"
            print(f"[fcp1] pipeline stopped: {self.error}", flush=True)
            return

    def stats_snapshot(self) -> Tuple[int, int]:
        with self.stats_lock:
            return self.frames_sent, self.bytes_sent

    def check_alive(self):
        if self.pipeline is None:
            raise RuntimeError("FCP1 MJPG pipeline is not started")
        if self.error:
            raise RuntimeError(self.error)

    def stop(self):
        self.stop_event.set()
        if self.pipeline is not None and self.Gst is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        if self.bus_thread is not None and self.bus_thread.is_alive():
            if self.bus_thread is not threading.current_thread():
                self.bus_thread.join(timeout=1.0)
        self.pipeline = None
        self.appsink = None
        self.sock = None
        self.bus_thread = None


class GStreamerHevcMpegTsSender:
    """Send camera-native HEVC inside MPEG-TS, as expected by the client."""

    def __init__(
        self,
        gst_launch: str,
        device: str,
        width: int,
        height: int,
        fps: int,
        client_ip: str,
        udp_port: int,
    ):
        self.gst_launch = gst_launch
        self.device = device
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.client_ip = client_ip
        self.udp_port = int(udp_port)
        self.proc: Optional[subprocess.Popen] = None
        self.stderr_thread: Optional[threading.Thread] = None
        self.stderr_tail = bytearray()

    def build_cmd(self):
        return [
            self.gst_launch,
            "-q",
            "v4l2src",
            f"device={self.device}",
            "io-mode=mmap",
            "do-timestamp=true",
            "!",
            f"video/x-h265,width={self.width},height={self.height},framerate={self.fps}/1",
            "!",
            "queue",
            "max-size-buffers=3",
            "max-size-time=0",
            "max-size-bytes=0",
            "leaky=no",
            "!",
            "h265parse",
            "config-interval=-1",
            "disable-passthrough=true",
            "!",
            "video/x-h265,stream-format=byte-stream,alignment=au",
            "!",
            "mpegtsmux",
            f"alignment={DEFAULT_HEVC_TS_ALIGNMENT}",
            "!",
            "udpsink",
            f"host={self.client_ip}",
            f"port={self.udp_port}",
            "sync=false",
            "async=false",
            "buffer-size=4194304",
        ]

    def start(self):
        self.proc = subprocess.Popen(
            self.build_cmd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name="hevc-mpegts-stderr",
            daemon=True,
        )
        self.stderr_thread.start()
        print(
            f"[mpegts] started device={self.device} "
            f"{self.width}x{self.height}@{self.fps} "
            f"dst={self.client_ip}:{self.udp_port}",
            flush=True,
        )

    def _drain_stderr(self):
        try:
            if self.proc is None or self.proc.stderr is None:
                return
            while True:
                chunk = self.proc.stderr.read(4096)
                if not chunk:
                    return
                self.stderr_tail.extend(chunk)
                if len(self.stderr_tail) > 32768:
                    del self.stderr_tail[:-32768]
        except Exception:
            pass

    def stderr_text(self) -> str:
        return self.stderr_tail.decode("utf-8", errors="replace").strip()

    def stats_snapshot(self) -> Tuple[int, int]:
        # gst-launch does not expose per-frame counters to this process.
        return 0, 0

    def check_alive(self):
        if self.proc is None:
            raise RuntimeError("HEVC MPEG-TS pipeline is not started")
        return_code = self.proc.poll()
        if return_code is not None:
            detail = self.stderr_text() or (
                f"HEVC MPEG-TS pipeline exited with status {return_code}"
            )
            raise RuntimeError(detail)

    def stop(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2.0)
        except Exception:
            try:
                self.proc.kill()
                self.proc.wait(timeout=2.0)
            except Exception:
                pass
        if self.stderr_thread is not None and self.stderr_thread.is_alive():
            self.stderr_thread.join(timeout=1.0)
        self.proc = None
        self.stderr_thread = None


class CameraSession:
    def __init__(self, config: ServerConfig, request, client_ip: str):
        self.config = config
        self.request = request
        self.client_ip = client_ip
        self.session_id = uuid.uuid4()
        self.stop_event = threading.Event()
        self.device: Optional[str] = None
        self.codec = normalize_codec(request.codec or "HEVC")
        self.width = positive_int(request.width, "width")
        self.height = positive_int(request.height, "height")
        self.fps = positive_int(request.fps, "fps")
        self.udp_port = optional_positive_int(
            request.udp_port,
            config.default_udp_port,
            "udp_port",
        )
        self.max_datagram = optional_positive_int(
            request.max_datagram,
            config.default_max_datagram,
            "max_datagram",
        )
        self.frames_sent = 0
        self.bytes_sent = 0
        self.started_at = 0.0
        self.sender = None

    @property
    def actual_width(self) -> int:
        return self.width

    @property
    def actual_height(self) -> int:
        return self.height

    @property
    def actual_fps(self) -> int:
        return self.fps

    def open(self):
        preferred_device = self.request.device or self.config.device
        self.device = find_camera_for_request(
            self.codec,
            self.width,
            self.height,
            self.fps,
            preferred_device=preferred_device,
        )

        if self.codec == "MJPG":
            self.sender = GStreamerFcp1MjpgSender(
                device=self.device,
                width=self.width,
                height=self.height,
                fps=self.fps,
                client_ip=self.client_ip,
                udp_port=self.udp_port,
                max_datagram=self.max_datagram,
                session_id=self.session_id,
            )
        else:
            self.sender = GStreamerHevcMpegTsSender(
                gst_launch=self.config.gst_launch,
                device=self.device,
                width=self.width,
                height=self.height,
                fps=self.fps,
                client_ip=self.client_ip,
                udp_port=self.udp_port,
            )

        self.sender.start()
        self.started_at = time.time()
        print(
            f"[session] opened id={self.session_id} codec={self.codec} "
            f"{self.actual_width}x{self.actual_height}@{self.actual_fps} "
            f"device={self.device} udp={self.client_ip}:{self.udp_port}",
            flush=True,
        )

    def close(self):
        self.stop_event.set()
        if self.sender is not None:
            self.sender.stop()
        self.sender = None
        print(f"[session] closed id={self.session_id}", flush=True)

    def _refresh_stats(self):
        if self.sender is not None:
            self.frames_sent, self.bytes_sent = self.sender.stats_snapshot()

    def event(self, event_type, message: str = ""):
        self._refresh_stats()
        elapsed = max(0.0, time.time() - self.started_at) if self.started_at else 0.0
        event = camera_proxy_pb2.StreamEvent(
            type=event_type,
            session_id=str(self.session_id),
            message=message,
            codec=self.codec,
            width=self.actual_width,
            height=self.actual_height,
            fps=self.actual_fps,
            device=self.device or "",
            frames_sent=self.frames_sent,
            bytes_sent=self.bytes_sent,
            elapsed_sec=elapsed,
        )
        # These fields are not needed by the legacy client, but filling them when
        # present keeps newer protobuf stubs able to display the UDP destination.
        _set_if_present(event, "rtp_port", self.udp_port)
        _set_if_present(event, "rtcp_port", 0)
        _set_if_present(event, "rtp_payload_type", 0)
        _set_if_present(event, "rtp_clock_rate", 0)
        return event

    def stream_until_stopped(self, context):
        if self.sender is None:
            raise RuntimeError("media sender is not open")
        last_stats = time.monotonic()
        while context.is_active() and not self.stop_event.is_set():
            self.sender.check_alive()
            now = time.monotonic()
            if now - last_stats >= self.config.stat_interval:
                last_stats = now
                yield self.event(camera_proxy_pb2.StreamEvent.STATS)
            else:
                time.sleep(0.05)


class CameraProxy(camera_proxy_pb2_grpc.CameraProxyServicer):
    def __init__(self, config: ServerConfig):
        self.config = config
        self.lock = threading.Lock()
        self.calibration_condition = threading.Condition(self.lock)
        self.active_session: Optional[CameraSession] = None
        self.calibration_read_active = False
        self.calibration_read_device: Optional[str] = None
        self.calibration_cache: Dict[str, CameraCalibration] = {}

    def _resolve_calibration_device(self, requested_device: str) -> str:
        if requested_device:
            return requested_device
        if self.config.device:
            return self.config.device
        for camera in iter_cameras():
            return camera.device
        raise LookupError("no eligible camera found")

    def _read_calibration_for_request(self, request, context):
        try:
            device = self._resolve_calibration_device(request.device)
        except LookupError as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))

        with self.calibration_condition:
            while True:
                cached = self.calibration_cache.get(device)
                if cached is not None:
                    return cached
                if not self.calibration_read_active:
                    self.calibration_read_active = True
                    self.calibration_read_device = device
                    break
                self.calibration_condition.wait()

        try:
            calibration = read_calibration(device)
        except LookupError as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except Exception as exc:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
        else:
            with self.calibration_condition:
                self.calibration_cache[device] = calibration
            return calibration
        finally:
            with self.calibration_condition:
                if self.calibration_read_device == device:
                    self.calibration_read_active = False
                    self.calibration_read_device = None
                    self.calibration_condition.notify_all()

    def ListCapabilities(self, request, context):
        response = camera_proxy_pb2.CapabilityResponse()
        for camera in iter_cameras():
            if request.device and camera.device != request.device:
                continue
            item = response.cameras.add(device=camera.device)
            for codec in ("MJPG", "HEVC"):
                camera_format = camera.get_format(codec)
                if camera_format is None:
                    continue
                codec_item = item.codecs.add(codec=codec)
                for size in camera_format.frame_sizes:
                    mode = codec_item.modes.add(width=size.width, height=size.height)
                    mode.fps.extend(size.fps_options)
        return response

    def GetIntrinsics(self, request, context):
        calibration = self._read_calibration_for_request(request, context)
        return camera_proxy_pb2.IntrinsicsResponse(
            device=calibration.device,
            camera_model=calibration.camera_model,
            distortion_model=calibration.distortion_model,
            intrinsics=calibration.intrinsics,
            camera_matrix=calibration.camera_matrix,
            distortion_coeffs=calibration.distortion_coeffs,
            resolution=calibration.resolution,
            sn=calibration.sn,
            sn_valid=calibration.sn_valid,
            camera_model_enum=calibration.camera_model_enum,
        )

    def GetSN(self, request, context):
        calibration = self._read_calibration_for_request(request, context)
        return camera_proxy_pb2.SNResponse(
            device=calibration.device,
            sn=calibration.sn,
            valid=calibration.sn_valid,
        )

    def OpenStream(self, request, context):
        session = CameraSession(
            self.config,
            request,
            request.client_ip or peer_ip(context),
        )
        with self.lock:
            if self.active_session is not None:
                active_id = getattr(self.active_session, "session_id", "unknown")
                context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    f"camera is busy: active session {active_id}",
                )
            if self.calibration_read_active:
                context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    "camera calibration read is in progress",
                )
            self.active_session = session

        try:
            session.open()
            yield session.event(camera_proxy_pb2.StreamEvent.STARTED, "stream started")
            for event in session.stream_until_stopped(context):
                yield event
            yield session.event(camera_proxy_pb2.StreamEvent.STOPPED, "stream stopped")
        except Exception as exc:
            print(f"[session] failed id={session.session_id}: {exc}", flush=True)
            yield session.event(camera_proxy_pb2.StreamEvent.ERROR, str(exc))
        finally:
            session.close()
            with self.lock:
                if self.active_session is session:
                    self.active_session = None

    def StopStream(self, request, context):
        with self.lock:
            session = self.active_session
            if session is None:
                return camera_proxy_pb2.StopStreamResponse(
                    stopped=False,
                    message="no active session",
                )
            if request.session_id and request.session_id != str(session.session_id):
                return camera_proxy_pb2.StopStreamResponse(
                    stopped=False,
                    message="session id does not match active session",
                )
            session.stop_event.set()
            return camera_proxy_pb2.StopStreamResponse(
                stopped=True,
                message=f"stopping session {session.session_id}",
            )


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="gRPC camera proxy using FCP1 MJPG and MPEG-TS HEVC"
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=50088)
    parser.add_argument("--device", default=None)
    parser.add_argument("--gst-launch", default="gst-launch-1.0")
    parser.add_argument("--stat-interval", type=float, default=2.0)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument(
        "--udp-port",
        "--rtp-port",
        dest="udp_port",
        type=int,
        default=DEFAULT_UDP_PORT,
        help="default client UDP destination port",
    )
    parser.add_argument(
        "--max-datagram",
        "--rtp-mtu",
        dest="max_datagram",
        type=int,
        default=DEFAULT_MAX_DATAGRAM,
        help="maximum FCP1 MJPG datagram size",
    )
    # Accepted as no-ops so existing service command lines do not fail.
    parser.add_argument("--rtcp-port", type=int, default=5005, help=argparse.SUPPRESS)
    parser.add_argument("--disable-rtcp", action="store_true", help=argparse.SUPPRESS)
    return parser


def config_from_args(args) -> ServerConfig:
    return ServerConfig(
        host=args.host,
        port=positive_int(args.port, "port"),
        device=args.device,
        gst_launch=args.gst_launch,
        stat_interval=float(args.stat_interval),
        default_udp_port=positive_int(args.udp_port, "udp_port"),
        default_max_datagram=positive_int(args.max_datagram, "max_datagram"),
    )


def main():
    args = build_argparser().parse_args()
    config = config_from_args(args)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=args.max_workers))
    camera_proxy_pb2_grpc.add_CameraProxyServicer_to_server(
        CameraProxy(config),
        server,
    )
    bind_address = f"{config.host}:{config.port}"
    if server.add_insecure_port(bind_address) == 0:
        raise RuntimeError(f"failed to bind gRPC server to {bind_address}")
    server.start()

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    print(f"[grpc] listening on {bind_address}", flush=True)
    try:
        while not stop_event.wait(1.0):
            pass
    finally:
        print("[grpc] stopping", flush=True)
        server.stop(grace=2.0).wait()


if __name__ == "__main__":
    main()
