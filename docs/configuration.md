# Server 配置

默认配置是项目根目录的 `gripper_server_config.json`。源码与 editable 安装按项目位置查找，不受启动目录影响。普通 wheel 安装默认查找当前目录，也可以显式指定：

```bash
python gripper_server.py --config /path/to/gripper_server_config.json
# 安装应用包后：
daimon-server --config /path/to/gripper_server_config.json
```

配置修改后重启 server 生效。显式命令行参数覆盖文件中对应项；文件本身仍须完整有效。

| 分组 | 字段 | 说明 |
|---|---|---|
| `server` | `host`, `port` | HTTP/WebSocket 与双相机网页监听地址、端口 |
| `server` | `open_browser` | 是否在服务就绪后自动打开双相机网页，默认 `true` |
| `grippers` | `left`, `right` | 夹爪设备 `IP:端口`，空字符串禁用对应侧 |
| `grippers` | `speed`, `torque` | 初始速度、力矩上限，`0..1` |
| `grippers` | `min_pos`, `max_pos` | 允许的位置范围，`0..1`，下限不大于上限 |
| `image_streams` | `enabled` | 布尔值，是否开启左右相机 HTTP 服务，默认 `false` |
| `image_streams` | `host`, `left_port`, `right_port` | 图像服务监听地址及左右端口 |
| `cameras` | `left_ip`, `right_ip`, `port` | 相机设备 IP 与共用 gRPC 端口 |
| `cameras` | `width`, `height`, `fps` | 请求宽高与帧率，正整数，左右相机共用 |
| `cameras` | `codec` | `MJPG` 或 `HEVC` |
| `cameras` | `jpeg_quality` | 输出 MJPEG 的 JPEG 质量，`1..100` |
| `cameras` | `client_ip` | 设备发送 UDP 图像的目标本机 IP，空字符串自动选择 |
| `cameras` | `bind_host` | 本机接收 UDP 的监听地址 |
| `cameras` | `device` | 指定设备路径，空字符串让设备端选择 |

除兼容旧配置而可省略的 `server.open_browser` 外，其他分组和字段均为必需项；未知字段、类型错误、非法范围会在连接硬件前报错。端口范围 `1..65535`。两侧夹爪不能同时禁用。

归一化位置 `0=闭合`、`1=张开`。速度、力矩按 `值 × 100` 转换，SDK 最小值为 `10`，所以 `0..0.1` 按最低档执行。位置按 `值 × 1000` 转为 SDK 单位。电流、温度、物理标定与时间参数保持原单位。

临时覆盖示例：

```bash
python gripper_server.py --camera-width 640 --camera-height 480 --camera-fps 30
python gripper_server.py --speed 0.3 --torque 0.2
python gripper_server.py --enable-image-streams
python gripper_server.py --no-open-browser
```

server 配置目前只控制 server。独立夹爪、相机和 dashboard 示例继续使用各自的命令行参数和默认值，尚未合并它们的运行配置。

网页地址为 `http://SERVER_IP:端口/`，左右图像通过同一个 server 端口访问。自动打开的是 server 所在机器上的浏览器；远程机器或无桌面环境可手动打开网页。关闭自动打开不会关闭网页服务，关闭图像服务时页面仍可访问并显示未启用状态。
