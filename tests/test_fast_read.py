"""Clocked Nexus read model: ready latency, stale RWD, bus/USB errors, no USB."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spc5tool.chips import SPC5748G, SPC58NH92
from spc5tool.cli import build_parser
from spc5tool.errors import NexusBusError
from spc5tool.memory import NexusMemory
from spc5tool.mpsse import MpsseBatch, MpsseError, MpsseTransport
from spc5tool.once import OnCE
from spc5tool.session import TargetSession
from spc5tool.tap import JtagTap
from spc5tool.web import Workspace
from tests.fake_mpsse import FakeMpsse, FakeTarget


class NexusTarget(FakeTarget):
    """AN4365 two-pass register access; independently clocked ready timing."""
    def __init__(self, latency=8, error_address=None):
        super().__init__(0x0988101D, 10)
        self.instruction = 0
        self.address_phase = True
        self.register = self.rwa = self.ticks = self.ready_at = 0
        self.write = False
        self.latency, self.error_address = latency, error_address
        self.accesses, self.control_words = [], []

    @staticmethod
    def data(address, size):
        return bytes(((a * 73) ^ (a >> 3) ^ (a >> 11)) & 255 for a in range(address, address + size))

    def clock(self, tms, tdi):
        self.ticks += 1
        return super().clock(tms, tdi)

    def _enter(self, nxt):
        if nxt == 'IRCAPTURE':
            self.ir_shift = 0x209
        elif nxt == 'IRUPDATE':
            self.instruction = self.ir_shift
            self.address_phase = True
        elif nxt == 'DRCAPTURE':
            if self.instruction != 0x7C:
                self.dr, self.dr_width = self.idcode, 32
            elif self.address_phase:
                self.dr, self.dr_width = 0, 8
            else:
                self.dr, self.dr_width = 0, 32
                if not self.write:
                    if self.register == 7:
                        self.dr = 0x10000004 | (2 if self.rwa == self.error_address else
                                               int(self.ticks >= self.ready_at))
                    elif self.register == 10:
                        # Deliberately wrong data when read before completion.
                        self.dr = (int.from_bytes(self.data(self.rwa, 4), 'little')
                                   if self.ticks >= self.ready_at else 0xA5A5A5A5)
                    else:
                        raise AssertionError('unexpected Nexus register read')
        elif nxt == 'DRUPDATE' and self.instruction == 0x7C:
            if self.address_phase:
                self.register, self.write = self.dr >> 1, bool(self.dr & 1)
            elif self.write:
                if self.register == 9:
                    self.rwa = self.dr
                elif self.register == 7:
                    self.control_words.append(self.dr)
                    if self.dr == 0:
                        self.ready_at = self.ticks
                        self.address_phase = True
                        self.state = nxt
                        return
                    if self.dr & 0x40000000:
                        raise AssertionError('read path attempted a bus write')
                    if self.dr & 0x3F3FFFFF != 0x10000004:
                        raise AssertionError('unexpected width, count, map or burst')
                    self.accesses.append(self.rwa)
                    self.ready_at = self.ticks + self.latency
                else:
                    raise AssertionError('read path wrote unexpected Nexus register')
            self.address_phase = not self.address_phase
        self.state = nxt


class CountingMpsse(FakeMpsse):
    def __init__(self, **kwargs):
        super().__init__(0)
        self.tgt = NexusTarget(**kwargs)
        self.writes = self.reads = 0

    def write_data(self, data):
        self.writes += 1
        return super().write_data(data)

    def read_data_bytes(self, size):
        self.reads += 1
        return super().read_data_bytes(size)


class FastReadTests(unittest.TestCase):
    def test_controller_read_one_exchange_and_polled_fallback(self):
        for latency in (8, 1000):
            memory, io = self.memory(latency=latency)
            result = memory.read32(0xFFFE0000)
            self.assertEqual(result, int.from_bytes(NexusTarget.data(0xFFFE0000, 4), 'big'))
            if latency == 8:
                self.assertEqual((io.writes, io.reads), (1, 1))
            else:
                self.assertTrue(memory.fast_read_fallback)
                self.assertGreater(io.reads, 1)

    def memory(self, fast=True, delay=32, **kwargs):
        io = CountingMpsse(**kwargs)
        tap = JtagTap(MpsseTransport(io))
        tap.reset()
        memory = NexusMemory(OnCE(tap, SPC5748G), fast_read=fast, read_idle_clocks=delay)
        io.reads = io.writes = 0
        return memory, io

    def test_exact_data_across_batches_endian_and_partial_words(self):
        for address, size in ((0xFA8000, 256), (0xFA8001, 777), (0xFFFFFFFD, 3), (7, 1)):
            with self.subTest(address=address, size=size):
                memory, io = self.memory()
                self.assertEqual(memory.read(address, size), NexusTarget.data(address, size))
                self.assertFalse(memory.fast_read_fallback)
                self.assertEqual(memory.once.tap.state, 'IDLE')
                self.assertEqual(io.tgt.state, 'IDLE')
                self.assertEqual(io.tgt.accesses, list(range(address & ~3, (address + size + 3) & ~3, 4)))
                self.assertLessEqual(io.reads, 4)

    def test_256_bytes_need_one_usb_reply_instead_of_128(self):
        slow, slow_io = self.memory(fast=False)
        fast, fast_io = self.memory()
        self.assertEqual(fast.read(0xFA8000, 256), slow.read(0xFA8000, 256))
        self.assertEqual(slow_io.reads, 128)
        self.assertEqual(fast_io.reads, 1)
        self.assertEqual(fast_io.writes, 1)
        self.assertFalse(fast.once.tap.io._low & 0x08)

    def test_short_delay_discards_stale_batch_and_disables_queue_for_session(self):
        memory, io = self.memory(latency=200, delay=1)
        self.assertEqual(memory.read(0xFA8000, 520), NexusTarget.data(0xFA8000, 520))
        self.assertTrue(memory.fast_read_fallback)
        self.assertFalse(memory.fast_read)
        self.assertGreater(io.reads, 1)
        self.assertEqual(io.tgt.accesses.count(0xFA8000), 2)
        self.assertIn(0, io.tgt.control_words)

    def test_otp_and_peripheral_reads_keep_standard_access(self):
        for address in (0x400120, 0xFFFF4000):
            memory, io = self.memory()
            self.assertEqual(memory.read(address, 32), NexusTarget.data(address, 32))
            self.assertEqual(io.reads, 16)

    def test_longer_idle_delay_keeps_queue_enabled(self):
        memory, io = self.memory(latency=200, delay=220)
        self.assertEqual(memory.read(0xFA8000, 256), NexusTarget.data(0xFA8000, 256))
        self.assertFalse(memory.fast_read_fallback)
        self.assertEqual(io.reads, 1)

    def test_bus_error_retains_exact_address_and_never_falls_back(self):
        address = 0xFA8000 + 19 * 4
        memory, _ = self.memory(error_address=address)
        with self.assertRaises(NexusBusError) as caught:
            memory.read(0xFA8000, 256)
        self.assertEqual(caught.exception.address, address)
        self.assertEqual(caught.exception.status, 0x10000006)
        self.assertFalse(memory.fast_read_fallback)

    def test_short_usb_read_fails_without_publishing_data(self):
        memory, io = self.memory()
        with patch.object(io, 'read_data_bytes', return_value=b''):
            with self.assertRaises(MpsseError):
                memory.read(0xFA8000, 256)
        self.assertFalse(memory.fast_read_fallback)

    def test_invalid_ranges_and_delay_do_not_touch_transport(self):
        memory, io = self.memory()
        for address, size in ((0, 0), (-1, 4), (0xFFFFFFFF, 2)):
            with self.assertRaises(ValueError):
                memory.read(address, size)
        self.assertEqual(io.writes, 0)
        with self.assertRaises(ValueError):
            NexusMemory(memory.once, read_idle_clocks=0)

    def test_batch_size_limits_and_single_execution(self):
        memory, io = self.memory()
        batch = MpsseBatch(memory.once.tap.io)
        tap = JtagTap(batch)
        tap.state = 'IDLE'
        tap.scan_ir(10, 0x7C)
        batch.execute()
        with self.assertRaises(MpsseError):
            batch.execute()
        batch = MpsseBatch(memory.once.tap.io)
        with self.assertRaises(ValueError):
            batch.scan(20000)
        self.assertEqual(io.reads, 0)

    def test_cli_auto_default_and_explicit_flags(self):
        args = build_parser().parse_args(['read', 'mpc5748g', '--address', '0xFA8000',
                                         '--length', '256', 'config.bin'])
        self.assertIsNone(args.fast_read)
        args = build_parser().parse_args(['read', 'mpc5748g', '--address', '0xFA8000',
                                         '--length', '256', '--fast-read', '--read-delay-us', '20', 'config.bin'])
        self.assertTrue(args.fast_read)
        self.assertEqual(args.read_delay_us, 20)
        args = build_parser().parse_args(['read', 'mpc5748g', '--address', '0xFA8000',
                                         '--length', '256', '--standard-read', 'config.bin'])
        self.assertFalse(args.fast_read)

    def test_cli_resolves_read_mode_before_opening_hardware(self):
        cases = [(['read', 'mpc5748g'], '0xFA8000', [], True),
                 (['read', 'mpc5748g'], '0xFA8000', ['--standard-read'], False),
                 (['read', 'mpc5748g'], '0xFA8000', ['--freq', '1000'], False),
                 (['read', 'mpc5748g'], '0x400100', [], False),
                 (['read', 'spc58nh92'], '0x1000000', [], False),
                 (['read', 'spc560b64'], '0', [], False),
                 (['spi', 'read', 'w25q128jv'], '0', [], False),
                 (['blank', 'mpc5748g'], '0xFA8000', [], True)]
        for command, address, options, expected in cases:
            with self.subTest(command=command, address=address, options=options), tempfile.TemporaryDirectory() as directory:
                output = [str(Path(directory) / 'dump.bin')] if 'read' in command else []
                args = build_parser().parse_args(command + ['--address', address, '--length', '256'] + options + output)
                with patch('spc5tool.cli.backend', side_effect=RuntimeError('backend reached')) as backend:
                    with self.assertRaisesRegex(RuntimeError, 'backend reached'):
                        args.func(args)
                backend.assert_called_once_with(args)
                self.assertEqual(getattr(args, 'fast_read', False), expected)

    def test_api_auto_default_only_for_eligible_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            request = dict(target='spc5748g', action='read', mode='hardware', address=0xFA8000, length=256)
            self.assertTrue(workspace.plan(request)['fast_read'])
            image = workspace.add_file('test.bin', bytes(256), 'image')
            cases = [{'fast_read': False}, {'frequency': 1000}, {'address': 0x400100},
                     {'target': 'spc58nh92', 'address': 0x1000000}, {'target': 'spc560b64', 'address': 0},
                     {'target': 'w25q128jv', 'address': 0}, {'action': 'identify'}]
            for update in cases:
                with self.subTest(update=update):
                    self.assertFalse(workspace.plan({**request, **update})['fast_read'])
            for action in ('read', 'blank', 'verify', 'write', 'erase', 'program'):
                plan = workspace.plan({**request, 'action': action, 'length': 0x4000, 'file_id': image['id'], 'expected_id': 0x0988101D})
                self.assertTrue(plan['fast_read'], action)

    def test_api_limits_queue_to_cflash_read(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            request = dict(target='spc5748g', action='read', mode='hardware',
                           address=0xFA8000, length=256, fast_read=True)
            plan = workspace.plan(request)
            self.assertTrue(plan['fast_read'])
            self.assertEqual(plan['read_delay_us'], 10)
            self.assertFalse(workspace.plan({**request, 'fast_read': False})['fast_read'])
            for update in ({'action': 'identify'}, {'target': 'spc58nh92'}, {'fast_read': 'true'}, {'fast_read': None},
                           {'address': 0x400100}, {'read_delay_us': 0}, {'read_delay_us': 1001},
                           {'frequency': 1000}):
                with self.subTest(update=update), self.assertRaises(ValueError):
                    workspace.plan({**request, **update})

    def test_session_checks_options_before_usb_and_enables_after_setup(self):
        with patch('spc5tool.session.MpsseTransport.open') as opened:
            with self.assertRaises(ValueError):
                TargetSession(SPC58NH92, fast_read=True)
            with self.assertRaises(ValueError):
                TargetSession(SPC5748G, fast_read=True, read_delay_us=0)
            opened.assert_not_called()
        with patch('spc5tool.session.MpsseTransport.open'), \
             patch('spc5tool.session.JtagTap') as tap, \
             patch('spc5tool.session.OnCE'), \
             patch('spc5tool.session.NexusMemory') as memory:
            tap.return_value.read_idcode.return_value = 0x0988101D
            memory.return_value.read32.return_value = 0
            memory.return_value.fast_read = False
            def check_setup(*_):
                self.assertFalse(memory.return_value.fast_read)
            memory.return_value.write32.side_effect = check_setup
            with TargetSession(SPC5748G, fast_read=True, frequency=5_000_000, read_delay_us=10):
                self.assertTrue(memory.return_value.fast_read)
                self.assertEqual(memory.return_value.read_idle_clocks, 50)


if __name__ == '__main__':
    unittest.main()
