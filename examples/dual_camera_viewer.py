#!/usr/bin/env python3
"""双目相机查看器兼容入口。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dm_gripper_cam_py.dual_camera_viewer import (  # noqa: E402
    CameraViewerConfig,
    main as _viewer_main,
    run_dual_camera_viewer,
)

# Edit these defaults when running this script directly.
LEFT_IP = "192.168.14.10"
RIGHT_IP = "192.168.14.11"
VIDEO_SIZE = (1280, 720)
FPS = 60

SCRIPT_CONFIG = CameraViewerConfig(
    left_ip=LEFT_IP,
    right_ip=RIGHT_IP,
    width=VIDEO_SIZE[0],
    height=VIDEO_SIZE[1],
    fps=FPS,
)

__all__ = [
    "CameraViewerConfig",
    "FPS",
    "LEFT_IP",
    "main",
    "RIGHT_IP",
    "SCRIPT_CONFIG",
    "VIDEO_SIZE",
    "run_dual_camera_viewer",
]


def main(argv: Sequence[str] | None = None) -> int:
    return _viewer_main(argv, default_config=SCRIPT_CONFIG)


if __name__ == "__main__":
    raise SystemExit(main())
