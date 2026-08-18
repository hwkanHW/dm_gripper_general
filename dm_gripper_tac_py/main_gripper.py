#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
dmrobotics multi-sensor visualization demo (one worker process per sensor)

What this script does
---------------------
- Starts one Python process per sensor (SENSORS_TO_USE list).
- Each sensor process connects to a remote device (gRPC address) and streams data
  back to the PC host (PC_HOST:pc_port).
- On Windows:
  - Uses a single process per sensor to both acquire data and render OpenCV windows
    (multiprocessing OpenCV visualization is often unstable on Windows).
- On non-Windows (Linux):
  - Each sensor worker spawns an additional visualization subprocess for
    deformation/shear/infer windows.
  - The main worker process continues to handle sensor I/O and sends frames to the
    visualization subprocess via a small multiprocessing queue.

Displayed channels
------------------
- Infer image (BGR/GRAY depending on your SDK binding)
- Deformation (2D flow) rendered as arrows
- Shear (2D curl) rendered as arrows
- Depth (float depth map scaled to 8-bit for preview)

Keyboard controls (per sensor)
------------------------------
- 'q' : quit the current sensor worker (closes its windows)
- 'r' : reset the sensor (requested either from the worker windows or from the
        visualizer subprocess on Linux)

Notes
-----
- The IPC queue is intentionally small to avoid backlog and slow shutdown.
- This demo is intended for interactive visualization. For headless usage, replace
  cv2.waitKey() with terminal key polling (termios + select) and disable imshow().
