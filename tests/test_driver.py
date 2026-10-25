import contextlib
import io
import json
import tempfile
import unittest
from itertools import chain, count, repeat
from pathlib import Path
from unittest.mock import Mock, patch

from spc5tool.chips import SPC560B64, SPC5748G
from spc5tool.cli import main
from spc5tool.errors import DriverError, OperationTimeout, VerificationError
from spc5tool.files import load_state, save_backup, save_state, sha256
from spc5tool.flash import Flash
from spc5tool.geometry import blocks_for, select_blocks
from spc5tool.idcode import decode
from spc5tool.memory import NexusMemory, OnceMemory
from spc5tool.mpsse import MpsseError, MpsseTransport
from spc5tool.once import OnCE, CpuScan
from tests.simulator import SimMemory, SimSpiPort
from spc5tool.spi import SpiNor, PARTS
from spc5tool.tap import JtagTap
from tests.fake_mpsse import FakeMpsse


class WireTests(unittest.TestCase):
    def test_invalid_idcodes(self):
        for value in (0, 0xffffffff, 0x12345678):
            self.assertFalse(decode(value).valid_ieee)
        self.assertTrue(decode(0x1234501d).valid_ieee)
        self.assertIn("STMicroelectronics", str(decode(0x01146041)))
        self.assertIn("NXP/Freescale", str(decode(0x0988101D)))

    def test_scan_vector_192_bits(self):
        backend = Mock()
        backend.write_data.side_effect = lambda b: len(b)
        backend.read_data_bytes.return_value = bytes(range(23)) + bytes([0xAA, 0x80])
        tr = MpsseTransport(backend)
        value = int.from_bytes(bytes(range(24)), 'little')
        result = tr.scan(192, value)
        command = backend.write_data.call_args.args[0]
        self.assertEqual(command[:3], b'\x39\x16\x00')
        self.assertEqual(command[3:26], bytes(range(23)))
        self.assertEqual(command[26:], b'\x3b\x06\x17\x6b\x00\x01\x87')
        expected = int.from_bytes(bytes(range(23)), 'little') | (0x55 << 184) | (1 << 191)
        self.assertEqual(result, expected)

    def test_wide_scans_and_write_only(self):
        for width in (1, 7, 8, 9, 31, 32, 64, 192, 256):
            backend = FakeMpsse(0xA5C3121D)
            tap = JtagTap(MpsseTransport(backend))
            tap.reset()
            tap.scan_dr(width, (1 << width) - 1, read=False)
            self.assertEqual(tap.state, 'IDLE')
            self.assertEqual(tap.read_idcode(), 0xA5C3121D)

    def test_short_io_and_closed(self):
        backend = Mock()
        backend.write_data.return_value = 0
        with self.assertRaises(MpsseError):
            MpsseTransport(backend).clock_tms([1])
        backend.write_data.side_effect = lambda b: len(b)
        backend.read_data_bytes.return_value = b''
        tr = MpsseTransport(backend)
        with self.assertRaises(MpsseError):
            tr.scan(32)
        tr.close()
        tr.close()
        backend.close.assert_called_once()
        with self.assertRaises(MpsseError):
            tr.scan(1)

    def test_partial_read_reassembly(self):
        backend = Mock()
        backend.write_data.side_effect = lambda b: len(b)
        backend.read_data_bytes.side_effect = [b'\x78', b'\x56', b'\x34', b'\x24', b'\x00']
        self.assertEqual(MpsseTransport(backend).scan(32), 0x12345678)

    def test_gpio_keeps_tms_and_reset_levels(self):
        backend = Mock()
        backend.write_data.side_effect = lambda b: len(b)
        tr = MpsseTransport(backend)
        tr.clock_tms([1, 0])
        tr.set_reset(srst=True)
        self.assertEqual(backend.write_data.call_args.args[0], b'\x80\x10\x3b')
        tr.set_reset(srst=False)
        self.assertEqual(backend.write_data.call_args.args[0], b'\x80\x30\x3b')

    def test_invalid_scan_no_io(self):
        backend = Mock()
        tr = MpsseTransport(backend)
        for width, value in ((0, 0), (8, 256), (32, -1), (600000, 0)):
            with self.assertRaises(ValueError):
                tr.scan(width, value)
        backend.write_data.assert_not_called()

    def test_reset_on_trst_leaves_srst_jcomp_high(self):
        backend = Mock()
        backend.write_data.side_effect = lambda b: len(b)
        transport = MpsseTransport(backend, reset_pin="trst")
        transport.clock_tms([1, 0])
        transport.set_reset(srst=True)
        transport.set_reset(srst=False)
        gpio = [c.args[0] for c in backend.write_data.call_args_list if c.args[0][0] == 0x80]
        self.assertEqual(gpio, [b'\x80\x20\x3b', b'\x80\x30\x3b'])
        self.assertTrue(all(c[1] & 0x20 for c in gpio))
        with self.assertRaises(ValueError):
            MpsseTransport(backend, reset_pin="unknown")


