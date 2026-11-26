"""Hardware-free regression checks for normalized controls."""
import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch


from pydantic import ValidationError
from daimon_control.server import app as server
from daimon_control.clients import gripper_gui as gui
from daimon_control.clients import direct_control as direct
from daimon_control.dashboards import single, dual


class NormalizedControlTests(unittest.TestCase):
    def test_sdk_mapping_and_feedback(self):
            grip = MagicMock()
            grip.move_to_pos.return_value = True
            runtime = server.GripperRuntime('left', 'test:55551', speed=0.5,
                                           torque=0.5, min_pos=0, max_pos=1)
            runtime.grip = grip
            runtime.connected = True
            for position, native in [(0, 0), (0.375, 375), (1, 1000)]:
                result = runtime.apply(server.GripperCommand(position=position, speed=0.5, torque=0.3))
                self.assertEqual(result['position'], position)
                grip.move_to_pos.assert_called_with(native)
                grip.set_speed.assert_called_with(50)
                grip.set_torque_limit.assert_called_with(30)
            runtime.apply(server.GripperCommand(position=0.5, speed=0, torque=0))
            grip.set_speed.assert_called_with(10)
            grip.set_torque_limit.assert_called_with(10)
            grip.read_pos.return_value = 375
            grip.read_torque_limit.return_value = 30
            self.assertEqual(runtime.snapshot()['position'], 0.375)
            self.assertEqual(runtime.snapshot()['torque_limit'], 0.3)
            grip.read_pos.return_value = -1
            self.assertIsNone(runtime.snapshot()['position'])

    def test_api_rejects_old_units_and_nonfinite_values(self):
            for key, invalid in [('position', 1000), ('speed', 50), ('torque', -0.1),
                                 ('position', float('nan')), ('speed', float('inf'))]:
                payload = {'position': 0.5, key: invalid}
                with self.assertRaises(ValidationError):
                    server.GripperCommand(**payload)

    def test_gui_payload_and_slider_preserve_fraction(self):
            client = gui.GripperRemoteClient.__new__(gui.GripperRemoteClient)
            client.command_ws = MagicMock()
            client.command_ws.recv.return_value = '{"success": true}'
            client.send_command('left', 0.375, 0.5, 0.3)
            payload = json.loads(client.command_ws.send.call_args.args[0])
            self.assertEqual(payload, {'side': 'left', 'position': 0.375, 'speed': 0.5, 'torque': 0.3})
            app = gui.GripperGui.__new__(gui.GripperGui)
            app.args = gui.build_arg_parser().parse_args(['--min-pos', '0.2', '--max-pos', '0.8'])
            self.assertAlmostEqual(app.position_from_x(50, (0, 0, 100, 10)), 0.5)

    def test_direct_and_single_dashboard(self):
            args = single.normalize_args(single.build_argparser().parse_args([]))
            gripper = direct.DirectGripper(direct.GripperEndpoint('test', 'test'), single.direct_gripper_args(args))
            gripper.grip = MagicMock()
            single.run_direct_gripper_command(gripper, 'move 0.375', args)
            gripper.grip.move_to_pos.assert_called_with(375)
            gripper.grip.set_speed.assert_called_with(40)
            gripper.grip.set_torque_limit.assert_called_with(20)

    def test_dual_dashboard_native_receiver_boundary(self):
            args = dual.normalize_args(dual.build_argparser().parse_args([]))
            command = dual.receiver_command(args, 'test:55551', 9000)
            self.assertEqual(command[command.index('--min-pos') + 1], '300')
            self.assertEqual(command[command.index('--release-speed') + 1], '40')
            with patch.object(dual, 'send_one_gripper_signal', return_value='OK') as send:
                dual.send_gripper_signal_to('move 0.375', args)
                self.assertEqual(send.call_args.args[0], 'move 375')
