"""Tigard SPC/MPC and SPI NOR programmer; live hardware only."""
import argparse
import json
import sys
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import chips
from .errors import DriverError
from .files import save_backup, write_new, sha256
from .images import decode_image, MAX_UPLOAD, SREC_SUFFIXES
from .flash import Flash
from .geometry import Block, backup_blocks, blocks_for, select_blocks
from .idcode import read_idcode
from .mpsse import MpsseTransport, DEFAULT_URL, DEFAULT_FREQUENCY
from .session import TargetSession
from .spi import SpiNor, PARTS
from .security import parse_password, password_address, provision, installation_message

ALIASES = {"mpc5607b": "spc560b64", "spc560b64l3": "spc560b64",
           "mpc5605b": "spc560b54", "mpc5606b": "spc560b60",
           "mpc5748g": "spc5748g", "spc5748gsmmj6": "spc5748g", "spc58nh": "spc58nh92"}
MUTATING = {"write", "erase", "program", "install-password"}


def integer(value):
    try:
        result = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use decimal or 0x-prefixed hexadecimal") from exc
    if result < 0:
        raise argparse.ArgumentTypeError("value must be nonnegative")
    return result


def chip_key(value):
    value = ALIASES.get(value.lower(), value.lower())
    if value not in chips.CHIPS:
        raise argparse.ArgumentTypeError(f"supported MCU profiles: {', '.join(chips.CHIPS)}")
    return value


def emit(value):
    print(json.dumps(value, indent=2))


def _blocks(args, address, size, whole=False):
    if args.spi:
        part = PARTS[args.chip]
        if address < 0 or size <= 0 or address + size > part.size:
            raise ValueError("range exceeds SPI array or is empty")
        if whole and (address % part.sector or size % part.sector):
            raise ValueError("SPI erase requires whole 4096-byte sectors")
        start = address // part.sector * part.sector
        end = (address + size + part.sector - 1) // part.sector * part.sector
        return tuple(Block(f"SPI.{a // part.sector}", "SPI", a, part.sector, 0, 0, 0)
                     for a in range(start, end, part.sector))
    return select_blocks(chips.get(args.chip), address, size, whole=whole)


def _info(args):
    if args.spi:
        part = PARTS[args.chip]
        emit({"part": part.key, "jedec": part.jedec.hex(), "size": part.size,
              "sector": part.sector, "page": part.page, "hardware_validated": False})
    else:
        chip = chips.get(args.chip)
        emit({"part": chip.part, "nxp_profile": chip.nxp_twin, "core": chip.core,
              "jtag_ir_bits": chip.jtagc_irlen, "hardware_validated": False,
              "blocks": [{"name": b.name, "address": f"0x{b.address:08x}", "size": b.size,
                          "selector_bank": b.bank, "selector_mask": f"0x{b.mask:08x}",
                          "kind": b.kind, "writable": b.writable} for b in blocks_for(chip)],
              "password_bits": chip.password_bits, "program_unit": chip.program_unit})
    return 0


def _usb_check(args):
    from .adapters import discover_tigards
    from .usb_backend import configure_usb, usb_error_message
    try:
        configure_usb()
        from pyftdi.usbtools import UsbTools
        backend = UsbTools.find_backend()
        print(f"USB library loaded: {type(backend).__module__}")
        if args.backend_only:
            return 0
        devices = discover_tigards()
    except (ImportError, ValueError, OSError) as exc:
        raise DriverError(usb_error_message(exc)) from exc
    emit(devices)
    print("USB descriptors only; no JTAG/reset or MCU memory access.")
    return 0 if devices else 1


@contextmanager
def backend(args):
    if args.spi:
        flash = SpiNor.open(PARTS[args.chip], args.url, args.freq)
        try:
            yield flash
        finally:
            flash.close()
    else:
        with TargetSession(chips.get(args.chip), args.url, args.freq, args.expect_idcode,
                           reset_pin=args.reset_pin,
                           password=parse_password(chips.get(args.chip), args.password_file.read_text()) if getattr(args, "password_file", None) else None,
                           prepare_flash=args.action != "authenticate",
                           fast_read=getattr(args, "fast_read", False),
                           read_delay_us=getattr(args, "read_delay_us", 10)) as session:
            print(f"Connected: IDCODE=0x{session.idcode.raw:08x}; target reset and halted.", file=sys.stderr)
            if session.authentication:
                print(session.authentication["message"], file=sys.stderr)
            yield session.flash