"""

import time
import multiprocessing as mp
import queue
import cv2
import numpy as np
import os

from dmrobotics import (
    Sensor,
    SensorOptions,
    Mode
)

from dmrobotics.utils import put_arrows_on_image

PC_HOST   = "192.168.127.100"
BASE_PORT = 60000

IS_WINDOWS = (os.name == "nt")

AUTO_RESET_ON_START = True
AUTO_RESET_WARMUP_FRAMES = 5
AUTO_RESET_SETTLE_S = 0.3

SENSORS_TO_USE = [
    {
        "name": "sensor_left",
        "dev_id": "left",
        "remote_addr": "192.168.127.10:50051",
        "pc_port": BASE_PORT + 0,
    },
    {
        "name": "sensor_right",
        "dev_id": "right",
        "remote_addr": "192.168.127.10:50052",
        "pc_port": BASE_PORT + 1,
    },
]


def auto_reset_unloaded_sensor(sensor: Sensor, name: str) -> None:
    """Set the current no-load sensor state as the baseline before visualization."""
    if not AUTO_RESET_ON_START:
        return

    print(f"[{name}] Startup no-load reset will run. Keep the sensor surfaces free of contact.")
    last_fid = -1
    time.sleep(AUTO_RESET_SETTLE_S)

    for _ in range(AUTO_RESET_WARMUP_FRAMES):
        try:
            if sensor.getDevStatus() != 0:
                time.sleep(0.1)
                continue
            if sensor.wait_for_new(last_fid, timeout_ms=500):
                fid, _ = sensor.getRawImg()
                if fid is not None:
                    last_fid = fid
        except Exception as e:
            print(f"[{name}] Startup warmup skipped one frame: {e}")
            break

    try:
        sensor.reset()
        print(f"[{name}] Startup no-load reset finished.")
    except Exception as e:
        print(f"[{name}] Startup no-load reset failed: {e}")


def visualizer_process(data_queue: mp.Queue, running_event, reset_event, name: str) -> None:
    """
    Dedicated visualization process (non-Windows only).

    Receives tuples from data_queue:
      - ("infer", img)
      - ("deformation", flow)
      - ("shear", curl)

    Hotkeys in the visualization windows:
      - 'q' : stop everything for this sensor
      - 'r' : request sensor reset (sets reset_event)
    """
    vis_canvas = None

    # Window names are prefixed with sensor name to distinguish cameras
    win_def   = f"deformation_{name}"
    win_shear = f"shear_{name}"
    win_infer = f"infer_{name}"

    try:
        while running_event.is_set():
            try:
                # Short timeout so we can react quickly to stop requests
                data = data_queue.get(timeout=0.05)
            except Exception:
                # Even if the queue is empty, still poll hotkeys
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    print(f"[{name}] Visualizer 'q' pressed.")
                    running_event.clear()
                    break
                elif key == ord("r"):
                    print(f"[{name}] Visualizer 'r' pressed.")
                    reset_event.set()
                continue

            img_type, img_data = data

            if img_type == "deformation":
                h, w = img_data.shape[:2]
                if vis_canvas is None or vis_canvas.shape[:2] != (h, w):
                    vis_canvas = np.zeros((h, w, 3), dtype=np.uint8)
                else:
                    vis_canvas.fill(0)

                vis = put_arrows_on_image(vis_canvas, img_data, step=16, scale=20.0)
                cv2.imshow(win_def, vis)

            elif img_type == "shear":
                if vis_canvas is None or vis_canvas.shape[:2] != img_data.shape[:2]:
                    vis_canvas = np.zeros(img_data.shape[:2] + (3,), dtype=np.uint8)
                else:
                    vis_canvas.fill(0)

                vis = put_arrows_on_image(vis_canvas, img_data, step=16, scale=20.0)
                cv2.imshow(win_shear, vis)

            elif img_type == "infer":
                cv2.imshow(win_infer, img_data)

            # Poll hotkeys from OpenCV windows
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print(f"[{name}] Visualizer 'q' pressed.")
                running_event.clear()
                break
            elif key == ord("r"):
                print(f"[{name}] Visualizer 'r' pressed.")
                reset_event.set()

    except Exception as e:
        print(f"[{name}] Visualizer error: {e}")
    finally:
        cv2.destroyAllWindows()
        print(f"[{name}] Visualizer exited.")


def run_one_sensor(desc: dict, stop_event: mp.Event) -> None:
    """
    One worker process per sensor.

    - On Windows: this process handles both acquisition and visualization.
    - On non-Windows: this process handles acquisition and spawns a separate
      visualization subprocess for deformation/shear/infer.
    """
    name = desc["name"]

    opt = SensorOptions(
        dev_id=desc["dev_id"],
        backend="Flux",              # remote + CUDA inference
        mode=Mode.STANDARD,
        show_fps=True,
        enable_raw=False,
        enable_deformation=True,
        enable_depth=True,
        enable_shear=True,
        enable_force=False,
        remote_addr=desc["remote_addr"],
        pc_host=PC_HOST,
        pc_port=desc["pc_port"],
    )

    print(f"[{name}] Connecting...")
    try:
        sensor = Sensor(opt)
    except Exception as e:
        print(f"[{name}] Sensor initialization failed for dev_id={opt.dev_id}: {e}")
        print(f"[{name}] If detect is enabled, inspect the sensor surface and rerun `dmrobotics init <serial>` if the sensor is intact.")
        return

    last_fid = -1
    frame_cnt = 0
    t0 = time.time()

    depth_win_name = f"depth_{name}"
    infer_win_name = f"infer_{name}"
    def_win_name   = f"deformation_{name}"
    shear_win_name = f"shear_{name}"

    def request_reset(source: str) -> None:
        print(f"[{name}] Sensor reset requested from {source}. Keep the sensor surface free of contact.")
        try:
            sensor.reset()
        except Exception as e:
            print(f"[{name}] Sensor reset failed: {e}")

    auto_reset_unloaded_sensor(sensor, name)

    # ============================
    # Windows: visualize in the same process
    # ============================
    if IS_WINDOWS:
        print(f"[{name}] Running in single-process mode on Windows (no vis subprocess).")
        running = True

        try:
            while running and not stop_event.is_set():
                # Device status check
                if sensor.getDevStatus() != 0:
                    k = cv2.waitKey(1) & 0xFF
                    if k == ord("q"):
                        running = False
                        break
                    elif k == ord("r"):
                        request_reset("status window")
                    time.sleep(0.01)
                    continue

                # Wait for a new frame
                got_new = sensor.wait_for_new(last_fid, timeout_ms=500)


                if not got_new:
                    k = cv2.waitKey(1) & 0xFF
                    if k == ord("q"):
                        running = False
                        break
                    elif k == ord("r"):
                        request_reset("main loop")
                    continue

                # Read raw/infer (infer is used for display)
                fid, raw = sensor.getRawImg()
                if raw is not None:
                    fid, inf = sensor.getInferImg()
                    if inf is not None and getattr(inf, "img", None) is not None:
                        cv2.imshow(infer_win_name, inf.img)

                # Deformation visualization
                _, deformation = sensor.getDeformation2D()
                if deformation is not None:
                    canvas_def = np.zeros(deformation.shape[:2] + (3,), dtype=np.uint8)
                    vis_def = put_arrows_on_image(canvas_def, deformation, step=16, scale=20.0)
                    cv2.imshow(def_win_name, vis_def)

                # Shear visualization
                _, shear = sensor.getShear()
                if shear is not None:
                    canvas_shear = np.zeros(shear.shape[:2] + (3,), dtype=np.uint8)
                    vis_shear = put_arrows_on_image(canvas_shear, shear, step=16, scale=20.0)
                    cv2.imshow(shear_win_name, vis_shear)

                # Depth preview
                fid, depth = sensor.getDepth()
                if depth is not None:
                    depth_img = (depth * 50).clip(0, 255).astype(np.uint8)
                    cv2.imshow(depth_win_name, depth_img)

                last_fid = fid

                # Hotkeys
                k = cv2.waitKey(1) & 0xFF
                if k == ord("q"):
                    print(f"[{name}] 'q' pressed in main loop.")
                    running = False
                    break
                elif k == ord("r"):
                    request_reset("main loop")

                # FPS
                frame_cnt += 1
                now = time.time()
                if now - t0 >= 1.0:
                    fps = frame_cnt / (now - t0)
                    print(f"[{name}] FPS: {fps:.2f}")
                    frame_cnt = 0
                    t0 = now

        except KeyboardInterrupt:
            print(f"[{name}] KeyboardInterrupt.")
            running = False

        finally:
            print(f"[{name}] Cleaning up (Windows)...")
            try:
                sensor.disconnect()
            except Exception:
                pass
            cv2.destroyAllWindows()
            print(f"[{name}] Worker process exited cleanly.")
        return

    # ==================================
    # Non-Windows: spawn a visualization subprocess
    # ==================================

    # Keep the queue small to avoid backlog
    data_queue = mp.Queue(maxsize=3)

    # Do not wait for the queue feeder thread on interpreter shutdown
    data_queue.cancel_join_thread()

    running_event = mp.Event()
    running_event.set()
    reset_event = mp.Event()

    # Start visualization subprocess (daemon)
    p_vis = mp.Process(
        target=visualizer_process,
        args=(data_queue, running_event, reset_event, name),
        name=f"vis-{name}",
        daemon=True
    )
    p_vis.start()

    try:
        while running_event.is_set() and not stop_event.is_set():
            # Handle reset requests from the visualizer subprocess
            if reset_event.is_set():
                request_reset("visualizer")
                reset_event.clear()

            # Device status check
            if sensor.getDevStatus() != 0:
                k = cv2.waitKey(1) & 0xFF
                if k == ord("q"):
                    running_event.clear()
                    break
                elif k == ord("r"):
                    request_reset("depth window")
                time.sleep(0.01)
                continue

            # Wait for a new frame
            got_new = sensor.wait_for_new(last_fid, timeout_ms=500)
            if opt.backend == "Flux":
                event, _ = sensor.getEvents()

            # Exit check
            if not running_event.is_set() or stop_event.is_set():
                break

            if not got_new:
                k = cv2.waitKey(1) & 0xFF
                if k == ord("q"):
                    running_event.clear()
                    break
                elif k == ord("r"):
                    request_reset("depth window")
                continue

            # Read raw/infer and forward infer to visualizer
            fid, raw = sensor.getRawImg()
            if raw is not None:
                fid, inf = sensor.getInferImg()
                if inf is not None and getattr(inf, "img", None) is not None:
                    try:
                        data_queue.put_nowait(("infer", inf.img.copy()))
                    except queue.Full:
                        pass
                    except Exception:
                        pass

            # Forward deformation to visualizer
            _, deformation = sensor.getDeformation2D()
            if deformation is not None:
                try:
                    data_queue.put_nowait(("deformation", deformation.copy()))
                except queue.Full:
                    pass

            # Forward shear to visualizer
            _, shear = sensor.getShear()
            if shear is not None:
                try:
                    data_queue.put_nowait(("shear", shear.copy()))
                except queue.Full:
                    pass

            # Depth preview is displayed in this worker process
            fid, depth = sensor.getDepth()
            last_fid = fid
            if depth is not None:
                depth_img = (depth * 50).clip(0, 255).astype(np.uint8)
                cv2.imshow(depth_win_name, depth_img)

            # Hotkeys (captured from the depth window)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                print(f"[{name}] 'q' pressed in depth window.")
                running_event.clear()
                break
            elif k == ord("r"):
                request_reset("depth window")

            # FPS
            frame_cnt += 1
            now = time.time()
            if now - t0 >= 1.0:
                fps = frame_cnt / (now - t0)
                print(f"[{name}] FPS: {fps:.2f}")
                frame_cnt = 0
                t0 = now

    except KeyboardInterrupt:
        print(f"[{name}] KeyboardInterrupt.")
        running_event.clear()

    finally:
        print(f"[{name}] Cleaning up (non-Windows)...")
        running_event.clear()

        # Disconnect sensor
        try:
            sensor.disconnect()
        except Exception:
            pass

        # Close windows owned by this worker process
        cv2.destroyAllWindows()

        # Close queue resources
        try:
            data_queue.close()
            data_queue.cancel_join_thread()
        except Exception:
            pass

        # Ensure visualizer subprocess exits
        p_vis.join(timeout=1.0)
        if p_vis.is_alive():
            print(f"[{name}] Terminating visualizer...")
            p_vis.terminate()
            p_vis.join()

        print(f"[{name}] Worker process exited cleanly.")


def main():
    # 'spawn' is safer for multiprocessing + CUDA; Windows also uses spawn by default
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    stop_event = mp.Event()
    procs = []
    for desc in SENSORS_TO_USE:
        p = mp.Process(
            target=run_one_sensor,
            args=(desc, stop_event),
            name=f"sensor-{desc['name']}",
        )
        p.start()
        procs.append(p)

    print("[main] All sensor workers started. Press 'q' in the corresponding window to close a sensor.")
    print("[main] The main process exits only after ALL sensor workers have exited.")

    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        print("\n[main] KeyboardInterrupt, requesting all workers to disconnect and exit...")
        stop_event.set()
        for p in procs:
            p.join(timeout=3.0)
        still_alive = [p for p in procs if p.is_alive()]
        if still_alive:
            print("[main] Some workers did not exit in time. Terminating the remaining workers...")
            for p in still_alive:
                p.terminate()
            for p in still_alive:
                p.join(timeout=1.0)
    finally:
        stop_event.set()
        print("[main] All workers finished. Exiting.")


if __name__ == "__main__":
    main()
