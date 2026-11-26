# 相机设备端部署文件

这里的 `server.py` 是运行在相机设备上的 gRPC / UDP 服务，默认 gRPC 端口 `50088`。它与根目录 `gripper_server.py`（电脑上的夹爪控制与 HTTP 图像服务）是两个不同服务。

文件从 `update_packages/` 迁移到这里，设备端 `server.py` 内容保持不变。本次目录整理不执行上传、远程重启或 systemd 变更。

## 配套依赖

这是已有设备环境的更新文件，不是完整独立安装包。设备工作目录需保留：

- `camera_proxy_pb2.py`、`camera_proxy_pb2_grpc.py`，协议须与客户端匹配。
- `camera_calibration.py`、`camera_selector.py`；本仓库未包含这两个设备端配套模块。
- 相机标定数据、设备权限、gRPC Python 依赖。
- GStreamer、对应编解码插件及可导入 `gi.repository.Gst` 的 Python 环境。

不要把它当作电脑端 server 的替代入口。

## 上传更新

从项目根目录把文件传到设备用户主目录：

```bash
scp deploy/camera/server.py cftc@192.168.1.11:~/server.py
```

再按设备实际部署目录更新文件。设备若运行 `/app/fish_cam_server/server.py`，仅上传到 `~/server.py` 不会改变运行服务；需在设备上明确安装到工作目录，并按既有服务名重启。

已有 systemd 配置、状态检查和常用 SSH 命令见 [设备运维问答](cftc_fish_camera_qa.md)。问答中的服务名和路径是示例，执行前应对照设备实际配置。
