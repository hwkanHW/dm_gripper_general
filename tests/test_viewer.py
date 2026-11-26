"""Browser launch is opt-out and happens only after the HTTP server is ready."""
import asyncio
import unittest
from unittest.mock import patch

import uvicorn
from daimon_control.server import app
from daimon_control.server.viewer import ViewerServer, open_viewer, viewer_url


class ViewerTests(unittest.TestCase):
    def test_local_urls(self):
        self.assertEqual(viewer_url('0.0.0.0', 8020), 'http://127.0.0.1:8020/')
        self.assertEqual(viewer_url('::', 9000), 'http://[::1]:9000/')
        self.assertEqual(viewer_url('192.168.1.13', 8020), 'http://192.168.1.13:8020/')

    def test_browser_opens_after_listener_starts(self):
        async def startup(instance, sockets=None):
            instance.started = True

        for auto_open in (True, False):
            instance = ViewerServer(uvicorn.Config(app.app, host='0.0.0.0', port=9000, log_config=None), auto_open=auto_open)
            with patch.object(uvicorn.Server, 'startup', startup), patch('daimon_control.server.viewer.threading.Thread') as thread:
                asyncio.run(instance.startup())
                if auto_open:
                    self.assertEqual(thread.call_args.kwargs['args'], ('http://127.0.0.1:9000/',))
                    thread.return_value.start.assert_called_once()
                else:
                    thread.assert_not_called()

    def test_failed_start_does_not_open_browser(self):
        async def startup(instance, sockets=None):
            instance.started = False

        instance = ViewerServer(uvicorn.Config(app.app, log_config=None), auto_open=True)
        with patch.object(uvicorn.Server, 'startup', startup), patch('daimon_control.server.viewer.threading.Thread') as thread:
            asyncio.run(instance.startup())
            thread.assert_not_called()

    def test_browser_unavailable_is_nonfatal(self):
        with patch('daimon_control.server.viewer.webbrowser.open_new_tab', return_value=False), self.assertLogs('uvicorn.error', level='WARNING'):
            open_viewer('http://127.0.0.1:8020/')
        with patch('daimon_control.server.viewer.webbrowser.open_new_tab', side_effect=OSError('no display')), self.assertLogs('uvicorn.error', level='WARNING'):
            open_viewer('http://127.0.0.1:8020/')
