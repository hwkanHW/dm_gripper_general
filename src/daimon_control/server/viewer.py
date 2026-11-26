"""Local web viewer assets and browser launch after the HTTP listener is ready."""
from __future__ import annotations

import logging
from pathlib import Path
import threading
import webbrowser

import uvicorn

WEB_ROOT = Path(__file__).with_name("web")
logger = logging.getLogger("uvicorn.error")


def viewer_url(host: str, port: int) -> str:
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def open_viewer(url: str) -> None:
    try:
        opened = webbrowser.open_new_tab(url)
    except Exception as exc:
        logger.warning("Could not open browser: %s. Open %s manually.", exc, url)
    else:
        if not opened:
            logger.warning("No browser available. Open %s manually.", url)


class ViewerServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, *, auto_open: bool):
        super().__init__(config)
        self.auto_open = auto_open

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        if not self.started:
            return
        url = viewer_url(self.config.host, self.config.port)
        logger.info("Dual-camera viewer: %s", url)
        if self.auto_open:
            threading.Thread(target=open_viewer, args=(url,), daemon=True, name="camera-viewer-browser").start()
