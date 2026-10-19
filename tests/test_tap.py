"""Hardware-free validation of the transport + TAP + IDCODE path.

Run:  python3 -m pytest tests/ -v      (or)   python3 tests/test_tap.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spc5tool.mpsse import MpsseTransport
from spc5tool.tap import JtagTap, _path
from spc5tool.idcode import read_idcode, decode
from tests.fake_mpsse import FakeMpsse


def load_tests(loader, suite, pattern):
    import unittest
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for name, fn in sorted(globals().items())
                              if name.startswith("test_") and callable(fn))


def _run_idcode(idcode: int, ir_width: int = 5) -> int:
    tr = MpsseTransport(FakeMpsse(idcode, ir_width))
    return JtagTap(tr).read_idcode()


def test_idcode_roundtrip_various():
    # A plausible NXP e200 IDCODE (low 12 bits = 0x01D) plus edge patterns.
    for val in (0x0A81_601D, 0x5748_C01D, 0xFFFF_FFFF, 0x0000_0001,
                0x8000_0000, 0xDEAD_BE1D, 0xAAAA_AAAA, 0x5555_5555):
        got = _run_idcode(val)
        assert got == val, f"idcode 0x{val:08X} -> 0x{got:08X}"


def test_idcode_bit_alignment():
    # Walking-ones proves per-bit LSB-first alignment end to end.
    for i in range(32):
        val = 1 << i
        got = _run_idcode(val)
        assert got == val, f"bit {i}: 0x{val:08X} -> 0x{got:08X}"


def test_idcode_6bit_ir_chip():
    # 5748G JTAGC IR is 6 bits; IDCODE read is IR-independent but exercise it.
    val = 0x5748_001D
    assert _run_idcode(val, ir_width=6) == val


def test_scan_ir_roundtrip():
    # Load an IR value and confirm the capture pattern shifts out (LSB=1),
    # i.e. the IR shift path (used by OnCE later) works.
    tr = MpsseTransport(FakeMpsse(0x1234_5678, ir_width=5))
    tap = JtagTap(tr)
    tap.reset()
    captured = tap.scan_ir(5, 0b10001, read=True)   # ACCESS_AUX_TAP_ONCE pattern
    assert captured == 0x01, f"IR capture pattern = 0x{captured:02X}"


def test_decode_classifier():
    r = decode(0x0A81_601D)
    assert r.valid_ieee and r.is_nxp and (r.raw & 0xFFF) == 0x01D
    assert not decode(0x0000_0000).valid_ieee
    assert not decode(0x1234_5670).is_nxp        # low12 != 0x01D


def test_tms_paths():
    assert _path("IDLE", "DRSHIFT") == [1, 0, 0]
    assert _path("DREXIT1", "IDLE") == [1, 0]
    assert _path("IDLE", "IRSHIFT") == [1, 1, 0, 0]
    assert _path("RESET", "IDLE") == [0]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