def _operation(args):
    if not args.spi and chips.get(args.chip).read_only and args.action in MUTATING:
        raise ValueError("Selected target is a read-only profile")
    data = None
    image = None
    if args.action in ("write", "program", "verify"):
        with Path(args.file).open('rb') as stream:
            image = decode_image(args.file, stream.read(MAX_UPLOAD + 1))
        args.address, data = image.select(getattr(args, "segment", None), args.address)
    if args.address is None:
        raise ValueError("this operation requires --address")
    if args.action == "read" and Path(args.file).suffix.lower() in SREC_SUFFIXES:
        raise ValueError("Read saves raw binary; use a .bin filename")
    size = len(data) if data is not None else args.length
    blocks = _blocks(args, args.address, size, whole=args.action == "erase")
    if getattr(args, "fast_read", False) is None:
        args.fast_read = (not args.spi and args.chip == "spc5748g" and args.action in ("read", "write", "program", "erase", "verify", "blank")
                          and args.freq >= 100000 and all(b.region == "CFLASH" for b in blocks))
    if getattr(args, "fast_read", False) and (args.chip != "spc5748g" or any(b.region != "CFLASH" for b in blocks)):
        raise ValueError("queued reads are currently limited to MPC5748G CFLASH")
    if not args.spi and args.action in MUTATING:
        Flash._check_blocks(blocks, erase=args.action == "erase")
        if args.action == "write" and any(b.kind == "otp" for b in blocks):
            raise ValueError("OTP requires program, not erase/preserve/write")
        unit = chips.get(args.chip).program_unit
        if args.action == "program" and (args.address % unit or size % unit):
            raise ValueError(f"MCU program requires complete aligned {unit}-byte ECC units")
    backups = (backup_blocks(chips.get(args.chip), args.address, size)
               if not args.spi and args.action == "program" else blocks)
    plan = {"target": args.chip, "operation": args.action, "address": f"0x{args.address:08x}",
            "bytes": size, "blocks": [{"name": b.name, "address": f"0x{b.address:08x}", "size": b.size} for b in blocks],
            "preserve_outside_image": args.action == "write", "hardware_validated": False}
    if args.action in MUTATING:
        plan["backup_ranges"] = [{"address": f"0x{b.address:08x}", "size": b.size} for b in backups]
    if args.action == "read":
        plan["fast_read"] = getattr(args, "fast_read", False)
    if data is not None:
        plan["sha256"] = sha256(data)
        if image.segments:
            plan["format"] = "srec"
            plan["segment"] = getattr(args, "segment", None) or 0
            plan["note"] = "Only the selected segment at its original address; other segments and gaps are preserved."
    if args.action == "plan" or (args.action in MUTATING and not args.execute):
        plan["executed"] = False
        emit(plan)
        return 0
    if args.action in MUTATING:
        if not args.backup:
            raise ValueError("--execute requires --backup NEW_DIRECTORY before modifying flash")
        if Path(args.backup).exists():
            raise ValueError("backup directory already exists; use a new path")
        if not args.spi and args.expect_idcode is None:
            raise ValueError("live modification requires --expect-idcode from your earlier IDCODE probe")
    if args.action == "read" and Path(args.file).exists():
        raise ValueError("output file already exists; use a new path")
    with backend(args) as flash:
        backup = lambda originals: save_backup(args.backup, args.chip, originals)
        if args.action == "read":
            started = time.monotonic()
            data = flash.read(args.address, size)
            elapsed = time.monotonic() - started
            write_new(args.file, data)
            plan["sha256"] = sha256(data)
            plan["read_seconds"] = round(elapsed, 3)
            plan["bytes_per_second"] = round(len(data) / max(elapsed, 1e-9))
            if getattr(args, "fast_read", False):
                plan["fast_read_fallback"] = bool(flash.memory.fast_read_fallback)
        elif args.action == "verify":
            flash.verify(args.address, data)
        elif args.action == "blank":
            flash.verify(args.address, b"\xff" * size)
        elif args.action == "write":
            flash.patch(args.address, data, backup)
        elif args.action in ("erase", "program"):
            backup([(b, flash.read(b.address, b.size)) for b in backups])
            if args.action == "erase":
                flash.erase(args.address, size)
            else:
                flash.program(args.address, data)
    plan["executed"] = True
    plan["mode"] = "hardware"
    if args.action in MUTATING or args.action in ("verify", "blank"):
        plan["verified"] = True
    emit(plan)
    return 0