class OnceTests(unittest.TestCase):
    def test_idcode_reconnect_returns_chip_not_auxiliary_core_id(self):
        from tests.model_once import OnceTarget
        backend = FakeMpsse(0)
        backend.tgt = OnceTarget()
        tap = JtagTap(MpsseTransport(backend))
        tap.reset()
        tap.scan_ir(5, 0x11)
        # Reproduce the live failure: a TMS reset still exposes the core ID.
        tap.reset()
        self.assertTrue(backend.tgt.aux)
        self.assertEqual(tap.scan_dr(32), 0x07C5101D)
        self.assertEqual(tap.read_idcode(), 0x1234501D)
        self.assertFalse(backend.tgt.aux)
        self.assertFalse(backend.tgt.reset_asserted)
        self.assertEqual(backend.tgt.executed, [])

    def test_memory_roundtrip_through_real_mpsse_and_tap(self):
        from tests.model_once import OnceTarget
        backend = FakeMpsse(0)
        backend.tgt = OnceTarget()
        tap = JtagTap(MpsseTransport(backend))
        self.assertEqual(tap.read_idcode(), 0x1234501D)
        once = OnCE(tap, SPC560B64)
        once.attach()
        memory = OnceMemory(once)
        memory.initialize()
        self.assertEqual(backend.tgt.memory, {})  # self-test changes registers only
        backend.tgt.write_word(0x00800000, 0x01234567)
        self.assertEqual(memory.read32(0x00800000), 0x01234567)
        for address, value in ((0xC3F88004, 0x01234567), (0xC3F88000, 0x89ABCDEF)):
            memory.write32(address, value)
            self.assertEqual(memory.read32(address), value)
            self.assertEqual(backend.tgt.read_word(address), value)
        self.assertTrue(backend.tgt.halted)
        self.assertEqual(set(backend.tgt.fetches), {0xFFFFC004})
        self.assertNotIn(0x181F0900, backend.tgt.executed)

    def test_old_sram_bootstrap_fetches_uninitialized_memory(self):
        from tests.model_once import OnceTarget
        target = OnceTarget()
        target.halted = True
        target.scan = [0x40000000, 0, 0, 0x3FFFFFFC, 0x181F0900, 0x400]
        with self.assertRaisesRegex(AssertionError, 'fetch from uninitialized'):
            target.execute()
        self.assertEqual(target.memory, {})

    def make_once(self, chip=SPC560B64):
        tap = Mock()
        tap.scan_ir.return_value = 0x209
        tap.scan_dr.return_value = 0
        return OnCE(tap, chip), tap

    def test_register_vectors(self):
        once, tap = self.make_once()
        once.write_register(0x12, 5)
        tap.scan_ir.assert_called_with(10, 0x12)
        tap.scan_dr.assert_called_with(32, 5, read=False)
        once.read_register(0x31)
        tap.scan_ir.assert_called_with(10, 0x231)
        once.status()
        tap.scan_ir.assert_called_with(10, 0x211, read=True)

    def test_step_chain_and_go_update_dr(self):
        once, tap = self.make_once()
        once.step(0x501F0000, 0x12345678, force_operand=True)
        calls = tap.method_calls
        payload = 0x12345678 | (0xFFFFC000 << 96) | (0x501F0000 << 128) | (0x400 << 160)
        self.assertEqual(calls[0].args, (10, 0x10))
        self.assertEqual(calls[1].args, (192, payload))
        self.assertEqual(calls[2].args, (10, 0x111))
        self.assertEqual(calls[3].args, (1, 0))
        self.assertEqual(calls[4].args, (10, 0x211))
        self.assertEqual(calls[5].args, (10, 0x210))

    def test_instruction_fetch_requires_a_valid_address_before_scanning(self):
        once, tap = self.make_once(SPC5748G)
        with self.assertRaisesRegex(DriverError, 'no instruction fetch address'):
            once.step(0x1BE1D001)
        for pc in (0, -4, 0xFFFFC002, 0x100000000):
            with self.assertRaises(ValueError):
                once.step(0x1BE1D001, pc=pc)
        self.assertEqual(tap.method_calls, [])

    def test_z0h_initializes_dbcr0_after_setting_edm(self):
        once, _ = self.make_once()
        with patch.object(once, 'write_register', wraps=once.write_register) as writes:
            once.attach()
        self.assertEqual([c.args for c in writes.call_args_list],
                         [(0x12, 5), (0x31, 0x80000000), (0x31, 0x80000000),
                          (0x30, 0xFFFFFFFF), (0x12, 6)])

    def test_chain_word_order(self):
        values = (0x11223344, 0x55667788, 0xAABBCCDD, 0x40000000, 0x501F0000, 0x402)
        scan = CpuScan(*values)
        self.assertEqual(scan.pack().to_bytes(24, 'little'), b''.join(x.to_bytes(4, 'little') for x in values))
        self.assertEqual(CpuScan.unpack(scan.pack()), scan)

    def test_attach_560_and_5748_core_select(self):
        for chip, opcodes in ((SPC560B64, (0x11,)), (SPC5748G, (0x28, 0x29, 0x2a))):
            once, tap = self.make_once(chip)
            once.attach()
            selectors = [c.args[1] for c in tap.scan_ir.call_args_list if c.args[0] == chip.jtagc_irlen]
            self.assertEqual(selectors, list(opcodes) * 2 + [opcodes[0]])
            self.assertEqual(tap.io.set_reset.call_args_list[0].kwargs, {'srst': True})
            self.assertEqual(tap.io.set_reset.call_args_list[1].kwargs, {'srst': False})
            data_writes = [c.args[1] for c in tap.scan_dr.call_args_list if len(c.args) == 2]
            self.assertIn(5, data_writes)
            self.assertIn(6, data_writes)
            self.assertNotIn(0xA0000000, data_writes)

    def test_reset_release_on_attach_failure(self):
        once, tap = self.make_once()
        tap.scan_ir.side_effect = DriverError('USB failure')
        with self.assertRaises(DriverError):
            once.attach()
        tap.io.set_reset.assert_called_with(srst=False)

    def test_secondary_cores_held_in_reset_are_quiescent(self):
        once, tap = self.make_once(SPC5748G)
        once.status = Mock(side_effect=[0x209, 0x041, 0x041, 0x209])
        with patch.object(once, 'write_register', wraps=once.write_register) as writes:
            once.attach()
        self.assertEqual([s['state'] for s in once.core_states.values()],
                         ['halted', 'held in reset', 'held in reset'])
        self.assertEqual(sum(c.args[0] == 0x31 for c in writes.call_args_list), 1)
        # Keep the debug request armed on both inactive cores.
        self.assertEqual(sum(c.args == (0x12, 5) for c in writes.call_args_list), 3)

    def test_reset_primary_or_running_secondary_cannot_pass_attach(self):
        for core, statuses in ((0, [0x041]), (1, [0x209, 0x201]), (1, [0x209, 0x241])):
            with self.subTest(core=core, status=statuses[-1]):
                once, _ = self.make_once(SPC5748G)
                once.timeout = 1.0
                # The final status persists while polling, just as on a target.
                # Control elapsed time independently of the host timer resolution.
                once.status = Mock(side_effect=chain(statuses, repeat(statuses[-1])))
                clock = Mock()
                clock.monotonic.side_effect = count(0.0, 0.25)
                with patch('spc5tool.once.time', clock):
                    with self.assertRaisesRegex(DriverError, f'core {core} .* did not halt') as error:
                        once.attach()
                self.assertIsInstance(error.exception.__cause__, OperationTimeout)
                self.assertGreater(once.status.call_count, len(statuses))

    def test_invalid_and_timeout_status(self):
        for status in (0, 0x3FF, 0x309, 0x289):
            once, tap = self.make_once()
            tap.scan_ir.return_value = status
            with self.assertRaises(DriverError):
                once.wait_debug()
        once, tap = self.make_once()
        once.timeout = 0.00001
        tap.scan_ir.return_value = 0x201
        with self.assertRaises(OperationTimeout):
            once.wait_debug()
        tap.scan_ir.return_value = 0x249  # DEBUG alone is insufficient while reset remains asserted.
        with self.assertRaises(OperationTimeout):
            once.wait_debug()

    def test_z0h_initial_error_is_not_a_new_single_step_failure(self):
        once, tap = self.make_once()
        once.status = Mock(return_value=0x309)
        once.attach()
        self.assertTrue(once.core_states['core0']['prior_step_error'])
        self.assertEqual(once.core_states['core0']['state'], 'halted')
        # A new instruction MUST still fail on ERR; no data read/retry follows.
        with patch.object(once, 'read_register') as read:
            with self.assertRaisesRegex(DriverError, 'single-step 0x501F0000.*OSR=0x309'):
                once.step(0x501F0000, 0x800000, force_operand=True)
            read.assert_not_called()
        once.status.return_value = 0x209
        self.assertEqual(once.step(0x501F0000, 0x800000, force_operand=True), 0)

    def test_entry_does_not_ignore_checkstop_invalid_scan_or_reset(self):
        for status in (0, 0x3FF, 0x389):
            once, _ = self.make_once()
            once.status = Mock(return_value=status)
            with self.assertRaises(DriverError):
                once.attach()
        for status in (0x349, 0x109):  # Reset still asserted / clock not running.
            once, _ = self.make_once()
            once.status = Mock(return_value=status)
            clock = Mock()
            clock.monotonic.side_effect = count(0.0, 0.5)
            with patch('spc5tool.once.time', clock), self.assertRaisesRegex(DriverError, 'did not halt'):
                once.attach()
        once, _ = self.make_once(SPC5748G)
        once.status = Mock(return_value=0x309)
        with self.assertRaisesRegex(DriverError, 'instruction execution error'):
            once.attach()

    def test_nexus_register_vector(self):
        once, tap = self.make_once(SPC5748G)
        once.nexus_write(9, 0x1000000)
        self.assertEqual([c.args for c in tap.method_calls], [(10, 0x7c), (8, 0x13), (32, 0x1000000)])

    def test_nexus_word_order_and_start_sequence(self):
        once = Mock(chip=SPC5748G)
        once.nexus_read.side_effect = [0, 1, 0x78563412]
        memory = NexusMemory(once)
        self.assertEqual(memory.read32(0xF90000), 0x12345678)
        self.assertEqual([c.args for c in once.nexus_write.call_args_list], [(9, 0xF90000), (7, 0x90000004)])
        once.reset_mock()
        once.nexus_read.side_effect = [1, 0]
        memory.write32(0xFFFE0000, 0x12345678)
        self.assertEqual([c.args for c in once.nexus_write.call_args_list],
                         [(9, 0xFFFE0000), (7, 0xD0000004), (10, 0x78563412)])

    def test_nexus_bus_error_and_timeout(self):
        once = Mock(chip=SPC5748G)
        once.nexus_read.return_value = 2
        with self.assertRaises(DriverError):
            NexusMemory(once).read32(0)
        once.nexus_read.return_value = 0
        with self.assertRaises(OperationTimeout):
            NexusMemory(once, timeout=0.00001).read32(0)

    def test_vle_memory_vectors(self):
        once = Mock()
        once.step.return_value = 0x10203040
        memory = OnceMemory(once)
        self.assertEqual(memory.read32(0x800000), 0x10203040)
        once.step.assert_called_with(0x501F0000, 0x800000, force_operand=True)
        memory.write32(0xC3F88000, 0x10)
        self.assertEqual([c.args for c in once.step.call_args_list[-2:]], [(0x1BE1D000, 0x10), (0x543F0000, 0xC3F88000)])
        once.reset_mock()
        once.step.side_effect = [0x5AA5C33D, 0xA55A3CC3]
        memory.initialize()
        self.assertEqual([c.args for c in once.step.call_args_list],
                         [(0x1BE1D001, 0x5AA5C33C), (0x1BE1D001, 0xA55A3CC2)])

    def test_register_selftest_rejects_stale_results_and_execution_errors(self):
        for results in ([0x5AA5C33C], [0x5AA5C33D, 0xA55A3CC2],
                        [DriverError('OSR=0x309')]):
            once = Mock()
            once.step.side_effect = results
            with self.assertRaises(DriverError):
                OnceMemory(once).initialize()
            self.assertEqual(once.step.call_count, len(results))
            # No memory opcode may follow a failed test, and no step is retried.
            self.assertTrue(all(c.args[0] == 0x1BE1D001 for c in once.step.call_args_list))

    def test_unaligned_read_big_endian(self):
        once = Mock(chip=SPC5748G)
        once.nexus_read.side_effect = [1, 0x44332211, 1, 0x88776655]
        self.assertEqual(NexusMemory(once).read(1, 5), bytes.fromhex('2233445566'))


