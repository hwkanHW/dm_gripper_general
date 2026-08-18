"""
Slip detection demo

- Connect to a tactile sensor via dmrobotics.Sensor
- Read deformation (2D flow) continuously
- Decide slip vs safe using a short history window

Output format:
    S2 | SLIP/SAFE | Coh: <coherence> | Valid: <valid_vector_count>
"""

from dmrobotics import Sensor, SensorOptions, Mode
import multiprocessing as mp
import time
from dmrobotics.extensions import TactileSlipDetector

if __name__ == "__main__":

    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    detector2 = TactileSlipDetector(slip_threshold=0.35)

    # NOTE: 'backend="cuda"' depends on your dmrobotics Python binding.
    opt_2 = SensorOptions(
        dev_id=2,
        backend="cuda",
        mode=Mode.STANDARD,
        enable_deformation=True
    )

    print("[Main] Connecting to sensor...")
    sensor2 = Sensor(opt_2)

    last_fid_2 = -1

    try:
        print("[Main] System ready. Monitoring slip...")
        while True:
            # Device status: 0=OK, 1=RESETTING, 2=DISCONNECTED
            if sensor2.getDevStatus() != 0:
                time.sleep(0.01)
                continue

            # Wait for a new frame
            if sensor2.wait_for_new(last_fid_2, timeout_ms=200):
                fid2, _ = sensor2.getRawImg()
                last_fid_2 = fid2

                _, def2 = sensor2.getDeformation2D()

                if def2 is not None:
                    is_slip2, c2, v2 = detector2.update(def2)
                    status = "SLIP" if is_slip2 else "SAFE"
                    print(f"S2 | {status} | Coh: {c2:.3f} | Valid: {v2:04d}", end="\r")

    except KeyboardInterrupt:
        print("\n[Main] Stopping...")
    finally:
        sensor2.disconnect()
        print("[Main] Completed")
