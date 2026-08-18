#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time

import grpc

from remote_camera import RemoteCameraCapture
from udp_frame import DEFAULT_MAX_DATAGRAM


def print_capabilities(response):
    for camera in response.cameras:
        print(camera.device)
        for codec in camera.codecs:
            print(f"  {codec.codec}:")
            for mode in codec.modes:
                fps_text = "/".join(str(v) for v in mode.fps)
                print(f"    - {mode.width}x{mode.height} @ {fps_text} fps")


def print_intrinsics(data):
    print(f"intrinsics={data['intrinsics']}")
    print(f"camera_matrix={data['camera_matrix']}")
    print(f"distortion_coeffs={data['distortion_coeffs']}")


def build_capture(args) -> RemoteCameraCapture:
    return RemoteCameraCapture(
        host=args.host,
        port=args.port,
        codec=args.codec,
        width=args.width,
        height=args.height,
        fps=args.fps,
        ffmpeg_bin=args.ffmpeg_bin,
        udp_port=args.udp_port,
        bind_host=args.bind_host,
        client_ip=args.client_ip,
        device=args.device,
        max_datagram=args.max_datagram,
        reassembly_timeout=args.reassembly_timeout,
    )


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simple CLI for RemoteCameraCapture")
    parser.add_argument("--host", required=True, help="server host")
    parser.add_argument("--port", type=int, default=50088, help="server gRPC port")
    parser.add_argument("--codec", choices=("MJPG", "HEVC"), default="MJPG")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--udp-port", type=int, default=0, help="local UDP port; 0 lets the OS pick one")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--client-ip", default="", help="optional IP address the server should send UDP packets to")
    parser.add_argument("--device", default="")
    parser.add_argument("--max-datagram", type=int, default=DEFAULT_MAX_DATAGRAM)
    parser.add_argument("--reassembly-timeout", type=float, default=0.5)
    parser.add_argument("--read-timeout", type=float, default=1.0)
    parser.add_argument("--calibration-timeout", type=float, default=5.0)
    parser.add_argument("--stat-interval", type=float, default=2.0)
    parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    parser.add_argument("--list", action="store_true", help="list server camera capabilities and exit")
    parser.add_argument("--get-intrinsics", action="store_true", help="read camera intrinsics and distortion coefficients")
    parser.add_argument("--get-sn", action="store_true", help="read camera SN and exit")
    return parser


def main():
    args = build_argparser().parse_args()
    try:
        if args.list:
            response = RemoteCameraCapture.list_capabilities(args.host, port=args.port, device=args.device)
            print_capabilities(response)
            return
        if args.get_intrinsics:
            cap = build_capture(args)
            response = cap.get_intrinsics(timeout=args.calibration_timeout)
            print_intrinsics(response)
            return
        if args.get_sn:
            cap = build_capture(args)
            sn = cap.get_SN(timeout=args.calibration_timeout)
            print(sn)
            return
    except grpc.RpcError as exc:
        print(f"grpc_error={exc.code().name} details={exc.details()}", file=sys.stderr)
        raise SystemExit(1)

    cap = build_capture(args)
    try:
        cap.open()
        print(
            f"[camera] opened codec={cap.get('codec')} "
            f"{cap.get('width')}x{cap.get('height')}@{cap.get('fps')} "
            f"device={cap.get('device')} session={cap.get('session_id')}",
            flush=True,
        )
        last_stats = time.monotonic()
        while cap.isOpened():
            ok, frame = cap.read(timeout=args.read_timeout)
            if not ok:
                continue
            now = time.monotonic()
            if now - last_stats >= args.stat_interval:
                shape = None if frame is None else frame.shape
                print(
                    f"[camera] frame_id={cap.get('frame_id')} shape={shape} "
                    f"decoded={cap.get('decoded_frames')} dropped={cap.get('dropped_frames')} "
                    f"server_sent={cap.get('server_frames_sent')}",
                    flush=True,
                )
                last_stats = now
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


if __name__ == "__main__":
    main()
