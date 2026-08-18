# cftc fish camera 运维问答脚本

## 问题 1：如何把 `/app/fish_cam_server` 目录下的 `python server.py` 配置为开机自启动？

可以用 `systemd` 做自启动。假设远程机器上项目路径是：

```bash
/app/fish_cam_server
```

启动命令是：

```bash
python server.py
```

在远程 `cftc` 上执行：

```bash
sudo nano /etc/systemd/system/fish-cam-server.service
```

写入：

```ini
[Unit]
Description=Fish Cam Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/app/fish_cam_server
ExecStart=/usr/bin/python3 /app/fish_cam_server/server.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
```

然后启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable fish-cam-server
sudo systemctl start fish-cam-server
```

查看状态：

```bash
sudo systemctl status fish-cam-server
```

看日志：

```bash
journalctl -u fish-cam-server -f
```

如果你平时必须用 `python` 而不是 `python3`，先确认路径：

```bash
which python
which python3
```

然后把 `ExecStart=/usr/bin/python3 ...` 改成实际输出的路径。

## 问题 2：如何查看当前系统里已经配置的开机自启动服务？

在远程 `cftc` 上看 systemd 自启动服务：

```bash
systemctl list-unit-files --type=service --state=enabled
```

如果想看刚才那个服务是否已自启动：

```bash
systemctl is-enabled fish-cam-server
systemctl status fish-cam-server
```

看所有正在运行的服务：

```bash
systemctl list-units --type=service --state=running
```

如果不是 systemd，也可以查这些老式自启动位置：

```bash
crontab -l
sudo crontab -l
ls /etc/init.d/
ls /etc/rc.local
ls /etc/cron.d/
```

查某个命令有没有被写进自启动：

```bash
grep -R "fish_cam_server\|server.py" /etc/systemd/system /lib/systemd/system /etc/cron* 2>/dev/null
```

## 问题 3：远程传输文件时，`scp` 命令应该怎么用？

`scp` 基本格式：

```bash
scp 本地文件 用户名@远程IP:远程路径
```

比如把本地文件传到远程 `cftc`：

```bash
scp ./server.py cftc@远程IP:/app/fish_cam_server/
```

把整个文件夹传过去：

```bash
scp -r ./fish_cam_server cftc@远程IP:/app/
```

从远程下载到本地：

```bash
scp cftc@远程IP:/app/fish_cam_server/server.py ./
```

如果远程 SSH 端口不是默认 `22`，比如是 `2222`：

```bash
scp -P 2222 ./server.py cftc@远程IP:/app/fish_cam_server/
```

常用例子：

```bash
scp -r -P 22 ./fish_cam_server cftc@192.168.1.100:/app/
```

注意：`scp` 里的端口参数是大写 `-P`，不是小写 `-p`。

## 问题 4：执行 `sudo systemctl disable --now fish_camera.service` 后，如何查看原来的 `fish_camera.service` 内容？

可以这样查看原来的 `fish_camera.service` 配置内容：

```bash
sudo systemctl cat fish_camera.service
```

如果服务文件还在，也可以看它的位置：

```bash
systemctl status fish_camera.service
```

里面通常会显示类似：

```text
Loaded: loaded (/etc/systemd/system/fish_camera.service; disabled; ...)
```

然后直接查看文件：

```bash
sudo nano /etc/systemd/system/fish_camera.service
```

或：

```bash
sudo cat /etc/systemd/system/fish_camera.service
```

如果你只是执行了：

```bash
sudo systemctl disable --now fish_camera.service
```

它只是“停止并取消开机自启”，一般不会删除 service 文件。所以 `systemctl cat` 还能看到。

如果提示找不到，可以搜一下：

```bash
sudo find /etc/systemd/system /lib/systemd/system /usr/lib/systemd/system -name "fish_camera.service"
```

查看是否还启用：

```bash
systemctl is-enabled fish_camera.service
systemctl status fish_camera.service
```

## 问题 5：如何重新启用并启动 `fish_camera.service`？

重新启用并立刻启动：

```bash
sudo systemctl enable --now fish_camera.service
```

确认是否启用成功：

```bash
systemctl is-enabled fish_camera.service
systemctl status fish_camera.service
```

如果你改过 service 文件，先执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fish_camera.service
```

看实时日志：

```bash
journalctl -u fish_camera.service -f
```
