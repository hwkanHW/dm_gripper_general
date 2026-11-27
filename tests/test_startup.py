"""Check independent camera startup without opening sockets or hardware."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from daimon_control import config
from daimon_control.server import app as server


class StartupTests(unittest.TestCase):
    def test_explicit_disable_runs_images_without_main_listener(self):
        args = config.parse_server_args([])
        self.assertTrue(args.enable_server)
        self.assertTrue(args.enable_image_streams)
        with patch.object(server, 'GripperServer'), \
                patch.object(server, 'CameraMjpegServer') as camera, \
                patch.object(server, 'ViewerServer') as viewer, \
                patch.object(server, 'websockets', None), \
                patch.object(server.asyncio, 'run', side_effect=lambda coro: coro.close()) as run, \
                patch.object(server, 'runtime'), patch.object(server, 'camera_servers', []):
            server.main(['--disable-server'])
            self.assertEqual(camera.call_count, 2)
            self.assertEqual([call.kwargs['port'] for call in camera.call_args_list], [8021, 8022])
            viewer.assert_not_called()
            run.assert_called_once()

    def test_default_runs_control_listener_without_opening_browser(self):
        with patch.object(server, 'GripperServer'), \
                patch.object(server, 'CameraMjpegServer'), \
                patch.object(server, 'ViewerServer') as viewer, \
                patch.object(server, 'websockets', MagicMock()), \
                patch.object(server, 'runtime'), patch.object(server, 'camera_servers', []):
            server.main([])
            viewer.return_value.run.assert_called_once()
            self.assertEqual(viewer.call_args.args[0].port, 8020)
            self.assertFalse(viewer.call_args.kwargs['auto_open'])

    def test_standalone_cancellation_stops_services(self):
        runtime, stream = MagicMock(), MagicMock()
        event = MagicMock()
        event.wait = AsyncMock(side_effect=asyncio.CancelledError)
        with patch.object(server, 'runtime', runtime), \
                patch.object(server, 'camera_servers', [stream]), \
                patch.object(server.asyncio, 'Event', return_value=event):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(server.run_without_main_server())
        runtime.start.assert_called_once()
        stream.start.assert_called_once()
        stream.stop.assert_called_once()
        runtime.stop.assert_called_once()
