from __future__ import annotations

import subprocess
import threading
import time
from typing import Optional, Tuple

import numpy as np


class HevcMpegTsUdpCapture:
    def __init__(
        self,
        udp_port: int,
        width: int,
        height: int,
        fps: int,
        ffmpeg_bin: str = "ffmpeg",
    ):
        self.udp_port = int(udp_port)
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.ffmpeg_bin = ffmpeg_bin
        self.frame_size = self.width * self.height * 3
        self.proc: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.stderr_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.frame_ready = threading.Condition()
        self.frame_lock = threading.Lock()
        self.stderr_tail = bytearray()
        self.latest_frame: Optional[np.ndarray] = None
        self.frame_id = 0
        self.last_read_id = 0
        self.frames_decoded = 0
        self.frames_returned = 0
        self.frames_dropped = 0
        self.opened = False

    def build_cmd(self):
        return [
            self.ffmpeg_bin,
            "-hide_banner",
            "-loglevel", "warning",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-analyzeduration", "0",
            "-probesize", "32",
            "-fpsprobesize", "0",
            "-threads", "1",
            "-f", "mpegts",
            "-i", f"udp://@0.0.0.0:{self.udp_port}?fifo_size=1000000&overrun_nonfatal=1",
            "-an",
            "-sn",
            "-dn",
            "-pix_fmt", "bgr24",
            "-f", "rawvideo",
            "pipe:1",
        ]

    def open(self):
        if self.opened:
            return True
        self.stop_event.clear()
        self.latest_frame = None
        self.frame_id = 0
        self.last_read_id = 0
        self.frames_decoded = 0
        self.frames_returned = 0
        self.frames_dropped = 0
        self.stderr_tail = bytearray()
        self.proc = subprocess.Popen(
            self.build_cmd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        self.stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self.stderr_thread.start()
        self.opened = True
        return True

    def is_opened(self) -> bool:
        return self.opened and self.proc is not None and self.proc.poll() is None

    def _stderr_loop(self):
        try:
            if self.proc is None or self.proc.stderr is None:
                return
            while not self.stop_event.is_set():
                chunk = self.proc.stderr.read(4096)
                if not chunk:
                    break
                self.stderr_tail.extend(chunk)
                if len(self.stderr_tail) > 32768:
                    del self.stderr_tail[:-32768]
        except Exception:
            pass

    def stderr_text(self) -> str:
        return self.stderr_tail.decode("utf-8", errors="replace").strip()

    def _alloc_frame_buffer(self) -> np.ndarray:
        return np.empty((self.height, self.width, 3), dtype=np.uint8)

    def _read_exact_into(self, frame: np.ndarray) -> bool:
        if self.proc is None or self.proc.stdout is None:
            return False
        view = memoryview(frame).cast("B")
        offset = 0
        while offset < len(view) and not self.stop_event.is_set():
            if self.proc.poll() is not None:
                return False
            nread = self.proc.stdout.readinto(view[offset:])
            if not nread:
                return False
            offset += nread
        return offset == len(view)

    def _reader_loop(self):
        warmup_remaining = max(2, min(8, int(round(self.fps * 0.1))))
        scratch = self._alloc_frame_buffer()
        try:
            while not self.stop_event.is_set():
                if not self._read_exact_into(scratch):
                    break
                if warmup_remaining > 0:
                    warmup_remaining -= 1
                    continue
                with self.frame_lock:
                    previous = self.latest_frame
                    if previous is not None:
                        self.frames_dropped += 1
                    self.latest_frame = scratch
                    self.frame_id += 1
                    self.frames_decoded += 1
                if previous is not None and previous.shape == scratch.shape and previous.dtype == scratch.dtype:
                    scratch = previous
                else:
                    scratch = self._alloc_frame_buffer()
                with self.frame_ready:
                    self.frame_ready.notify_all()
        finally:
            self.opened = False
            with self.frame_ready:
                self.frame_ready.notify_all()

    def read(self, timeout: Optional[float] = None) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.is_opened() and not self.opened:
            self.open()
        deadline = None if timeout is None else time.time() + timeout
        while True:
            with self.frame_lock:
                if self.latest_frame is not None and self.frame_id != self.last_read_id:
                    self.last_read_id = self.frame_id
                    self.frames_returned += 1
                    return True, self.latest_frame.copy()
            if self.proc is not None and self.proc.poll() is not None:
                stderr_text = self.stderr_text()
                if stderr_text:
                    raise RuntimeError(f"ffmpeg exited while decoding HEVC MPEG-TS UDP: {stderr_text}")
                return False, None
            if not self.is_opened():
                return False, None
            with self.frame_ready:
                remaining = None if deadline is None else max(0.0, deadline - time.time())
                if remaining == 0.0:
                    return False, None
                self.frame_ready.wait(timeout=remaining)

    def close(self):
        self.stop_event.set()
        with self.frame_ready:
            self.frame_ready.notify_all()
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=2.0)
                except Exception:
                    pass
        if self.reader_thread is not None and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)
        if self.stderr_thread is not None and self.stderr_thread.is_alive():
            self.stderr_thread.join(timeout=1.0)
        self.proc = None
        self.reader_thread = None
        self.stderr_thread = None
        self.opened = False
