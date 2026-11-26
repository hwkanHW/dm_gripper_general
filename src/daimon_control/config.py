"""Server configuration loading, CLI overrides, and validation."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .paths import DEFAULT_CONFIG


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a Daimon gripper and camera FastAPI server",
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="server JSON config (default: %(default)s)")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument(
        "--open-browser", action=argparse.BooleanOptionalAction,
        help="open the dual-camera web viewer when the server is ready",
    )
    parser.add_argument("--left-gripper", help="left gripper host:port, empty to disable")
    parser.add_argument("--right-gripper", help="right gripper host:port, empty to disable")
    parser.add_argument("--speed", type=float, help="initial speed, 0..1 (SDK minimum 0.1)")
    parser.add_argument("--torque", type=float, help="initial torque, 0..1 (SDK minimum 0.1)")
    parser.add_argument("--min-pos", type=float, help="command lower bound, 0..1")
    parser.add_argument("--max-pos", type=float, help="command upper bound, 0..1")
    parser.add_argument(
        "--enable-image-streams",
        dest="enable_image_streams",
        action="store_true",
        help="override image_streams.enabled to true",
    )
    parser.add_argument(
        "--disable-image-streams",
        dest="enable_image_streams",
        action="store_false",
        help="do not serve left/right camera MJPEG streams",
    )
    parser.add_argument("--image-host", help="bind address for MJPEG image servers")
    parser.add_argument("--left-image-port", type=int)
    parser.add_argument("--right-image-port", type=int)
    parser.add_argument("--left-camera-host", help="override cameras.left_ip in config")
    parser.add_argument("--right-camera-host", help="override cameras.right_ip in config")
    parser.add_argument("--camera-port", type=int)
    parser.add_argument("--camera-codec", choices=("MJPG", "HEVC"))
    parser.add_argument("--camera-width", type=int, help="override cameras.width in config")
    parser.add_argument("--camera-height", type=int, help="override cameras.height in config")
    parser.add_argument("--camera-fps", type=int, help="override cameras.fps in config")
    parser.add_argument("--camera-jpeg-quality", type=int)
    parser.add_argument("--camera-client-ip")
    parser.add_argument("--camera-bind-host")
    parser.add_argument("--camera-device")
    return parser


def parse_server_args(argv=None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        with args.config.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
        sections = {
            "server": {"host": "host", "port": "port", "open_browser": "open_browser"},
            "grippers": {
                "left": "left_gripper", "right": "right_gripper",
                "speed": "speed", "torque": "torque",
                "min_pos": "min_pos", "max_pos": "max_pos",
            },
            "image_streams": {
                "enabled": "enable_image_streams", "host": "image_host",
                "left_port": "left_image_port", "right_port": "right_image_port",
            },
            "cameras": {
                "left_ip": "left_camera_host", "right_ip": "right_camera_host",
                "port": "camera_port", "codec": "camera_codec",
                "width": "camera_width", "height": "camera_height", "fps": "camera_fps",
                "jpeg_quality": "camera_jpeg_quality", "client_ip": "camera_client_ip",
                "bind_host": "camera_bind_host", "device": "camera_device",
            },
        }
        if not isinstance(config, dict) or set(config) != set(sections):
            raise ValueError(f"config must contain exactly these sections: {', '.join(sections)}")
        actions = {action.dest: action for action in parser._actions}
        allow_empty = {"left_gripper", "right_gripper", "camera_client_ip", "camera_device"}
        ranges = {
            "port": (1, 65535), "left_image_port": (1, 65535),
            "right_image_port": (1, 65535), "camera_port": (1, 65535),
            "speed": (0, 1), "torque": (0, 1),
            "min_pos": (0, 1), "max_pos": (0, 1),
            "camera_jpeg_quality": (1, 100),
        }

        def validate(dest, value, label):
            action = actions[dest]
            expected = bool if dest in {"enable_image_streams", "open_browser"} else (action.type or str)
            if (expected is float and type(value) not in (int, float)) or (expected is not float and type(value) is not expected):
                raise ValueError(f"{label} must be {expected.__name__}")
            if expected is str:
                value = value.strip()
                if not value and dest not in allow_empty:
                    raise ValueError(f"{label} must not be empty")
            if action.choices and value not in action.choices:
                raise ValueError(f"{label} must be one of {action.choices}")
            if expected in (int, float):
                low, high = ranges.get(dest, (1, None))
                if not math.isfinite(value) or value < low or (high is not None and value > high):
                    raise ValueError(f"{label} is outside the allowed range {low}..{high or 'unlimited'}")
            return float(value) if expected is float else value

        for section, fields in sections.items():
            values = config[section]
            defaults = {"open_browser": True} if section == "server" else {}
            required = set(fields) - set(defaults)
            if not isinstance(values, dict) or set(values) - set(fields) or required - set(values):
                raise ValueError(f"{section} requires {', '.join(sorted(required))}; allowed: {', '.join(fields)}")
            values = {**defaults, **values}
            for key, dest in fields.items():
                value = validate(dest, values[key], f"{section}.{key}")
                if hasattr(args, dest):
                    value = validate(dest, getattr(args, dest), f"--{dest.replace('_', '-')}")
                setattr(args, dest, value)
        if args.min_pos > args.max_pos:
            raise ValueError("grippers.min_pos must be <= grippers.max_pos")
    except (OSError, ValueError) as exc:
        parser.error(f"cannot load server config {args.config}: {exc}")
    return args