def _password_operation(args):
    chip = chips.get(args.chip)
    if args.action == "authenticate":
        if not args.password_file:
            raise ValueError("authenticate requires --password-file")
        with backend(args) as flash:
            emit({"target": args.chip, "operation": args.action, "executed": True,
                  "authentication": flash.authentication, "message": flash.authentication["message"]})
        return 0
    if chip.algorithm not in ("c90-b", "c55-mpc5748", "c55-spc58h"):
        raise ValueError("Password provisioning awaits this family's verified security map")
    data = parse_password(chip, args.new_password_file.read_text(), installing=True)
    address = password_address(chip, args.slot)
    plan = {"target": args.chip, "operation": args.action, "slot": args.slot,
            "address": f"0x{address:08x}", "bytes": len(data), "executed": False,
            "note": "Preserves protection settings; does not activate censorship. OTP passwords cannot be replaced."}
    plan["backup_ranges"] = [{"address": f"0x{b.address:08x}", "size": b.size}
                             for b in backup_blocks(chip, address, len(data))]
    if not args.execute:
        emit(plan)
        return 0
    if not args.backup or args.backup.exists():
        raise ValueError("--execute requires --backup NEW_DIRECTORY")
    if args.expect_idcode is None:
        raise ValueError("live modification requires --expect-idcode")
    with backend(args) as flash:
        provision(flash, data, lambda originals: save_backup(args.backup, args.chip, originals), slot=args.slot)
    plan.update(executed=True, verified=True, message=installation_message(chip))
    emit(plan)
    return 0


def _idcode(args):
    with MpsseTransport.open(args.url, args.freq) as transport:
        result = read_idcode(transport)
    print(result)
    return 0 if result.valid_ieee else 1


def _probe(args):
    with backend(args) as flash:
        if args.spi:
            emit({"target": args.chip, "jedec": flash.identify().hex(), "mode": "hardware"})
        else:
            blocks = blocks_for(chips.get(args.chip))
            emit({"target": args.chip, "mode": "hardware", "hardware_validated": False,
                  "core_states": flash.memory.once.core_states,
                  "mcr": {f"0x{base:08x}": f"0x{flash.memory.read32(base):08x}" for base in sorted({b.controller for b in blocks})}})
    return 0


def _common(parser, spi):
    parser.add_argument("chip", choices=list(PARTS) if spi else None, type=str if spi else chip_key)
    parser.set_defaults(spi=spi)


def _connection(parser):
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--freq", type=integer, default=DEFAULT_FREQUENCY, help="TCK/SPI clock in Hz (default: 5000000)")
    parser.add_argument("--expect-idcode", type=integer, help="require this exact JTAG IDCODE (live MCU writes)")
    parser.add_argument("--password-file", type=Path, help="current JTAG password in a local hex-text file; never printed")
    parser.add_argument("--reset-pin", choices=("srst", "trst"), default="srst",
                        help="Tigard lead connected to MCU RESET; the other reset lead stays high for JCOMP")


