# 结构与迁移记录

## 模块职责

| 位置 | 职责 |
|---|---|
| `src/daimon_control/config.py` | 全部 server 配置读取、CLI 覆盖、范围校验 |
| `src/daimon_control/gripper.py` | server 与直接控制的 SDK 适配器，保留各自生命周期 |
| `src/daimon_control/units.py` | 归一化控制与 SDK 单位转换 |
| `src/daimon_control/camera.py` | 应用访问相机 SDK 的入口 |
| `src/daimon_control/server/models.py` | 对外命令数据结构 |
| `src/daimon_control/server/app.py` | FastAPI 路由、WebSocket 与服务启动 |
| `src/daimon_control/server/image_stream.py` | 相机读取、JPEG 编码与 HTTP 图像服务 |
| `src/daimon_control/server/viewer.py` | 服务就绪后的浏览器启动与网页资源定位 |
| `src/daimon_control/server/web/` | 双相机网页的 HTML、CSS 和 JavaScript |
| `src/daimon_control/clients/` | 远程控制 GUI、HTTP 查看器、直接控制 GUI |
| `src/daimon_control/dashboards/` | 单/双手整合界面与现有控制链路 |
| `sdks/dm_gripper_*/` | SDK、协议生成文件、模型及历史实验脚本 |

## 旧路径与新位置

| 原路径 | 新实现 / 处理方式 |
|---|---|
| `gripper_server.py` | 启动入口保留，实现拆入 `config.py`、`gripper.py`、`server/` |
| `gripper_gui_client.py` | 启动入口保留，实现移入 `clients/gripper_gui.py` |
| `img_client.py` | 启动入口保留，实现移入 `clients/image_viewer.py` |
| `examples/gripper_control_common.py` | 旧兼容文件已删除，SDK 适配器移入 `gripper.py`，界面移入 `clients/direct_control.py` |
| `examples/daimond_pack_single.py` | 旧入口已删除，使用 `examples/dashboard_single.py`；实现移入 `dashboards/single.py` |
| `examples/daimond_pack_dual.py` | 旧入口已删除，使用 `examples/dashboard_dual.py`；实现移入 `dashboards/dual.py` |
| `update_packages/server.py` | 移至 `deploy/camera/server.py`，保持设备端脚本内容 |
| `update_packages/cftc_fish_camera_qa.md` | 移至 `deploy/camera/cftc_fish_camera_qa.md`，更新本地上传路径 |
| `requirement.txt` | 旧转发文件已删除，统一使用 `requirements.txt` |

业务代码应直接导入 `daimon_control`。根目录三个启动入口和规范命名的 dashboard 入口导入时引用同一个实现模块，避免出现两份运行状态。

`update_packages/` 的文件已迁入 `deploy/camera/`，旧目录及迁移占位文件已删除。Python 缓存和打包产生的 `build/` 可随时清理；安装中的 SDK `.egg-info` 元数据予以保留。

## 原生与历史脚本

三个 SDK 整体迁入 `sdks/`，内部的全部脚本、模型和库文件保持不变，不删除或归档。Python 导入名继续使用 `dm_gripper_cam_py`、`dm_gripper_tac_py`、`dm_lingkong_grip_sdk` 和 `dmrobotics`，不增加 `sdks.` 前缀。

已有环境的 editable 安装需要按根目录 README 重新注册路径。

- 夹爪 `test_grip.py`、`interactive_position.py`、`dual_interactive_position.py`、`cycle_test.py` 是 SDK 原生调试，继续使用 SDK 单位；日常使用推荐 `examples/gripper_*_control.py`。
- 相机 `test_remote_camera.py` 用于单相机画面与标定参数检查，`client.py` 用于模式查询与底层流测试；日常双相机预览推荐 `examples/dual_camera_viewer.py`。
- 触觉 `main.py`、`main_mp.py`、`main_gripper.py`、`demo.py`、`get_force.py`、`save_img.py`、`gen_*.py`、`slip_detection.py` 保留为原生实验与生成工具。具体硬件和 GPU 依赖需逐项确认后再决定归档。

第二阶段再讨论合并 server / dashboard 控制链路、替换缺失的外部接收器，以及归档经确认不再使用的原生脚本。

## SDK 路径迁移

| 原目录 | 新目录 |
|---|---|
| `dm_gripper_py/` | `sdks/dm_gripper_py/` |
| `dm_gripper_cam_py/` | `sdks/dm_gripper_cam_py/` |
| `dm_gripper_tac_py/` | `sdks/dm_gripper_tac_py/` |

根目录和示例入口直接导入已安装的应用包，不修改 `sys.path`。`_bootstrap.py` 已移除，须先按根目录 README 完成 editable 安装；安装时由 `pyproject.toml` 映射相机和触觉入口的位置。设备地址、配置路径及日常启动命令保持不变。

根目录 `README.md` 保留，架构图从 `pipeline.png` 移至 `docs/pipeline.png`，README 图片链接同步更新。
