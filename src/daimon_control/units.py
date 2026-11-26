"""Conversions between normalized controls and the gripper SDK units."""
from .utils import clamp


def position_to_sdk(value: float) -> int:
    return round(value * 1000)


def percent_to_sdk(value: float) -> int:
    # The SDK accepts 10..100; zero is the minimum setting, not a stop command.
    return max(10, round(value * 100))


def normalize_reading(value: int | float | None, scale: int) -> float | None:
    if value is None or value < 0:
        return None
    return float(clamp(value / scale, 0.0, 1.0))
