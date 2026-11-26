"""Compatibility entry points must resolve from a different working directory."""
import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class EntrypointTests(unittest.TestCase):
    def test_compatibility_imports_share_implementation(self):
        sys.path.insert(0, str(ROOT / 'examples'))
        pairs = {
            'gripper_server': 'daimon_control.server.app',
            'gripper_gui_client': 'daimon_control.clients.gripper_gui',
            'img_client': 'daimon_control.clients.image_viewer',
            'dashboard_single': 'daimon_control.dashboards.single',
            'dashboard_dual': 'daimon_control.dashboards.dual',
        }
        for old, canonical in pairs.items():
            with self.subTest(module=old):
                self.assertIs(importlib.import_module(old), importlib.import_module(canonical))

    def test_source_commands_help_from_other_directory(self):
        scripts = [
            'gripper_server.py', 'gripper_gui_client.py', 'img_client.py',
            'examples/gripper_single_control.py', 'examples/gripper_dual_control.py',
            'examples/dual_camera_viewer.py', 'examples/tac.py',
            'examples/dashboard_single.py', 'examples/dashboard_dual.py',
        ]
        with tempfile.TemporaryDirectory() as directory:
            for script in scripts:
                with self.subTest(script=script):
                    result = subprocess.run([sys.executable, str(ROOT / script), '--help'],
                                            cwd=directory, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('usage:', result.stdout)

    def test_receiver_path_still_points_to_original_location(self):
        from daimon_control.dashboards.dual import DEFAULT_RECEIVER_PATH
        self.assertEqual(DEFAULT_RECEIVER_PATH, ROOT / 'examples/grip_signal_receiver.py')

    def test_sdk_modules_resolve_under_sdks(self):
        from dm_lingkong_grip_sdk import lingkong_grip_api
        from dm_gripper_cam_py import remote_camera
        from dm_gripper_tac_py import tac
        import dmrobotics
        for module, sdk in ((lingkong_grip_api, 'dm_gripper_py'),
                            (remote_camera, 'dm_gripper_cam_py'),
                            (tac, 'dm_gripper_tac_py'),
                            (dmrobotics, 'dm_gripper_tac_py')):
            with self.subTest(module=module.__name__):
                path = Path(module.__file__).resolve()
                self.assertIn(ROOT / 'sdks' / sdk, path.parents)
        for name in ('dm_gripper_py', 'dm_gripper_cam_py', 'dm_gripper_tac_py'):
            self.assertFalse((ROOT / name).exists())
