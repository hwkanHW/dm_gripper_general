# dmrobotics/__init__.py

import logging
from dataclasses import dataclass
from typing import Optional, Union

from enum import Enum
from . import utils as utils 

__all__ = [
    "Sensor",
    "SensorOptions",
    "Mode",
    "utils",
    "listConnectedDevIDs",
    "listRemotedDevIDs",
    "compare_images",
    "init_sensor_cache",
    "init_sensor_cache_remote",
    "set_detect_enabled",
    "set_detect_enabled_remote",
]

@dataclass(frozen=True)
class Mode(str, Enum):
    STANDARD = "standard"  
    HIGH = "high"           

@dataclass(frozen=False)
class SensorOptions:
    dev_id: Union[int, str]
    show_fps: bool = False
    mode: Mode = Mode.HIGH
    backend: str = "cpu"
    enable_raw: bool = False
    enable_deformation: bool = False
    enable_shear: bool = False
    enable_depth: bool = False
    enable_force: bool = False
    cuda_id: int = 0
    max_fps: int = 120
    remote_addr: str = "192.168.127.10:50051"
    pc_host: str = "0.0.0.0"
    pc_port: int = 60000



class Sensor:
    def __init__(self, opt: Optional[SensorOptions] = None) -> None:
        if opt is None:
            raise ValueError("opt must be provided as SensorOptions(...)")

        self._opt = opt

        # ---- validate backend ----
        allowed = {"cpu", "cuda", "flux"}
        b = getattr(opt, "backend", None)
        if b is None:
            raise ValueError("opt.backend must be set to one of: cpu / cuda / Flux")

        b_norm = str(b).strip().lower()
        if b_norm not in allowed:
            raise ValueError(
                f"Invalid backend '{b}'. backend must be one of: cpu / cuda / Flux"
            )

        # 统一用规范化后的值，避免后面比较大小写踩坑
        opt.backend = "Flux" if b_norm == "flux" else b_norm  # -> "cpu" / "cuda" / "Flux"

        # ---- construct hardware ----
        if opt.backend == "Flux":
            from .src.dmSDK_client import SensorClient
            self.hardware = SensorClient(opt)
        else:
            from .src.dmSDK import DMV1
            self.hardware = DMV1(opt)

        logging.info("Sensor ID: %s", self.getDevID())
        logging.info("Sensor status: %s", self.getDevStatus())

   
    def reset(self) -> None:
        return self.hardware.reset()

    def getBaseFrame(self):
        return self.hardware.getBaseFrame()

    def getRawImg(self):
        return self.hardware.getRawImg()

    def getInferImg(self):
        return self.hardware.getInferImg()

    def getDepth(self):
        return self.hardware.getDepth()

    def getDeformation2D(self):
        return self.hardware.getDeformation2D()

    def getShear(self):
        return self.hardware.getShear()
    
    def getForce(self):
        return self.hardware.getForce()
    
    def getDistributeForce(self):
        return self.hardware.getDistributeForce()
    
    def process(self,img,getdepth = False,getshear= False,getforce=False,getdistforce=False):
        return self.hardware.process(img,getdepth,getshear,getforce,getdistforce)
    
    def getContactArea(self):
        return self.hardware.getContactArea()
    
    def disconnect(self) -> None:
        self.hardware.disConnect()

    def getDevID(self):
        return self.hardware.getDevID()

    def getDevStatus(self):
        return self.hardware.getDevStatus(print_info=False)

    def setBaseFrame(self, img) -> None:
        self.hardware.setBaseFrame(img)

    def setMaxFPS(self,max_fps : int = 120):
        return self.hardware.setMaxFPS(max_fps)
    
    def wait_for_new(self, last_seq: int, timeout_ms: int = 1000):
        return self.hardware.wait_for_new(last_seq, timeout_ms)

    def setEnableFlags(self,raw:bool,deformation:bool,depth:bool,shear:bool):
        return self.hardware.setEnableFlags(raw,deformation,depth,shear)
    
    def getEvents(self):
        return self.hardware.getEvents()


# ---------------------------------------------------------------------------
# Device enumeration
# 枚举当前可用的传感器
# ---------------------------------------------------------------------------

def listConnectedDevIDs():
    """
    Query connected sensor IDs via CameraDeviceManager.
    使用 CameraDeviceManager 查询当前可用的传感器 ID 列表。

    Returns a list (e.g. serial numbers).
    返回一个列表（例如设备序列号列表）。
    """
    from .src.CameraDeviceManager import CameraDeviceManager
    mgr = CameraDeviceManager()
    dev_ids = mgr.find_devices()
    print(dev_ids)
    return dev_ids

def listRemotedDevIDs(remote_addr: str, timeout_s: float = 2.0):
    from .src.dmSDK_client import listRemotedDevIDs as _listRemotedDevIDs
    return _listRemotedDevIDs(remote_addr, timeout_s=timeout_s)

def init_sensor_cache(
    serial,
    timeout_ms=3000,
):
    from .src.detect import init_sensor_cache as _init_sensor_cache

    return _init_sensor_cache(serial=serial, timeout_ms=timeout_ms)


def compare_images(
    reference_image,
    current_image,
):
    from .src.detect import compare_images as _compare_images

    return _compare_images(
        reference_image=reference_image,
        current_image=current_image,
    )

def init_sensor_cache_remote(
    remote_addr: str,
    serial: str,
    timeout_ms: int = 3000,
    rpc_timeout_s: Optional[float] = None,
):
    from .src.dmSDK_client import initSensorCacheRemote

    return initSensorCacheRemote(
        remote_addr=remote_addr,
        serial=serial,
        timeout_ms=timeout_ms,
        timeout_s=rpc_timeout_s,
    )


def set_detect_enabled(
    value,
    timeout_s: float = 2.0,
):
    from .src.detect import set_detect_enabled as _set_detect_enabled

    return _set_detect_enabled(value=value)

def set_detect_enabled_remote(
    remote_addr: str,
    value,
    timeout_s: float = 2.0,
):
    from .src.dmSDK_client import setDetectEnabledRemote
    from .src.detect import parse_detect_enabled
    return setDetectEnabledRemote(
        remote_addr=remote_addr,
        enabled=parse_detect_enabled(value),
        timeout_s=timeout_s,
    )