def _commands(sub, spi=False):
    info = sub.add_parser("info", help="show supported geometry without hardware")
    _common(info, spi)
    info.set_defaults(func=_info)
    probe = sub.add_parser("probe", help="connect and inspect status (MCU: reset and halt)")
    _common(probe, spi)
    _connection(probe)
    probe.set_defaults(func=_probe, action="probe", execute=False)
    if not spi:
        for action in ("authenticate", "install-password"):
            p = sub.add_parser(action, help="open a password session" if action == "authenticate" else "install a password while preserving protection settings")
            _common(p, spi)
            _connection(p)
            if action == "install-password":
                p.add_argument("--new-password-file", required=True, type=Path, help="new password in a local hex-text file; never printed")
                p.add_argument("--slot", choices=("jtag", "group0", "group1", "group2", "group3"), default="jtag")
                p.add_argument("--execute", action="store_true")
                p.add_argument("--backup", type=Path)
            p.set_defaults(func=_password_operation, action=action)
    for action in ("plan", "read", "verify", "blank", "write", "program", "erase"):
        p = sub.add_parser(action, help={"write": "back up, preserve, erase, program and verify a binary patch",
                                        "program": "program an already-erased range and verify",
                                        "erase": "erase whole blocks; SHADOW preserves configuration; OTP cannot erase",
                                        "plan": "inspect affected blocks without opening USB",
                                        "read": "dump a mapped region to a new binary file",
                                        "blank": "check that every byte in the range reads FF",
                                        "verify": "compare flash with a binary file"}[action])
        _common(p, spi)
        _connection(p)
        p.add_argument("--address", required=action not in ("write", "program", "verify"), type=integer,
                       help="physical address; required for binary input, must match S-record address if given")
        if action in ("write", "program", "verify"):
            p.add_argument("--segment", type=integer, help="zero-based S-record segment; required for multiple segments")
        if action in ("read", "write", "program", "erase", "verify", "blank") and not spi:
            read_mode = p.add_mutually_exclusive_group()
            read_mode.add_argument("--fast-read", action="store_true", default=None,
                                   help="queued MPC5748G CFLASH access and quad-page programming (default at 100 kHz or faster)")
            read_mode.add_argument("--standard-read", action="store_false", dest="fast_read",
                                   help="use standard polling and single-unit programming")
            p.add_argument("--read-delay-us", type=integer, default=10, help="queued read settling delay, 1..1000 microseconds (default: 10)")
        if action in ("read", "erase", "plan", "blank"):
            p.add_argument("--length", required=True, type=integer)
        if action in ("read", "write", "program", "verify"):
            p.add_argument("file", help="binary output; binary or Motorola S-record input")
        if action in MUTATING:
            p.add_argument("--execute", action="store_true", help="perform operation; otherwise only print plan")
            p.add_argument("--backup", type=Path, help="new directory for affected-block backups (OTP: touched ECC units only) and SHA-256 manifest")
        p.set_defaults(func=_operation, action=action)
        if action not in MUTATING:
            p.set_defaults(execute=False, backup=None)


def build_parser():
    parser = argparse.ArgumentParser(prog="spc5tool", description=__doc__)
    sub = parser.add_subparsers(required=True)
    usb = sub.add_parser("usb-check", help="check USB library and Tigard descriptors without JTAG/reset")
    usb.add_argument("--backend-only", action="store_true", help="load the USB library without enumerating devices")
    usb.set_defaults(func=_usb_check)
    gui = sub.add_parser("gui", help="open the local flasher UI for live Tigard hardware")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--workspace", type=Path, default=Path(".spc5tool-ui"))
    gui.add_argument("--allow-hardware", action="store_true", default=True, help=argparse.SUPPRESS)
    gui.add_argument("--mode", choices=("hardware",), default="hardware", help=argparse.SUPPRESS)
    gui.add_argument("--freq", type=integer, default=DEFAULT_FREQUENCY, help="initial interface clock in Hz (default: 5000000)")
    gui.add_argument("--open-browser", action="store_true", help="open the local page in your default browser")
    gui.add_argument("--url", default=DEFAULT_URL)
    gui.add_argument("--reset-pin", choices=("srst", "trst"), default="srst",
                     help="Tigard lead connected to MCU RESET (default: srst)")
    from .web import serve
    gui.set_defaults(func=serve)
    ident = sub.add_parser("idcode", help="read JTAG identification only (no MCU reset)")
    ident.add_argument("--url", default=DEFAULT_URL)
    ident.add_argument("--freq", type=integer, default=DEFAULT_FREQUENCY)
    ident.set_defaults(func=_idcode)
    _commands(sub)
    spi = sub.add_parser("spi", help="Winbond SPI NOR on Tigard channel B")
    _commands(spi.add_subparsers(required=True), spi=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (DriverError, ValueError, OSError, zipfile.BadZipFile, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted; flash may be partial. Keep the backup and reconnect before retrying.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