class FlashTests(unittest.TestCase):
    def make_flash(self, chip=SPC560B64):
        memory = SimMemory(chip)
        return Flash(memory, chip, timeout=0.002), memory

    def test_geometry_c90_mid_fields_and_c55_small_fields(self):
        c90 = {b.address: b for b in blocks_for(SPC560B64)}
        self.assertEqual(c90[0x40000].mask, 0x10000)
        self.assertEqual(c90[0x60000].mask, 0x20000)
        c55 = {b.address: b for b in blocks_for(SPC5748G)}
        for address, mask in ((0xF90000, 1), (0xFB0000, 0x100), (0xFC0000, 0x40000),
                              (0xFE0000, 0x400000), (0xFF0000, 0x1000000), (0x1540000, 0x200000)):
            self.assertEqual(c55[address].mask, mask)
        self.assertEqual(sum(b.size for b in c55.values() if b.region == "CFLASH"), 0x5f0000)

    def test_every_block_program_erase_and_lock_restore(self):
        for chip in (SPC560B64, SPC5748G):
            flash, memory = self.make_flash(chip)
            locks = {base: dict(c['regs']) for base, c in memory.controllers.items()}
            for block in blocks_for(chip):
                if block.kind != "flash" or not block.writable:
                    continue
                with self.subTest(chip=chip.key, block=block.name):
                    flash.program(block.address, b'\x01\x23\x45\x67\x89\xab\xcd\xef')
                    self.assertEqual(flash.read(block.address, 8), bytes.fromhex('0123456789abcdef'))
                    flash.erase(block.address, block.size)
                    self.assertEqual(flash.read(block.address, block.size), b'\xff' * block.size)
                    offsets = (0x10, 0x14, 0x18, 0x1c) if chip == SPC5748G else (
                        (4, 12) if block.region == 'DFLASH' else (4, 8, 12))
                    for offset in offsets:
                        self.assertEqual(memory.read32(block.controller + offset), locks[block.controller][offset])

    def test_c55_select_register_not_lock_register(self):
        flash, memory = self.make_flash(SPC5748G)
        memory.regions[0xF90000][0x70000:0x70008] = b'abcdefgh'
        flash.erase(0x1000000, 0x40000)
        self.assertIn((0xFFFE0040, 1), memory.log)
        self.assertIn((0xFFFE0038, 0), memory.log)
        self.assertEqual(flash.read(0x1000000, 8), b'\xff' * 8)

    def test_protected_holes_alignment_and_overflow(self):
        for chip, bad in ((SPC560B64, (0x204000, 0x402000, 0x180000)),
                          (SPC5748G, (0x3FFFF8, 0x404000, 0xF80000, 0x610000, 0xF8C000, 0x1580000))):
            flash, memory = self.make_flash(chip)
            for address in bad:
                with self.assertRaises(ValueError):
                    flash.program(address, b'12345678')
            with self.assertRaises(ValueError):
                select_blocks(chip, 0xffffffff, 2)
            base = blocks_for(chip)[0].address
            for address, size in ((base, 0), (base + 1, 8), (base, 7)):
                with self.assertRaises(ValueError):
                    flash.program(address, b'x' * size)
            with self.assertRaises(ValueError):
                flash.erase(base, 8)
            self.assertEqual(memory.log, [])

    def test_blank_preflight_checks_whole_image(self):
        flash, memory = self.make_flash()
        memory.regions[0][8:16] = b'12345678'
        with self.assertRaises(DriverError):
            flash.program(0, b'abcdefghABCDEFGH')
        self.assertEqual(memory.regions[0][:8], b'\xff' * 8)
        self.assertFalse(any(v & 1 for a, v in memory.log if a == 0xC3F88000))

    def test_patch_crosses_blocks_preserves_surroundings(self):
        flash, memory = self.make_flash()
        memory.regions[0][0x7FF0:0x8010] = bytes(range(32))
        snapshots = []
        flash.patch(0x7FFD, b'HELLO!!!', lambda b: snapshots.extend(b))
        self.assertEqual(len(snapshots), 2)
        expected = bytearray(range(32))
        expected[13:21] = b'HELLO!!!'
        self.assertEqual(flash.read(0x7FF0, 32), bytes(expected))
        self.assertEqual(snapshots[0][1][-16:], bytes(range(16)))

    def test_backup_failure_prevents_erase(self):
        flash, memory = self.make_flash()
        def fail(_):
            raise OSError('disk full')
        with self.assertRaises(OSError):
            flash.patch(0, b'changed!', fail)
        self.assertEqual(memory.regions[0][:8], b'\xff' * 8)
        self.assertFalse(any(a == 0xC3F88000 and v & 1 for a, v in memory.log))

    def test_timeout_peg_and_verify_failure_cleanup(self):
        for fault, expected in (('timeout', OperationTimeout), ('peg', DriverError), ('corrupt', VerificationError)):
            flash, memory = self.make_flash()
            memory.fault = fault
            with self.assertRaises(expected):
                flash.program(0, b'12345678')
            self.assertEqual(memory.read32(0xC3F88000) & 0x1f, 0)
            self.assertEqual(memory.read32(0xC3F88004), 0xffffffff)

    def test_ecc_read_error_and_busy_refused(self):
        flash, memory = self.make_flash()
        memory.read_error = True
        with self.assertRaisesRegex(DriverError, 'ECC'):
            flash.read(0, 8)
        memory.read_error = False
        memory.controllers[0xC3F88000]['mcr'] = 0x405
        before = len(memory.log)
        with self.assertRaisesRegex(DriverError, 'busy'):
            flash.erase(0, 0x8000)
        self.assertEqual(len(memory.log), before)

    def test_stuck_lock_refused(self):
        flash, memory = self.make_flash()
        memory.lock_stuck = True
        with self.assertRaisesRegex(DriverError, 'locked'):
            flash.program(0, b'12345678')
        self.assertEqual(memory.regions[0][:8], b'\xff' * 8)

    def test_noop_erase_detected_by_blank_check(self):
        flash, memory = self.make_flash()
        memory.regions[0][:8] = b'original'
        memory._unlocked = lambda block: False
        with self.assertRaises(VerificationError):
            flash.erase(0, 0x8000)

    def test_all_ones_program_does_not_consume_ecc_unit(self):
        flash, memory = self.make_flash()
        flash.program(0, b'\xff' * 8)
        self.assertNotIn((0xC3F88000, 0x11), memory.log)
        flash.program(0, b'12345678')

    def test_cleanup_failure_poisoned_session_cannot_continue(self):
        flash, memory = self.make_flash()
        original = memory.write32
        started = False
        def fail_abort(address, value):
            nonlocal started
            if address == 0xC3F88000 and value == 0x11:
                started = True
            elif started and address == 0xC3F88000 and value == 0x10:
                raise DriverError('USB lost during cleanup')
            return original(address, value)
        memory.write32 = fail_abort
        with self.assertRaisesRegex(DriverError, 'USB lost'):
            flash.program(0, b'12345678')
        self.assertTrue(flash.poisoned)
        before = len(memory.log)
        with self.assertRaisesRegex(DriverError, 'session failed'):
            flash.program(8, b'abcdefgh')
        self.assertEqual(before, len(memory.log))

    def test_interrupt_during_interlock_cleans_mode_and_locks(self):
        flash, memory = self.make_flash()
        original = memory.write32
        def interrupt(address, value):
            if address == 4:
                raise KeyboardInterrupt()
            return original(address, value)
        memory.write32 = interrupt
        with self.assertRaises(KeyboardInterrupt):
            flash.program(0, b'12345678')
        self.assertEqual(memory.read32(0xC3F88000) & 0x1f, 0)
        self.assertEqual(memory.read32(0xC3F88004), 0xffffffff)


