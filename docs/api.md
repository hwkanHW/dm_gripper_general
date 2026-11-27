# 夹爪与图像接口

夹爪 API 默认开启，在 `http://SERVER_IP:8020` 访问，具体端口以配置为准。独立图像服务默认开启，浏览器默认不自动打开。

| 接口 | 用途 |
|---|---|
| `GET /` | 双相机实时预览网页 |
| `GET /cameras/state` | 左右相机画面状态、帧数和错误信息 |
| `GET /cameras/left/video` | 左相机 MJPEG 视频流 |
| `GET /cameras/right/video` | 右相机 MJPEG 视频流 |
| `GET /health` | 夹爪 API 服务是否响应 |
| `GET /state` | 左右夹爪的连接状态与读数 |
| `POST /command` | 发送控制命令 |
| `WS /ws/state?rate_hz=20` | 周期推送状态 |
| `WS /ws/command?ack=true` | 接收控制命令并返回确认 |

HTTP 和 WebSocket 接受相同的命令结构：

```json
{"side":"left","position":0.5,"speed":0.3,"torque":0.2}
```

`side` 是 `left`、`right` 或 `all`，省略时为 `all`。`position` 必填，`speed` 与 `torque` 可省略，沿用每侧上次值。初始值来自配置。三者范围均为 `0..1`，旧的 `position=1000`、`speed=50` 等值会被拒绝。

位置会限制在配置的 `min_pos..max_pos` 内。`torque` 是力矩上限，实际输出与负载有关。速度、力矩低于 `0.1` 按 SDK 最低档执行，`0` 不是停止指令。

状态中的 `position`、`target_position`、`speed`、`torque`、`torque_limit` 使用归一化值。SDK 位置读数无效时返回 `null`；电流和温度保留 SDK 的实际单位。

左右图像分别在配置的端口提供下列接口，默认 `8021` 和 `8022`：

| 接口 | 用途 |
|---|---|
| `GET /health` | 相机 IP、帧计数、最后帧时间与错误信息 |
| `GET /video` 或 `/stream.mjpg` | MJPEG 连续图像 |
| `GET /snapshot.jpg` | 最新 JPEG，无帧时返回 `503` |

图像服务 `/health` 可响应不代表已经收到画面，需检查 `has_frame` 和 `frame_count`。

主 server 端口上的 `/cameras/*` 接口复用已有取流对象。`/cameras/state` 返回 `enabled` 和左右 `cameras` 状态；图像服务关闭时视频接口返回 `503`。网页资源位于 `/viewer-assets/`。
