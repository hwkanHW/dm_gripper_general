"""Exercise ASGI routes using a fake runtime, without network or hardware."""
import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

from daimon_control.server import app as server
from daimon_control.server.image_stream import CameraMjpegServer


async def asgi_request(app, path, method='GET', payload=None):
    messages = []
    body = b'' if payload is None else json.dumps(payload).encode()
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
             'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
             'query_string': b'', 'root_path': '', 'server': ('test', 80),
             'client': ('test', 123), 'headers': [(b'content-type', b'application/json')]}

    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(message['status'] for message in messages if message['type'] == 'http.response.start')
    data = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
    return status, data


class ServerTests(unittest.TestCase):
    def test_http_command_validation_and_runtime(self):
        runtime = MagicMock()
        runtime.apply_command.return_value = {'success': True, 'position': 0.375}
        with patch.object(server, 'runtime', runtime):
            status, data = asyncio.run(asgi_request(server.app, '/command', 'POST',
                {'side': 'left', 'position': 0.375, 'speed': 0.3, 'torque': 0.2}))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)['position'], 0.375)
            self.assertEqual(runtime.apply_command.call_args.args[0].speed, 0.3)
            runtime.reset_mock()
            status, _ = asyncio.run(asgi_request(server.app, '/command', 'POST', {'position': 1000}))
            self.assertEqual(status, 422)
            runtime.apply_command.assert_not_called()

    def test_health_and_state(self):
        runtime = MagicMock()
        runtime.snapshot.return_value = {'grippers': {'left': {'position': 0.5}}}
        with patch.object(server, 'runtime', runtime):
            status, body = asyncio.run(asgi_request(server.app, '/health'))
            self.assertEqual((status, json.loads(body)), (200, {'status': 'ok'}))
            status, body = asyncio.run(asgi_request(server.app, '/state'))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['grippers']['left']['position'], 0.5)

    def test_image_routes_without_camera(self):
        stream = CameraMjpegServer(side='left', host='127.0.0.1', port=8021,
            camera_host='test', camera_port=50088, codec='MJPG', width=640,
            height=480, fps=30, jpeg_quality=80, client_ip='', bind_host='0.0.0.0', device='')
        status, body = asyncio.run(asgi_request(stream.app, '/health'))
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)['has_frame'])
        status, _ = asyncio.run(asgi_request(stream.app, '/snapshot.jpg'))
        self.assertEqual(status, 503)
        stream.latest_jpeg = b'test-jpeg'
        status, body = asyncio.run(asgi_request(stream.app, '/snapshot.jpg'))
        self.assertEqual((status, body), (200, b'test-jpeg'))

    def test_websocket_commands_and_validation(self):
        runtime = MagicMock()
        runtime.apply_command.return_value = {'success': True}
        events = iter([
            {'type': 'websocket.connect'},
            {'type': 'websocket.receive', 'text': json.dumps({'position': 0.375, 'speed': 0.3})},
            {'type': 'websocket.receive', 'text': json.dumps({'position': 1000})},
            {'type': 'websocket.disconnect', 'code': 1000},
        ])
        sent = []

        async def run():
            async def receive():
                return next(events)

            async def send(message):
                sent.append(message)

            scope = {'type': 'websocket', 'asgi': {'version': '3.0'}, 'scheme': 'ws',
                     'path': '/ws/command', 'raw_path': b'/ws/command', 'root_path': '',
                     'query_string': b'', 'headers': [], 'server': ('test', 80),
                     'client': ('test', 123), 'subprotocols': []}
            await server.app(scope, receive, send)

        with patch.object(server, 'runtime', runtime):
            asyncio.run(run())
        replies = [json.loads(event['text']) for event in sent if event['type'] == 'websocket.send']
        self.assertTrue(replies[0]['success'])
        self.assertFalse(replies[1]['success'])
        runtime.apply_command.assert_called_once()
        self.assertEqual(runtime.apply_command.call_args.args[0].position, 0.375)

    def test_lifespan_starts_and_stops_both_services(self):
        runtime, stream = MagicMock(), MagicMock()

        async def run():
            async with server.lifespan(server.app):
                runtime.start.assert_called_once()
                stream.start.assert_called_once()

        with patch.object(server, 'runtime', runtime), patch.object(server, 'camera_servers', [stream]):
            asyncio.run(run())
        runtime.stop.assert_called_once()
        stream.stop.assert_called_once()

    def test_viewer_page_assets_and_disabled_stream(self):
        status, body = asyncio.run(asgi_request(server.app, '/'))
        self.assertEqual(status, 200)
        self.assertIn('双夹爪相机', body.decode())
        for path in ('/viewer-assets/style.css', '/viewer-assets/app.js'):
            status, body = asyncio.run(asgi_request(server.app, path))
            self.assertEqual(status, 200)
            self.assertTrue(body)
        with patch.object(server, 'camera_servers', []):
            status, body = asyncio.run(asgi_request(server.app, '/cameras/state'))
            self.assertFalse(json.loads(body)['enabled'])
            status, body = asyncio.run(asgi_request(server.app, '/cameras/left/video'))
            self.assertEqual(status, 503)

    def test_viewer_uses_existing_camera_stream(self):
        stream = MagicMock()
        stream.side = 'left'
        stream.snapshot.return_value = {'camera_host': 'test', 'has_frame': True, 'frame_count': 123}
        stream._mjpeg_generator.return_value = iter([b'--frame\r\ntest-jpeg\r\n'])
        with patch.object(server, 'camera_servers', [stream]):
            status, body = asyncio.run(asgi_request(server.app, '/cameras/state'))
            state = json.loads(body)
            self.assertTrue(state['enabled'])
            self.assertEqual(state['cameras']['left']['frame_count'], 123)
            self.assertIsNone(state['cameras']['right'])
            status, body = asyncio.run(asgi_request(server.app, '/cameras/left/video'))
            self.assertEqual(status, 200)
            self.assertIn(b'test-jpeg', body)
            stream._mjpeg_generator.assert_called_once()
            status, _ = asyncio.run(asgi_request(server.app, '/cameras/unknown/video'))
            self.assertEqual(status, 422)
