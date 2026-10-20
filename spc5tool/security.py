"""Device-specific password encoding and provisioning; no password recovery.

Input order is increasing flash address: NVPWD0 then NVPWD1 for C90,
and the eight UTEST words in increasing address order for C55.
Passwords must never be included in plans, logs, status snapshots or URLs.
"""
import re
from .errors import VerificationError


def parse_password(chip, value, *, installing=False):
    if not isinstance(value, str):
        raise ValueError("password must be hexadecimal text")
    text = re.sub(r"\s", "", value)
    digits = chip.password_bits // 4
    if not re.fullmatch(rf"[0-9a-fA-F]{{{digits}}}", text):
        raise ValueError(f"{chip.part} requires exactly {digits} hexadecimal digits ({chip.password_bits} bits)")
    data = bytes.fromhex(text)
    if installing:
        if chip.password_bits == 64:
            validate_c90_password(data)
        elif data == b"\xff" * len(data):
            raise ValueError("an all-FF password is indistinguishable from erased OTP")
    return data


def validate_c90_password(data):
    if len(data) != 8 or any(data[i:i + 2] in (b"\x00\x00", b"\xff\xff") for i in range(0, 8, 2)):
        raise ValueError("SPC560 password: each 16-bit halfword must contain both a zero and a one")


def validate_shadow(data):
    if len(data) != 0x4000:
        raise ValueError("SHADOW validation needs the complete 16 KiB sector")
    a = int.from_bytes(data[0x3DE0:0x3DE4], "big")
    b = int.from_bytes(data[0x3DE4:0x3DE8], "big")
    if a != b or a >> 16 != 0x55AA:
        raise ValueError("SHADOW censorship words must match and preserve serial control 0x55AA; this image would lock out the target")
    if a & 0xFFFF != 0x55AA:
        validate_c90_password(data[0x3DD8:0x3DE0])


def password_address(chip, slot="jtag"):
    if chip.algorithm == "c90-b":
        if slot != "jtag":
            raise ValueError("SPC560 has one censorship password, no PASS groups")
        return 0x203DD8
    if slot == "jtag":
        return 0x400120
    if slot in ("group0", "group1", "group2", "group3"):
        return 0x400140 + int(slot[-1]) * 32
    raise ValueError("unknown password slot")


def provision(flash, data, backup, *, slot="jtag"):
    parse_password(flash.chip, data.hex(), installing=True)
    if flash.chip.algorithm not in ("c90-b", "c55-mpc5748", "c55-spc58h"):
        raise ValueError("Password provisioning awaits this family's verified security map")
    address = password_address(flash.chip, slot)
    if len(data) * 8 != flash.chip.password_bits:
        raise ValueError("wrong password length")
    try:
        if flash.chip.algorithm == "c90-b":
            validate_c90_password(data)
            flash.patch(address, data, backup)
        else:
            # Install into an erased slot, preserving lifecycle and DCF records.
            # Activation is a separate device configuration operation.
            from .geometry import select_blocks
            blocks = select_blocks(flash.chip, address, len(data))
            with flash.stage("backup", 0, 0.2):
                backup([(b, flash.read(b.address, b.size)) for b in blocks])
            with flash.stage("install and verify password", 0.2, 1):
                flash.program(address, data)
    except VerificationError:
        raise VerificationError("Password read-back verification failed") from None
