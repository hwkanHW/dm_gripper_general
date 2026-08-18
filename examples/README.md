# 示例脚本

这些脚本是项目的可运行入口。请从项目根目录执行命令，脚本内部会自动把项目根目录加入 Python 搜索路径。

| 示例 | 用途 | 命令 |
|---|---|---|
| 双目相机预览 | 查看左右远程相机，脚本顶部可配置 IP、视频大小和帧率 | `python examples/dual_camera_viewer.py` |
| 双手整合 dashboard | 双相机、触觉和双夹爪整合显示/控制，底部有左右两条独立连续位置 bar | `python examples/daimond_pack_dual.py` |
| 单手整合 dashboard | 单手相机、触觉和夹爪整合显示/控制，底部是连续夹爪位置 bar | `python examples/daimond_pack_single.py` |
| 单手夹爪控制 | 直接连接 SDK 控制一只夹爪，连续位置 bar | `python examples/gripper_single_control.py` |
| 双手夹爪控制 | 直接连接 SDK 分别控制左右夹爪，左右分开的连续位置 bar | `python examples/gripper_dual_control.py` |
| 触觉 dashboard | 触觉显示兼容入口 | `python examples/tac.py` |

## 双目相机配置

直接改 `examples/dual_camera_viewer.py` 顶部默认值：

```python
LEFT_IP = "192.168.14.10"
RIGHT_IP = "192.168.14.11"
VIDEO_SIZE = (1280, 720)
FPS = 60
```

也可以临时用命令行覆盖：

```bash
python examples/dual_camera_viewer.py --left-ip 192.168.14.10 --right-ip 192.168.14.11 --video-size 1280x720 --fps 60
```

## Dashboard 频率

`examples/daimond_pack_single.py` 和 `examples/daimond_pack_dual.py` 顶部都暴露了常用频率默认值：

```python
DEFAULT_CAMERA_FPS = 60
DEFAULT_TACTILE_MAX_FPS = 120
DEFAULT_CONTROL_SEND_HZ = 10.0
```

也可以命令行临时覆盖：

```bash
python examples/daimond_pack_single.py --fps 60 --max-fps 120 --control-send-hz 10
python examples/daimond_pack_dual.py --fps 60 --max-fps 120 --control-send-hz 10
```

单手 dashboard 通过 `--ip` 选择整只单手设备；相机、触觉和夹爪默认都使用这个 IP，夹爪地址自动是 `IP:55551`：

```bash
python examples/daimond_pack_single.py --ip 192.168.14.10
python examples/daimond_pack_single.py --ip 192.168.14.11
```

如果只想单独改夹爪地址，可以覆盖 `--gripper-server`：

```bash
python examples/daimond_pack_single.py --ip 192.168.14.10 --gripper-server 192.168.14.99:55551
```

## 独立夹爪控制

这两个脚本直接连接夹爪 SDK，不需要单独启动接收器。

单手脚本顶部可改默认夹爪地址、位置范围和连续控制发送频率：

```python
GRIPPER_IP = "192.168.14.11"
MIN_POS = 0
MAX_POS = 1000
CONTROL_SEND_HZ = 10.0
```

双手脚本顶部可分别改左右夹爪地址：

```python
LEFT_IP = "192.168.14.10"
RIGHT_IP = "192.168.14.11"
```

运行：

```bash
python examples/gripper_single_control.py
python examples/gripper_dual_control.py
```
