"use strict";
const sides = ["left", "right"];
const activeStreams = new Map();
const connection = document.getElementById("connection");

function status(side, label, message, state = "waiting") {
  const badge = document.getElementById(`${side}-status`);
  badge.textContent = label;
  badge.dataset.state = state;
  const placeholder = document.getElementById(`${side}-placeholder`);
  placeholder.hidden = state === "live";
  document.getElementById(`${side}-message`).textContent = message;
  document.getElementById(`${side}-image`).hidden = state !== "live";
}

function stopStream(side) {
  const image = document.getElementById(`${side}-image`);
  image.removeAttribute("src");
  activeStreams.delete(side);
}

function showCamera(side, camera, enabled) {
  if (!enabled || !camera) {
    stopStream(side);
    document.getElementById(`${side}-source`).textContent = "相机 —";
    document.getElementById(`${side}-frames`).textContent = "尚未收到画面";
    status(side, "未启用", "相机图像服务未启用");
    return;
  }
  document.getElementById(`${side}-source`).textContent = `相机 ${camera.camera_host}`;
  document.getElementById(`${side}-frames`).textContent = `已接收 ${camera.frame_count.toLocaleString()} 帧`;
  const fresh = camera.has_frame && camera.last_frame_age !== null && camera.last_frame_age < 5;
  if (camera.error || !fresh) {
    stopStream(side);
    const label = camera.error ? "连接异常" : camera.has_frame ? "画面已暂停" : "等待画面";
    const message = camera.error || (camera.has_frame ? "暂未收到新画面，正在等待恢复…" : "相机连接中，等待第一帧…");
    status(side, label, message, camera.error || camera.has_frame ? "error" : "waiting");
    return;
  }
  if (!activeStreams.has(side)) {
    const image = document.getElementById(`${side}-image`);
    activeStreams.set(side, true);
    image.src = `/cameras/${side}/video?t=${Date.now()}`;
  }
  status(side, "实时画面", "", "live");
}

for (const side of sides) {
  document.getElementById(`${side}-image`).addEventListener("error", () => {
    activeStreams.delete(side);
    status(side, "正在重连", "视频连接中断，正在重新连接…", "error");
  });
}

document.getElementById("reconnect").addEventListener("click", () => window.location.reload());

async function refresh() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch("/cameras/state", {cache: "no-store", signal: controller.signal});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    connection.textContent = `已连接 · ${window.location.host}`;
    for (const side of sides) showCamera(side, data.cameras[side], data.enabled);
  } catch (_) {
    connection.textContent = "服务连接中断 · 正在重试";
    for (const side of sides) {
      stopStream(side);
      status(side, "连接已断开", "正在尝试重新连接服务…", "error");
    }
  } finally {
    clearTimeout(timeout);
    setTimeout(refresh, 1000);
  }
}

refresh();