class SpiTests(unittest.TestCase):
    def make_spi(self):
        port = SimSpiPort(PARTS['w25q128jv'])
        return SpiNor(port, port.part, timeout=0.001), port

    def test_identify_and_page_boundary(self):
        flash, port = self.make_spi()
        self.assertEqual(flash.identify(), bytes.fromhex('ef4018'))
        flash.program(255, b'1234567890')
        commands = [c for c, n in port.log if c[0] == 2]
        self.assertEqual(commands, [b'\x02\x00\x00\xff1', b'\x02\x00\x01\x00234567890'])
        self.assertEqual(flash.read(255, 10), b'1234567890')

    def test_patch_sector_preservation_and_erase(self):
        flash, port = self.make_spi()
        port.data[4090:4105] = b'0123456789abcde'
        backup = []
        flash.patch(4094, b'HELLO', lambda b: backup.extend(b))
        self.assertEqual(flash.read(4090, 15), b'0123HELLO9abcde')
        self.assertEqual(len(backup), 2)
        flash.erase(0, 4096)
        self.assertEqual(flash.read(0, 4096), b'\xff' * 4096)
        self.assertEqual(flash.read(4096, 9), b'LLO9abcde')

    def test_protected_refuses_status_register_writes(self):
        for index, mask in ((0, 4), (1, 0x40), (1, 0x80), (2, 4)):
            flash, port = self.make_spi()
            port.status[index] = mask
            with self.assertRaises(DriverError):
                flash.program(0, b'abc')
            self.assertFalse(any(c[0] in (1, 0x31, 0x11, 2) for c, _ in port.log))

    def test_timeout_wel_verify_and_id_errors(self):
        flash, port = self.make_spi()
        port.stuck_busy = True
        with self.assertRaises(OperationTimeout):
            flash.read(0, 8)
        port.stuck_busy = False
        port.reject_wren = True
        with self.assertRaisesRegex(DriverError, 'latch'):
            flash.program(0, b'abc')
        port.reject_wren = False
        port.corrupt = True
        with self.assertRaises(VerificationError):
            flash.program(0, b'abc')
        with patch.object(port, 'exchange', return_value=b'\x00' * 3):
            with self.assertRaisesRegex(DriverError, 'JEDEC'):
                flash.identify()

    def test_range_and_sector_boundaries(self):
        flash, port = self.make_spi()
        for a, n in ((0, 0), (0x1000000, 1), (0xffffff, 2)):
            with self.assertRaises(ValueError):
                flash.read(a, n)
        with self.assertRaises(ValueError):
            flash.erase(1, 4096)
        self.assertEqual(port.log, [])


