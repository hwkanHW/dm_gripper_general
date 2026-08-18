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
    GripperControlApp,
    GripperEndpoint,
    add_gripper_args,
    normalize_common_args,
)

# Edit these defaults when running this script directly.
GRIPPER_IP = "192.168.14.10"
GRIPPER_SERVER = f"{GRIPPER_IP}:{DEFAULT_GRIPPER_PORT}"
MIN_POS = 0
MAX_POS = 1000
CONTROL_SEND_HZ = DEFAULT_CONTROL_SEND_HZ


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single gripper continuous position control")
    parser.add_argument("--ip", default=GRIPPER_IP)
    parser.add_argument("--server", default=None)
    add_gripper_args(parser)
    parser.set_defaults(
        min_pos=MIN_POS,
        max_pos=MAX_POS,
        control_send_hz=CONTROL_SEND_HZ,
    )
    return parser


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.server is None:
        args.server = f"{args.ip}:{DEFAULT_GRIPPER_PORT}"
    args.server = str(args.server)
    return normalize_common_args(args)


def main() -> int:
    args = normalize_args(build_argparser().parse_args())
    endpoint = GripperEndpoint("gripper", args.server)
    app = GripperControlApp(args, [endpoint], "Single Gripper Control")
    try:
        app.open()
        return app.run()
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
