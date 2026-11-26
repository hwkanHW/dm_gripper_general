"""Small shared numeric and network helpers."""
import os
from urllib.parse import urlsplit


def clamp(value: int | float, low: int | float, high: int | float):
    return max(low, min(high, value))


def disable_proxy_for_host(addr: str) -> None:
    host = urlsplit(addr if "://" in addr else "//" + addr).hostname
    if not host:
        return
    for key in ("NO_PROXY", "no_proxy"):
        hosts = [item.strip() for item in os.environ.get(key, "").split(",") if item.strip()]
        if host not in hosts:
            hosts.append(host)
        os.environ[key] = ",".join(hosts)
