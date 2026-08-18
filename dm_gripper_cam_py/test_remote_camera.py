#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time

import cv2

from remote_camera import RemoteCameraCapture


def format_float_list(values) -> str:
    return "[" + ", ".join(f"{float(value):.9g}" for value in values) + "]"


def format_float_matrix(rows) -> str:
    if not rows:
        return "[]"
    formatted_rows = ["    " + format_float_list(row) for row in rows]
    return "[\n" + "\n".join(formatted_rows) + "\n  ]"


def print_camera_parameters(sn: str, intrinsics_data) -> None:
    print("[Camera Calibration Parameters]", flush=True)
    print(f"  SN: {sn}", flush=True)
    print(f"  Intrinsics [fx, fy, cx, cy]: {format_float_list(intrinsics_data['intrinsics'])}", flush=True)
    print("  Camera matrix K:", flush=True)
    print(f"  {format_float_matrix(intrinsics_data['camera_matrix'])}", flush=True)
    print(f"  Distortion coefficients: {format_float_list(intrinsics_data['distortion_coeffs'])}", flush=True)

def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Realtime RemoteCameraCapture display demo")
    parser.add_argument("--host", default="192.168.14.11")
    parser.add_argument("--port", type=int, default=50088)
    parser.add_argument("--codec", choices=("HEVC", "MJPG"), default="MJPG")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--read-timeout", type=float, default=1.0)
    parser.add_argument("--calibration-timeout", type=float, default=5.0)
    parser.add_argument("--stats-interval", type=float, default=1.0)
    parser.add_argument("--window-name", default="Remote Camera")
    parser.add_argument("--client-ip", default="")
    parser.add_argument("--udp-port", type=int, default=0)
    return parser


def main():
    args = build_argparser().parse_args()
    cap = RemoteCameraCapture(
        host=args.host,
        port=args.port,
        codec=args.codec,
        width=args.width,
        height=args.height,
        fps=args.fps,
        window_name=args.window_name,
        client_ip=args.client_ip,
        udp_port=args.udp_port,
    )

    frames_this_interval = 0
    last_stats_time = time.monotonic()

    try:
        cap.open()
        print(
            f"[demo] opened {cap.get('codec')} "
            f"{cap.get('width')}x{cap.get('height')}@{cap.get('fps')} "
            f"device={cap.get('device')} session={cap.get('session_id')}",
            flush=True,
        )

        sn = cap.get_sn(timeout=args.calibration_timeout)
        intrinsics_data = cap.get_intrinsics(timeout=args.calibration_timeout)
        print_camera_parameters(sn, intrinsics_data)

        cv2.namedWindow(args.window_name, cv2.WINDOW_NORMAL)

        while cap.isOpened():
            ok, frame = cap.read(timeout=args.read_timeout)
            if not ok:
                err = cap.get("error")
                if err:
                    print(f"[demo] read failed: {err}", flush=True)
                continue

            frames_this_interval += 1
            cv2.imshow(args.window_name, frame)

            now = time.monotonic()
            elapsed = now - last_stats_time
            if elapsed >= args.stats_interval:
                decode_fps = frames_this_interval / elapsed
                print(
                    f"[demo] decode_fps={decode_fps:.1f} "
                    f"frame_id={cap.get('frame_id')} "
                    f"decoded={cap.get('decoded_frames')} "
                    f"dropped={cap.get('dropped_frames')} "
                    f"drop_ratio={cap.get('drop_ratio') * 100:.1f}% "
                    f"server_sent={cap.get('server_frames_sent')}",
                    flush=True,
                )
                frames_this_interval = 0
                last_stats_time = now

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q") or key == 27:
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("[demo] released", flush=True)


if __name__ == "__main__":
    main()