class CliTests(unittest.TestCase):
    def setUp(self):
        from tests.backends import flash_model
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        backend = patch('spc5tool.cli.backend', side_effect=lambda args: flash_model(
            args.chip, Path(temp.name) / (args.chip + '.zip')))
        backend.start()
        self.addCleanup(backend.stop)

    def call(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = main([str(a) for a in args])
        return result, output.getvalue(), errors.getvalue()

    def test_dry_run_and_validation_never_open_usb(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(MpsseTransport, 'open', side_effect=AssertionError('USB opened')):
            path = Path(temporary) / 'patch.bin'
            path.write_bytes(b'12345678')
            code, output, _ = self.call(['write', 'mpc5607b', path, '--address', '0'])
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(output)['executed'])
            code, _, _ = self.call(['write', 'mpc5607b', path, '--address', '0', '--execute'])
            self.assertEqual(code, 2)
            code, _, _ = self.call(['write', 'mpc5607b', path, '--address', '0', '--execute', '--backup', Path(temporary) / 'backup'])
            self.assertEqual(code, 2)

    def test_cli_persistent_write_verify_read_and_backup(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(MpsseTransport, 'open', side_effect=AssertionError('USB opened')):
            root = Path(temporary)
            source, state, backup, output = (root / n for n in ('source.bin', 'sim.zip', 'backup', 'dump.bin'))
            source.write_bytes(b'OFFLINE-TEST')
            common = ['--expect-idcode', '0x0988101D', '--address', '0xF93FFD']
            code, _, error = self.call(['write', 'spc5748g', source, *common, '--execute', '--backup', backup])
            self.assertEqual(code, 0, error)
            manifest = json.loads((backup / 'manifest.json').read_text())
            self.assertEqual(len(manifest['blocks']), 2)
            for entry in manifest['blocks']:
                self.assertEqual(sha256((backup / entry['file']).read_bytes()), entry['sha256'])
            code, _, error = self.call(['verify', 'spc5748g', source, *common])
            self.assertEqual(code, 0, error)
            code, _, error = self.call(['read', 'spc5748g', output, '--length', str(source.stat().st_size), *common])
            self.assertEqual(code, 0, error)
            self.assertEqual(output.read_bytes(), source.read_bytes())
            code, _, _ = self.call(['read', 'spc5748g', output, '--length', '8', *common])
            self.assertEqual(code, 2)

    def test_spi_cli_offline_workflow(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(SpiNor, 'open', side_effect=AssertionError('USB opened')):
            root = Path(temporary)
            source = root / 'input.bin'
            source.write_bytes(b'spi-test')
            common = ['--address', '255']
            code, _, error = self.call(['spi', 'write', 'w25q128jv', source, *common, '--execute', '--backup', root / 'backup'])
            self.assertEqual(code, 0, error)
            code, _, error = self.call(['spi', 'verify', 'w25q128jv', source, *common])
            self.assertEqual(code, 0, error)

    def test_state_target_mismatch_and_backup_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            memory = SimMemory(SPC560B64)
            state = root / 'sim.zip'
            save_state(state, 'spc560b64', memory.regions)
            with self.assertRaises(ValueError):
                load_state(state, 'spc5748g', memory.regions)
            block = blocks_for(SPC560B64)[0]
            save_backup(root / 'backup', 'spc560b64', [(block, b'\xff' * block.size)])
            with self.assertRaises(FileExistsError):
                save_backup(root / 'backup', 'spc560b64', [(block, b'\x00' * block.size)])


if __name__ == '__main__':
    unittest.main()
