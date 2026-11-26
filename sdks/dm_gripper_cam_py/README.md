# Fish Camera Client

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

List server-supported camera modes:

```bash
python3 client.py --host 192.168.127.10 --port 50088 --list
```

Display live frames and print decode FPS:

```bash
python3 test_remote_camera.py
python3 test_remote_camera.py --codec HEVC
```

Edit the defaults at the top of `examples/dual_camera_viewer.py` when you want
the same left/right hand IPs, video size, and fps every run:

```python
LEFT_IP = "192.168.14.10"
RIGHT_IP = "192.168.14.11"
VIDEO_SIZE = (1280, 720)
FPS = 60
```

Run the dual-camera viewer with temporary command-line overrides:

```bash
python3 examples/dual_camera_viewer.py --left-ip 192.168.14.10 --right-ip 192.168.14.11 --video-size 1280x720 --fps 60
```

Use the same dual-camera configuration from Python:

```python
from dm_gripper_cam_py import CameraViewerConfig, run_dual_camera_viewer

run_dual_camera_viewer(
    CameraViewerConfig(port=50088, codec="MJPG"),
    left_ip="192.168.14.10",
    right_ip="192.168.14.11",
    video_size=(1280, 720),
    fps=60,
)
```

Use as an OpenCV-style capture:

```python
from remote_camera import RemoteCameraCapture

cap = RemoteCameraCapture(host="192.168.127.10", port=50088)
cap.open()
ok, frame = cap.read(timeout=1.0)
cap.release()
```

`RemoteCameraCapture` defaults to `MJPG`, `1920x1080`, and `60` fps. You can still override any of them:

```python
cap = RemoteCameraCapture(
    host="192.168.127.10",
    port=50088,
    codec="HEVC",
    width=1280,
    height=720,
    fps=60,
)
```

Use it as a context manager when possible:

```python
with RemoteCameraCapture(host="192.168.127.10") as cap:
    ok, frame = cap.read(timeout=1.0)
```

Other `RemoteCameraCapture` helpers:

```python
capabilities = RemoteCameraCapture.list_capabilities("192.168.127.10", port=50088)

with RemoteCameraCapture(host="192.168.127.10") as cap:
    sn = cap.get_sn(timeout=5.0)
    intrinsics = cap.get_intrinsics(timeout=5.0)
    latest = cap.get_latest_frame()
    codec = cap.get("codec")
    size = (cap.get("width"), cap.get("height"))
    dropped = cap.get("dropped_frames")
```

The server may negotiate a different mode if the requested codec, resolution, or fps is not available. After `open()`, use `cap.get("codec")`, `cap.get("width")`, `cap.get("height")`, and `cap.get("fps")` to inspect the active stream.
