#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Simplified DM tactile visualization demo.

Structure:
- SensorManager: sensor communication
- Visualizer: OpenCV rendering
- Utils: argument parsing and image conversion
"""

import os
import time
import argparse
import multiprocessing as mp
import queue
import sys
from pathlib import Path
from urllib.parse import urlsplit
import cv2
import numpy as np

TACTILE_ROOT = Path(__file__).resolve().parent
if str(TACTILE_ROOT) not in sys.path:
    sys.path.insert(0, str(TACTILE_ROOT))

from dmrobotics import Sensor, SensorOptions, Mode
from dmrobotics.utils import put_arrows_on_image


DEPTH_SENSITIVITY = 600.0
DEPTH_DEADBAND = 0.002

def disable_proxy_for_host(addr):
    """Avoid grpc using HTTP proxy for local sensor."""
    host = urlsplit(
        addr if "://" in addr else "//" + addr
    ).hostname

    if not host:
        return

    for key in ["NO_PROXY", "no_proxy"]:
        values = os.environ.get(key, "")
        hosts = [x.strip() for x in values.split(",") if x.strip()]

        if host not in hosts:
            hosts.append(host)

        os.environ[key] = ",".join(hosts)

def depth_to_u8(depth, sensitivity=DEPTH_SENSITIVITY,
                deadband=DEPTH_DEADBAND):
    """Convert float depth map to displayable uint8 image."""
    if depth is None:
        return None

    d = np.asarray(depth, dtype=np.float32)
    d = np.nan_to_num(d)
    d = np.maximum(d - deadband, 0)
    return np.clip(d * sensitivity, 0, 255).astype(np.uint8)


def make_options(args):
    """Create sensor configuration."""
    return SensorOptions(
        dev_id=int(args.dev_id) if args.dev_id.isdigit() else args.dev_id,
        backend=args.backend,
        mode=Mode.STANDARD,
        show_fps=True,
        max_fps=args.max_fps,
        enable_deformation=True,
        enable_depth=True,
        enable_shear=True,
        enable_force=args.force,
        remote_addr=args.remote_addr,
        pc_host=args.pc_host,
        pc_port=args.pc_port,
    )


class SensorManager:
    """Handle sensor input only."""

    def __init__(self, options):
        self.sensor = Sensor(options)
        self.last_id = -1

    def update(self):
        """Wait for next frame."""
        if not self.sensor.wait_for_new(self.last_id, 500):
            return False
        return True

    def read(self):
        """Read all required data once."""
        data = {}

        fid, img = self.sensor.getInferImg()
        if img is not None:
            data["infer"] = img.img

        fid, flow = self.sensor.getDeformation2D()
        data["deformation"] = flow

        fid, shear = self.sensor.getShear()
        data["shear"] = shear

        fid, depth = self.sensor.getDepth()
        data["depth"] = depth

        self.last_id = fid
        return data

    def reset(self):
        """Reset tactile sensor."""
        self.sensor.reset()

    def close(self):
        """Release sensor."""
        self.sensor.disconnect()


def draw_flow(flow):
    """Render tactile flow field."""
    if flow is None:
        return None
    canvas = np.zeros(
        flow.shape[:2] + (3,),
        dtype=np.uint8
    )
    return put_arrows_on_image(
        canvas,
        flow,
        step=16,
        scale=20
    )


def resize_keep(img, size):
    """Resize image to dashboard tile size."""
    return cv2.resize(
        img,
        size,
        interpolation=cv2.INTER_AREA
    )


def make_dashboard(data):
    """
    Combine all visualization into one OpenCV window.
    Layout:
        infer | deformation
        shear | depth
    """

    tiles = []
    # ---------- infer ----------
    infer = data.get("infer")

    if infer is None:
        infer = np.zeros(
            (480,640,3),dtype=np.uint8
        )
    infer = resize_keep(
        infer,(320,240)
    )
    # ---------- deformation ----------
    deformation = draw_flow(
        data.get("deformation")
    )
    if deformation is None:
        deformation = np.zeros(
            (240,320,3),
            dtype=np.uint8
        )
    deformation = resize_keep(
        deformation,
        (320,240)
    )
    # ---------- shear ----------
    shear = draw_flow(
        data.get("shear")
    )
    if shear is None:
        shear = np.zeros(
            (240,320,3),
            dtype=np.uint8
        )
    shear = resize_keep(
        shear,(320,240)
    )
    # ---------- depth ----------
    depth = depth_to_u8(
        data.get("depth")
    )
    if depth is None:
        depth = np.zeros(
            (240,320),
            dtype=np.uint8
        )
    depth = resize_keep(
        depth,(320,240)
    )
    # 灰度转BGR方便拼接
    depth = cv2.cvtColor(
        depth,
        cv2.COLOR_GRAY2BGR
    )
    # ---------- dashboard ----------
    top = np.hstack(
        [infer,deformation]
    )
    bottom = np.hstack(
        [shear,depth]
    )
    dashboard = np.vstack(
        [top,bottom]
    )
    return dashboard


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev-id", default="0")
    parser.add_argument("--backend", default="Flux")
    parser.add_argument("--remote-addr",
                        default="192.168.10.11:50051")
    parser.add_argument("--pc-host",
                        default="192.168.10.123")
    parser.add_argument("--pc-port", type=int, default=60030)
    parser.add_argument("--max-fps", type=int, default=120)
    parser.add_argument("--force", action="store_true")

    return parser.parse_args()


def main():
    args = parse_args()
    args.backend = "Flux" if str(args.backend).strip().lower() == "flux" else args.backend
    disable_proxy_for_host(args.remote_addr)
    print(
        "[tactile] opening "
        f"remote={args.remote_addr} dev_id={args.dev_id} "
        f"pc={args.pc_host}:{args.pc_port} backend={args.backend}",
        flush=True,
    )
    sensor = SensorManager(make_options(args))
    print("[tactile] opened", flush=True)

    try:
        while True:

            if str(args.backend).strip().lower() == "flux":
                sensor.sensor.getEvents()

            if not sensor.update():
                continue

            frames = sensor.read()
            dashboard = make_dashboard(frames)
            cv2.imshow(
                "DM Tactile Dashboard",
                dashboard
            )

            key = cv2.waitKey(1) & 0xff

            if key == ord("q"):
                break

            if key == ord("r"):
                print("Reset sensor")
                sensor.reset()

    finally:
        sensor.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
