"""Production entry points cannot execute simulated operations or silently fall back."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spc5tool.cli import build_parser, main
from spc5tool.web import Workspace


class LiveOnlyTests(unittest.TestCase):
    def test_cli_defaults_and_removed_flags(self):
        parser = build_parser()
        args = parser.parse_args(['gui'])
        self.assertEqual(args.mode, 'hardware')
        self.assertTrue(args.allow_hardware)
        for command in (['gui'], ['idcode'], ['probe', 'mpc5748g'], ['spi', 'probe', 'w25q128jv']):
            self.assertEqual(parser.parse_args(command).freq, 5_000_000)
            self.assertEqual(parser.parse_args(command + ['--freq', '100000']).freq, 100000)
        for arguments in (['gui', '--mode', 'simulation'],
                          ['read', 'mpc5748g', 'unused.bin', '--address', '0xFA8000', '--length', '8', '--simulate'],
                          ['probe', 'mpc5748g', '--state', 'unused.zip']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                parser.parse_args(arguments)
            self.assertEqual(result.exception.code, 2)

    def test_api_rejects_simulation_before_opening_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            with patch('spc5tool.web.MpsseTransport.open') as opened:
                with self.assertRaisesRegex(ValueError, 'live hardware only'):
                    workspace.plan({'target': 'spc5748g', 'action': 'read', 'mode': 'simulation',
                                    'address': 0xFA8000, 'length': 8})
                opened.assert_not_called()
            self.assertFalse((Path(directory) / 'states').exists())

    def test_missing_hardware_fails_without_fake_dump(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'read.bin'
            with patch('spc5tool.session.MpsseTransport.open', side_effect=OSError('Tigard disconnected')) as opened:
                with contextlib.redirect_stderr(io.StringIO()) as errors:
                    code = main(['read', 'mpc5748g', str(output), '--address', '0xFA8000', '--length', '8'])
            self.assertEqual(code, 2)
            self.assertIn('Tigard disconnected', errors.getvalue())
            self.assertFalse(output.exists())
            opened.assert_called_once()

    def test_runtime_contains_no_simulator_or_demo_controls(self):
        import spc5tool
        root = Path(spc5tool.__file__).parent
        self.assertFalse((root / 'simulator.py').exists())
        for name in ('index.html', 'app.js'):
            source = (root / 'webui' / name).read_text()
            for removed in ('demoButton', 'emptyDemo', 'modeSelect', '/api/demo', 'simulation'):
                self.assertNotIn(removed, source)
