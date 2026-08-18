#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from gripper_control_common import (
    DEFAULT_CONTROL_SEND_HZ,
    DEFAULT_GRIPPER_PORT,
    DEFAULT_LEFT_IP,
    DEFAULT_RIGHT_IP,
    GripperControlApp,
    GripperEndpoint,
    add_gripper_args,
    normalize_common_args,
)

# Edit these defaults when running this script directly.
LEFT_IP = DEFAULT_LEFT_IP
RIGHT_IP = DEFAULT_RIGHT_IP
LEFT_GRIPPER_SERVER = f"{LEFT_IP}:{DEFAULT_GRIPPER_PORT}"
RIGHT_GRIPPER_SERVER = f"{RIGHT_IP}:{DEFAULT_GRIPPER_PORT}"
MIN_POS = 0
MAX_POS = 1000
CONTROL_SEND_HZ = DEFAULT_CONTROL_SEND_HZ


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dual gripper separate continuous position control")
    parser.add_argument("--left-ip", default=LEFT_IP)
    parser.add_argument("--right-ip", default=RIGHT_IP)
    parser.add_argument("--left-server", default=None)
    parser.add_argument("--right-server", default=None)
    add_gripper_args(parser)
    parser.set_defaults(
        min_pos=MIN_POS,
        max_pos=MAX_POS,
        control_send_hz=CONTROL_SEND_HZ,
    )
    return parser


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.left_server is None:
        args.left_server = f"{args.left_ip}:{DEFAULT_GRIPPER_PORT}"
    if args.right_server is None:
        args.right_server = f"{args.right_ip}:{DEFAULT_GRIPPER_PORT}"
    args.left_server = str(args.left_server)
    args.right_server = str(args.right_server)
    return normalize_common_args(args)


def main() -> int:
    args = normalize_args(build_argparser().parse_args())
    endpoints = [
        GripperEndpoint("left", args.left_server),
        GripperEndpoint("right", args.right_server),
    ]
    app = GripperControlApp(args, endpoints, "Dual Gripper Control")
    try:
        app.open()
        return app.run()
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
