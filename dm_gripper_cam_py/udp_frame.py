from __future__ import annotations

import math
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

MAGIC = b"FCP1"
VERSION = 1
DEFAULT_MAX_DATAGRAM = 1200
CODEC_MJPG = 1
CODEC_HEVC = 2
CODEC_BY_NAME = {
    "MJPG": CODEC_MJPG,
    "MJPEG": CODEC_MJPG,
    "HEVC": CODEC_HEVC,
}
NAME_BY_CODEC = {
    CODEC_MJPG: "MJPG",
    CODEC_HEVC: "HEVC",
}

_HEADER = struct.Struct("!4sB16sQdIHHHBB")
HEADER_SIZE = _HEADER.size
FLAG_KEYFRAME = 1


@dataclass(frozen=True)
class FrameChunk:
    session_id: uuid.UUID
    frame_id: int
    timestamp: float
    total_len: int
    chunk_index: int
    chunk_count: int
    codec: str
    flags: int
    payload: bytes


@dataclass(frozen=True)
class CompletedFrame:
    session_id: uuid.UUID
    frame_id: int
    timestamp: float
    codec: str
    flags: int
    payload: bytes


class UdpFrameError(ValueError):
    pass


def normalize_codec(codec: str) -> str:
    name = str(codec).upper()
    if name == "MJPEG":
        name = "MJPG"
    if name not in ("MJPG", "HEVC"):
        raise UdpFrameError(f"unsupported codec: {codec}")
    return name


def _session_uuid(value) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, bytes):
        return uuid.UUID(bytes=value)
    return uuid.UUID(str(value))


def iter_datagrams(
    session_id,
    frame_id: int,
    codec: str,
    payload,
    timestamp: Optional[float] = None,
    max_datagram: int = DEFAULT_MAX_DATAGRAM,
    flags: int = 0,
) -> Iterable[bytes]:
    session = _session_uuid(session_id)
    codec_name = normalize_codec(codec)
    codec_id = CODEC_BY_NAME[codec_name]
    payload_view = memoryview(payload)
    max_payload = int(max_datagram) - HEADER_SIZE
    if max_payload <= 0:
        raise UdpFrameError(f"max_datagram must be greater than header size {HEADER_SIZE}")
    total_len = len(payload_view)
    chunk_count = max(1, int(math.ceil(total_len / float(max_payload))))
    if chunk_count > 0xFFFF:
        raise UdpFrameError(f"frame is too large for max_datagram={max_datagram}: {chunk_count} chunks")
    ts = time.time() if timestamp is None else float(timestamp)
    try:
        for chunk_index in range(chunk_count):
            start = chunk_index * max_payload
            chunk = payload_view[start:start + max_payload]
            header = _HEADER.pack(
                MAGIC,
                VERSION,
                session.bytes,
                int(frame_id),
                ts,
                total_len,
                chunk_index,
                chunk_count,
                len(chunk),
                codec_id,
                int(flags) & 0xFF,
            )
            yield header + bytes(chunk)
    finally:
        payload_view.release()


def parse_datagram(datagram: bytes) -> FrameChunk:
    if len(datagram) < HEADER_SIZE:
        raise UdpFrameError("datagram is shorter than UDP frame header")
    (
        magic,
        version,
        session_bytes,
        frame_id,
        timestamp,
        total_len,
        chunk_index,
        chunk_count,
        chunk_size,
        codec_id,
        flags,
    ) = _HEADER.unpack_from(datagram)
    if magic != MAGIC:
        raise UdpFrameError("invalid UDP frame magic")
    if version != VERSION:
        raise UdpFrameError(f"unsupported UDP frame version: {version}")
    if chunk_count <= 0:
        raise UdpFrameError("chunk_count must be positive")
    if chunk_index >= chunk_count:
        raise UdpFrameError("chunk_index is out of range")
    payload = datagram[HEADER_SIZE:]
    if len(payload) != chunk_size:
        raise UdpFrameError("payload length does not match header chunk_size")
    try:
        codec = NAME_BY_CODEC[codec_id]
    except KeyError as exc:
        raise UdpFrameError(f"unsupported codec id: {codec_id}") from exc
    return FrameChunk(
        session_id=uuid.UUID(bytes=session_bytes),
        frame_id=int(frame_id),
        timestamp=float(timestamp),
        total_len=int(total_len),
        chunk_index=int(chunk_index),
        chunk_count=int(chunk_count),
        codec=codec,
        flags=int(flags),
        payload=payload,
    )


class FrameReassembler:
    def __init__(self, timeout_sec: float = 0.5, max_incomplete_frames: int = 64):
        self.timeout_sec = float(timeout_sec)
        self.max_incomplete_frames = int(max_incomplete_frames)
        self._frames: Dict[Tuple[uuid.UUID, int], dict] = {}

    def push(self, datagram: bytes, now: Optional[float] = None) -> Optional[CompletedFrame]:
        current = time.monotonic() if now is None else float(now)
        self.expire(current)
        chunk = parse_datagram(datagram)
        key = (chunk.session_id, chunk.frame_id)
        state = self._frames.get(key)
        if state is None:
            state = {
                "created": current,
                "timestamp": chunk.timestamp,
                "total_len": chunk.total_len,
                "codec": chunk.codec,
                "flags": chunk.flags,
                "chunks": [None] * chunk.chunk_count,
                "received": 0,
            }
            self._frames[key] = state
        if len(state["chunks"]) != chunk.chunk_count:
            self._frames.pop(key, None)
            raise UdpFrameError("chunk_count changed within a frame")
        if state["total_len"] != chunk.total_len or state["codec"] != chunk.codec:
            self._frames.pop(key, None)
            raise UdpFrameError("frame metadata changed within a frame")
        if state["chunks"][chunk.chunk_index] is None:
            state["chunks"][chunk.chunk_index] = chunk.payload
            state["received"] += 1
        if state["received"] != len(state["chunks"]):
            self._trim_oldest()
            return None
        payload = b"".join(part for part in state["chunks"] if part is not None)
        self._frames.pop(key, None)
        if len(payload) != state["total_len"]:
            raise UdpFrameError("reassembled payload length does not match total_len")
        return CompletedFrame(
            session_id=chunk.session_id,
            frame_id=chunk.frame_id,
            timestamp=state["timestamp"],
            codec=state["codec"],
            flags=state["flags"],
            payload=payload,
        )

    def expire(self, now: Optional[float] = None) -> int:
        current = time.monotonic() if now is None else float(now)
        expired: List[Tuple[uuid.UUID, int]] = []
        for key, state in self._frames.items():
            if current - state["created"] >= self.timeout_sec:
                expired.append(key)
        for key in expired:
            self._frames.pop(key, None)
        return len(expired)

    def _trim_oldest(self):
        while len(self._frames) > self.max_incomplete_frames:
            oldest_key = min(self._frames, key=lambda key: self._frames[key]["created"])
            self._frames.pop(oldest_key, None)
