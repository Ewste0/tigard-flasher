"""Tigard SPC/MPC and SPI NOR programmer; live hardware only."""
import argparse
import json
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import chips
from .errors import DriverError
from .files import save_backup, write_new, sha256
from .flash import Flash
from .geometry import Block, backup_blocks, blocks_for, select_blocks
from .idcode import read_idcode
from .mpsse import MpsseTransport, DEFAULT_URL
from .session import TargetSession
from .spi import SpiNor, PARTS
from .security import parse_password, password_address, provision

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
                           password=parse_password(chips.get(args.chip), args.password_file.read_text()) if getattr(args, "password_file", None) else None) as session:
            print(f"Connected: IDCODE=0x{session.idcode.raw:08x}; target reset and halted.", file=sys.stderr)
            yield session.flash


def _operation(args):
    if not args.spi and chips.get(args.chip).read_only and args.action in MUTATING:
        raise ValueError("Selected target is a read-only profile")
    data = Path(args.file).read_bytes() if args.action in ("write", "program", "verify") else None
    size = len(data) if data is not None else args.length
    blocks = _blocks(args, args.address, size, whole=args.action == "erase")
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
    if data is not None:
        plan["sha256"] = sha256(data)
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
            data = flash.read(args.address, size)
            write_new(args.file, data)
            plan["sha256"] = sha256(data)
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
        with backend(args):
            emit({"target": args.chip, "operation": args.action, "executed": True,
                  "message": "Password supplied; debug session opened. An uncensored target cannot validate the password."})
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
    plan.update(executed=True, verified=True)
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
    parser.add_argument("--freq", type=integer, default=500000, help="TCK/SPI clock in Hz")
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
        p.add_argument("--address", required=True, type=integer, help="physical address (not file offset)")
        if action in ("read", "erase", "plan", "blank"):
            p.add_argument("--length", required=True, type=integer)
        if action in ("read", "write", "program", "verify"):
            p.add_argument("file", help="raw binary input/output")
        if action in MUTATING:
            p.add_argument("--execute", action="store_true", help="perform operation; otherwise only print plan")
            p.add_argument("--backup", type=Path, help="new directory for affected-block backups (OTP: touched ECC units only) and SHA-256 manifest")
        p.set_defaults(func=_operation, action=action)
        if action not in MUTATING:
            p.set_defaults(execute=False, backup=None)


def build_parser():
    parser = argparse.ArgumentParser(prog="spc5tool", description=__doc__)
    sub = parser.add_subparsers(required=True)
    gui = sub.add_parser("gui", help="open the local flasher UI for live Tigard hardware")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--workspace", type=Path, default=Path(".spc5tool-ui"))
    gui.add_argument("--allow-hardware", action="store_true", default=True, help=argparse.SUPPRESS)
    gui.add_argument("--mode", choices=("hardware",), default="hardware", help=argparse.SUPPRESS)
    gui.add_argument("--freq", type=integer, default=500000, help="initial interface clock in Hz")
    gui.add_argument("--open-browser", action="store_true", help="open the local page in your default browser")
    gui.add_argument("--url", default=DEFAULT_URL)
    gui.add_argument("--reset-pin", choices=("srst", "trst"), default="srst",
                     help="Tigard lead connected to MCU RESET (default: srst)")
    from .web import serve
    gui.set_defaults(func=serve)
    ident = sub.add_parser("idcode", help="read JTAG identification only (no MCU reset)")
    ident.add_argument("--url", default=DEFAULT_URL)
    ident.add_argument("--freq", type=integer, default=500000)
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
