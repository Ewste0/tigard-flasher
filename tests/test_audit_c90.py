"""C90 audit constraints from the MPC5607B RM rev7.1, not USB/silicon tests.

Tables 30-4/5 limit application OTP writes to the first 8 KiB of each TestFlash.
Sections 30.4.3/4 and 30.6.2 require one complete 64-bit ECC unit per pulse.
Figure 30-10/table 30-20 reserve LMS bit20; section 30.6.3 selects SHADOW by
the interlock address, independently of LMS. TSLK/STSLK are lock bits, not
erase selector bits. These constraints also match the supplied B54/B60/B64
P&E CFLASH/DFLASH/SHADOW headers (8-byte programming and lock keys).
"""
import unittest

from spc5tool.chips import SPC560B54, SPC560B60, SPC560B64
from spc5tool.errors import DriverError
from spc5tool.flash import Flash
from tests.simulator import SimMemory


C90_PROFILES = (SPC560B54, SPC560B60, SPC560B64)
OTP_WINDOWS = ((0x400000, 0xC3F88000), (0xC00000, 0xC3F8C000))


class PulseMemory(SimMemory):
    """Record the actual interlocks and locks at EHV's rising edge."""

    def __init__(self, chip):
        super().__init__(chip)
        self.pulses = []

    def _execute(self, base, controller):
        self.pulses.append((base, controller['mcr'] & 0x14,
                            controller['interlock'], dict(controller['pending']),
                            controller['regs'][4], controller['regs'][12]))
        super()._execute(base, controller)


