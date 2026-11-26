# 示例入口

在安装好应用包的环境中，从项目根目录运行。安装方式见根目录 README。示例只负责启动和少量默认参数；公共实现位于 `src/daimon_control/`。

| 用途 | 命令 | 实现位置 |
|---|---|---|
| 双相机直连预览 | `python examples/dual_camera_viewer.py` | `sdks/dm_gripper_cam_py/dual_camera_viewer.py` |
| 单夹爪直接控制 | `python examples/gripper_single_control.py` | `daimon_control/clients/direct_control.py` |
| 双夹爪直接控制 | `python examples/gripper_dual_control.py` | `daimon_control/clients/direct_control.py` |
| 单手 dashboard | `python examples/dashboard_single.py` | `daimon_control/dashboards/single.py` |
| 双手 dashboard | `python examples/dashboard_dual.py` | `daimon_control/dashboards/dual.py` |
| 触觉 dashboard | `python examples/tac.py` | `sdks/dm_gripper_tac_py/tac.py` |

Dashboard 统一使用 `dashboard_single.py`、`dashboard_dual.py`。公共夹爪控制代码从 `daimon_control` 包导入，示例目录只保留可运行入口。

## 相机

直连脚本不经过 gripper server，也不读取 `gripper_server_config.json`：

```bash
python examples/dual_camera_viewer.py \
  --left-ip 192.168.1.10 --right-ip 192.168.1.11 \
  --video-size 1280x720 --fps 30 --codec MJPG
```

必要时用 `--client-ip` 指定本机接收 UDP 图像的网卡 IP。`MJPG` 和 `HEVC` 是可选编码，实际模式取决于设备。脚本顶部保留 `LEFT_IP`、`RIGHT_IP`、`VIDEO_SIZE`、`FPS` 默认值。

相机单会话被占用时，先停止占用程序；若需同时控制夹爪，启动 `python gripper_server.py --disable-image-streams` 再使用直连查看器。

## 独立夹爪控制

```bash
python examples/gripper_single_control.py --ip 192.168.1.10 --speed 0.5 --torque 0.3
python examples/gripper_dual_control.py --left-ip 192.168.1.10 --right-ip 192.168.1.11
```

位置、速度、力矩参数为 `0..1`，位置 `0=闭合`、`1=张开`；速度、力矩低于 `0.1` 按 SDK 最低档执行。脚本顶部可编辑 `MIN_POS`、`MAX_POS` 和 `CONTROL_SEND_HZ`。此处的设备默认值与 server 配置独立。

## Dashboard

```bash
python examples/dashboard_single.py --ip 192.168.1.10
python examples/dashboard_single.py --ip 192.168.1.10 --fps 30 --max-fps 120 --control-send-hz 10
```

单手 dashboard 的相机、触觉、夹爪默认使用 `--ip`，夹爪地址自动补充 `:55551`，可用 `--gripper-server` 单独覆盖。

默认频率和实现位于 `src/daimon_control/dashboards/single.py`、`dual.py`；推荐通过命令行覆盖。每个入口支持 `--help`。

双手 dashboard 依赖外部 `grip_signal_receiver.py`，本仓库未包含。需用 `--grip-signal-receiver-path /path/to/grip_signal_receiver.py` 指定接收器，或用 `--no-grip-signal-auto-start` 连接已运行的兼容接收器。此次仅迁移结构，未替换其控制链路。

GUI 常用操作：拖动位置条，`L` 闭合、`P` 张开、`Q` / `Esc` 退出；dashboard 的 `R` 重置触觉。
