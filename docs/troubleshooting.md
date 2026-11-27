# 常见问题

## 网页没有自动打开

默认不自动打开浏览器，夹爪控制和图像服务正常运行。需要自动打开时，使用 `--open-browser` 或设置 `server.open_browser=true`。浏览器在 server 所在机器上打开，也可手动访问 `http://SERVER_IP:8020/`，端口以配置为准。自动打开失败不会停止 server。

夹爪客户端出现 `Connection refused` 时，确认 `server.enabled=true`，且启动命令未带 `--disable-server`，然后重启 server。关闭浏览器自动打开应使用 `--no-open-browser`。

## 客户端连不到相机 HTTP 服务

`img_client.py --host` 填 server 所在电脑的地址。同机查看可用：

```bash
python img_client.py --host 127.0.0.1
```

检查 server 是否正在运行、配置中 `image_streams.enabled` 是否为 `true`，以及配置端口是否可达。超时只说明接口不可达，不能直接判定服务没有运行。

## 服务存在但没有图像

检查接口：

```bash
curl --noproxy '*' http://127.0.0.1:8021/health
curl --noproxy '*' http://127.0.0.1:8022/health
```

端口以配置为准。`has_frame=false`、`frame_count=0` 表示尚未收到可用图像；结合 `error` 排查设备 IP、gRPC 端口、编码模式、UDP 接收网卡和网络连通性。请求分辨率、帧率可能被设备协商为其他支持的模式。

## camera is busy / RESOURCE_EXHAUSTED

设备已有活动相机会话。server、独立查看器和相机测试脚本都会直接占用设备会话。停止占用程序并释放连接后再重试。

若 server 正在取流，使用 `img_client.py` 查看其 HTTP 图像；若要直连调试相机，先以 `--disable-image-streams` 重启 server，再启动直连查看器。异常退出后仍占用时，检查其他电脑上的客户端或设备端会话状态。

## 双手 dashboard 缺少接收器

双手 dashboard 仍通过外部 TCP 接收器控制夹爪；默认路径是 `examples/grip_signal_receiver.py`，本仓库不包含该文件。可以：

- 提供已有兼容脚本：`--grip-signal-receiver-path /path/to/grip_signal_receiver.py`。
- 连接已有接收器：`--no-grip-signal-auto-start`，并配置对应 host / port。
- 仅需双夹爪控制时，使用 `examples/gripper_dual_control.py` 或 server + GUI。

目录整理没有补写接收器，也没有替换 dashboard 的控制链路。

## 包导入或环境问题

根目录、`examples/` 启动入口和 `daimon-*` 命令均依赖已安装的应用包。如果出现 `No module named daimon_control`，请先激活正确环境，再从项目根目录注册 SDK 与应用包：

```bash
python -m pip install --no-deps --no-build-isolation -e sdks/dm_gripper_py -e sdks/dm_gripper_tac_py -e .
```

SDK 安装和环境创建命令见根目录 README。`CuPy import failed` 的提示来自触觉 SDK；GPU 能力需要单独安装，server 与普通相机查看无需导入 dashboard。