class C90AuditTests(unittest.TestCase):
    def test_reserved_selector_bits_do_not_latch(self):
        for chip, high_mask in zip(C90_PROFILES, (0x3, 0xF, 0xFF)):
            with self.subTest(chip=chip.key):
                memory = SimMemory(chip)
                for address, mask in ((0xC3F88010, 0x0003003F),
                                      (0xC3F88014, high_mask),
                                      (0xC3F8C010, 0xF)):
                    memory.write32(address, 0xFFFFFFFF)
                    self.assertEqual(memory.read32(address), mask)

    def test_both_otp_window_edges_use_one_doubleword_pulse(self):
        for chip in C90_PROFILES:
            for window, controller in OTP_WINDOWS:
                with self.subTest(chip=chip.key, window=hex(window)):
                    memory = PulseMemory(chip)
                    flash = Flash(memory, chip)
                    # A neighboring bank and each unselected lock remain intact.
                    original_locks = {base: (c['regs'][4], c['regs'][12])
                                      for base, c in memory.controllers.items()}
                    for address in (window, window + 0x1FF8):
                        flash.program(address, bytes.fromhex('1020304050607080'))
                    self.assertEqual(len(memory.pulses), 2)
                    for pulse, address in zip(memory.pulses, (window, window + 0x1FF8)):
                        base, mode, interlock, words, lml, sll = pulse
                        self.assertEqual((base, mode, interlock), (controller, 0x10, address))
                        self.assertEqual(words, {address: 0x10203040, address + 4: 0x50607080})
                        self.assertEqual((lml, sll), (0xFFEFFFFF, 0xFFEFFFFF))
                    for base, original in original_locks.items():
                        c = memory.controllers[base]
                        self.assertEqual((c['regs'][4], c['regs'][12]), original)
                    self.assertEqual(memory.read(window + 8, 0x1FF0), b'\xff' * 0x1FF0)

    def test_otp_reserved_tail_and_partial_ecc_units_rejected_before_pulse(self):
        for chip in C90_PROFILES:
            for window, _ in OTP_WINDOWS:
                for address, length in ((window - 8, 16), (window + 0x1FF8, 16),
                                        (window + 0x2000, 8), (window + 4, 8),
                                        (window, 4), (window, 12)):
                    with self.subTest(chip=chip.key, address=hex(address), length=length):
                        memory = PulseMemory(chip)
                        with self.assertRaises(ValueError):
                            Flash(memory, chip).program(address, b'\x12' * length)
                        self.assertEqual(memory.pulses, [])
                        self.assertEqual(memory.log, [])

    def test_otp_preflight_covers_later_unit_before_any_pulse(self):
        for chip in C90_PROFILES:
            for window, _ in OTP_WINDOWS:
                with self.subTest(chip=chip.key, window=hex(window)):
                    memory = PulseMemory(chip)
                    memory.regions[window][8:16] = b'\x55' * 8
                    with self.assertRaisesRegex(DriverError, 'OTP units cannot be reused'):
                        Flash(memory, chip).program(window, b'\xAA' * 16)
                    self.assertEqual(memory.pulses, [])
                    self.assertEqual(memory.read(window, 8), b'\xff' * 8)

    def test_all_ones_does_not_consume_an_otp_program_pulse(self):
        for chip in C90_PROFILES:
            for window, _ in OTP_WINDOWS:
                with self.subTest(chip=chip.key, window=hex(window)):
                    memory = PulseMemory(chip)
                    flash = Flash(memory, chip)
                    flash.program(window, b'\xff' * 8)
                    self.assertEqual(memory.pulses, [])
                    flash.program(window, b'\x12' * 8)
                    self.assertEqual(len(memory.pulses), 1)
                    with self.assertRaisesRegex(DriverError, 'OTP units cannot be reused'):
                        flash.program(window, b'\x10' * 8)
                    self.assertEqual(len(memory.pulses), 1)

    def test_secondary_test_lock_stops_before_high_voltage(self):
        class SecondaryLockStuck(PulseMemory):
            def write32(self, address, value):
                if address in (0xC3F8800C, 0xC3F8C00C) and value != 0xC3C33333:
                    value |= 1 << 20
                super().write32(address, value)

        for chip in C90_PROFILES:
            for window, _ in OTP_WINDOWS:
                with self.subTest(chip=chip.key, window=hex(window)):
                    memory = SecondaryLockStuck(chip)
                    with self.assertRaisesRegex(DriverError, 'remains locked'):
                        Flash(memory, chip).program(window, b'\x12' * 8)
                    self.assertEqual(memory.pulses, [])
                    for controller in memory.controllers.values():
                        self.assertEqual(controller['regs'][4], 0xFFFFFFFF)
                        self.assertEqual(controller['regs'][12], 0xFFFFFFFF)

    def test_shadow_erase_has_no_test_lock_bit_in_lms(self):
        for chip in C90_PROFILES:
            with self.subTest(chip=chip.key):
                memory = PulseMemory(chip)
                memory.regions[0x200000][:0x3DD0] = b'\x12' * 0x3DD0
                original_tail = bytes(memory.regions[0x200000][0x3DD0:])
                # Stale normal-array selections must not make SHADOW ambiguous.
                memory.write32(0xC3F88010, 0x3003F)
                memory.write32(0xC3F88014, 0xFF)
                for address, region in memory.regions.items():
                    if address != 0x200000:
                        region[:8] = b'\xA5' * 8
                neighbors = {address: bytes(region) for address, region in memory.regions.items()
                             if address != 0x200000}
                Flash(memory, chip).erase(0x200000, 0x4000)
                erases = [pulse for pulse in memory.pulses if pulse[1] == 4]
                self.assertEqual([(p[0], p[2]) for p in erases], [(0xC3F88000, 0x200000)])
                self.assertNotIn((0xC3F88010, 1 << 20), memory.log)
                self.assertEqual(memory.regions[0x200000][:0x3DD0], b'\xff' * 0x3DD0)
                self.assertEqual(memory.regions[0x200000][0x3DD0:], original_tail)
                for address, original in neighbors.items():
                    self.assertEqual(memory.regions[address], original)

    def test_normal_erase_selects_each_bank_and_preserves_test_and_shadow(self):
        for chip, cflash_end in zip(C90_PROFILES, (0xC0000, 0x100000, 0x180000)):
            for address, size, controller, selector, mask in (
                    (0, 0x8000, 0xC3F88000, 0x10, 1),
                    (0x40000, 0x20000, 0xC3F88000, 0x10, 1 << 16),
                    (cflash_end - 0x20000, 0x20000, 0xC3F88000, 0x14,
                     1 << ((cflash_end - 0x80000) // 0x20000 - 1)),
                    (0x800000, 0x4000, 0xC3F8C000, 0x10, 1)):
                with self.subTest(chip=chip.key, address=hex(address)):
                    memory = PulseMemory(chip)
                    data, offset = memory._region(address, size)
                    data[offset:offset + size] = b'\x12' * size
                    for special in (0x200000, 0x400000, 0xC00000):
                        memory.regions[special][:8] = b'\xA5' * 8
                    specials = {base: bytes(memory.regions[base])
                                for base in (0x200000, 0x400000, 0xC00000)}
                    Flash(memory, chip).erase(address, size)
                    self.assertEqual(memory.read(address, size), b'\xff' * size)
                    self.assertEqual(len(memory.pulses), 1)
                    self.assertEqual(memory.pulses[0][:3], (controller, 4, address))
                    self.assertIn((controller + selector, mask), memory.log)
                    for base, original in specials.items():
                        self.assertEqual(memory.regions[base], original)


if __name__ == '__main__':
    unittest.main()
