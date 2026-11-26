#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in src/daimon_control."""
import importlib
import sys

_implementation = importlib.import_module("daimon_control.server.app")

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    # Keep imports and module-level state identical to the canonical module.
    sys.modules[__name__] = _implementation
