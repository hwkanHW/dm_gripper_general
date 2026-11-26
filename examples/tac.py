#!/usr/bin/env python3
from __future__ import annotations

"""Compatibility entry point for the Daimon tactile dashboard."""



from dm_gripper_tac_py.tac import (  # noqa: F401
    SensorManager,
    depth_to_u8,
    disable_proxy_for_host,
    draw_flow,
    main,
    make_dashboard,
    make_options,
    parse_args,
    resize_keep,
)


if __name__ == "__main__":
    main()
