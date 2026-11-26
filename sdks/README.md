# 底层 SDK

| 目录 | 内容 |
|---|---|
| `dm_gripper_py/` | 夹爪 SDK、原生控制与测试脚本 |
| `dm_gripper_cam_py/` | 相机 SDK、gRPC 协议、视频解码和原生测试 |
| `dm_gripper_tac_py/` | 触觉 SDK、模型、CUDA/TensorRT 动态库与原生实验脚本 |

三个目录整体迁入此处，内部结构保持不变。应用代码位于 `../src/daimon_control/`，日常启动入口位于项目根目录及 `../examples/`。

从项目根目录注册 SDK 与应用：

```bash
python -m pip install --no-deps --no-build-isolation -e sdks/dm_gripper_py -e sdks/dm_gripper_tac_py -e .
```

相机测试仍可通过脚本直接运行：

```bash
python sdks/dm_gripper_cam_py/test_remote_camera.py --host 192.168.1.11
```

Python 导入名保持不变，例如 `from dm_gripper_cam_py.remote_camera import RemoteCameraCapture`。设备端相机服务更新文件位于 `../deploy/camera/`。
