"""Hardware-free regression checks for normalized controls."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]

from pydantic import ValidationError
from daimon_control import config as server


class ConfigTests(unittest.TestCase):
    def test_server_enable_config_and_overrides(self):
        config = json.loads(server.DEFAULT_CONFIG.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config['server']['enabled'] = True
            path.write_text(json.dumps(config))
            self.assertTrue(server.parse_server_args(['--config', str(path)]).enable_server)
            self.assertFalse(server.parse_server_args(['--config', str(path), '--disable-server']).enable_server)
            del config['server']['enabled']
            path.write_text(json.dumps(config))
            self.assertTrue(server.parse_server_args(['--config', str(path)]).enable_server)
            self.assertTrue(server.parse_server_args(['--config', str(path), '--enable-server']).enable_server)
            config['server']['enabled'] = 'false'
            path.write_text(json.dumps(config))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                server.parse_server_args(['--config', str(path)])

    def test_config_fraction_and_rejection(self):
            config = json.loads(server.DEFAULT_CONFIG.read_text())
            config['grippers'].update(speed=0.25, torque=0.4, min_pos=0.2, max_pos=0.8)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'config.json'
                path.write_text(json.dumps(config))
                args = server.parse_server_args(['--config', str(path), '--speed', '0.75'])
                self.assertEqual((args.speed, args.torque, args.min_pos, args.max_pos), (0.75, 0.4, 0.2, 0.8))
                config['grippers']['speed'] = 100
                path.write_text(json.dumps(config))
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    server.parse_server_args(['--config', str(path)])

    def test_default_config_from_other_directory(self):
        import os
        from daimon_control.config import DEFAULT_CONFIG, parse_server_args
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                args = parse_server_args([])
                self.assertEqual(args.config, ROOT / 'gripper_server_config.json')
                self.assertTrue(DEFAULT_CONFIG.is_file())
            finally:
                os.chdir(original_cwd)

    def test_invalid_file_reports_parser_error(self):
        from daimon_control.config import parse_server_args
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.json'
            for contents in (None, '{', '[]', '{}'):
                if contents is not None:
                    path.write_text(contents)
                with self.subTest(contents=contents), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                    parse_server_args(['--config', str(path)])
                self.assertEqual(result.exception.code, 2)

    def test_all_sections_and_enable_overrides(self):
        from daimon_control.config import parse_server_args
        config = json.loads(server.DEFAULT_CONFIG.read_text())
        config['server'].update(host='127.0.0.1', port=9000)
        config['image_streams'].update(enabled=False, left_port=9001)
        config['cameras'].update(width=320, height=240, fps=15)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(config))
            args = parse_server_args(['--config', str(path)])
            self.assertEqual((args.host, args.port, args.enable_image_streams, args.left_image_port,
                              args.camera_width, args.camera_height, args.camera_fps),
                             ('127.0.0.1', 9000, False, 9001, 320, 240, 15))
            self.assertTrue(parse_server_args(['--config', str(path), '--enable-image-streams']).enable_image_streams)
            self.assertFalse(parse_server_args(['--disable-image-streams']).enable_image_streams)

    def test_browser_config_and_cli_override(self):
        config = json.loads(server.DEFAULT_CONFIG.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config['server']['open_browser'] = False
            path.write_text(json.dumps(config))
            self.assertFalse(server.parse_server_args(['--config', str(path)]).open_browser)
            self.assertTrue(server.parse_server_args(['--config', str(path), '--open-browser']).open_browser)
            self.assertFalse(server.parse_server_args(['--no-open-browser']).open_browser)
            # Existing config files without the new field keep working.
            del config['server']['open_browser']
            path.write_text(json.dumps(config))
            self.assertFalse(server.parse_server_args(['--config', str(path)]).open_browser)
            config['server']['open_browser'] = 'false'
            path.write_text(json.dumps(config))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                server.parse_server_args(['--config', str(path)])
