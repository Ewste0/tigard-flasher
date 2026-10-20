"""Device-specific password encoding and provisioning; no password recovery.

Input order is increasing flash address: NVPWD0 then NVPWD1 for C90,
and the eight UTEST words in increasing address order for C55.
Passwords must never be included in plans, logs, status snapshots or URLs.
"""
import re
import hmac
from .errors import DriverError, VerificationError


def validate_login(chip, password):
    lengths = {"censor64": 8, "jtag256": 32}
    if chip.password_protocol not in lengths:
        raise ValueError("This family's JTAG password scan awaits verification from its reference manual")
    if not isinstance(password, bytes) or len(password) != lengths[chip.password_protocol]:
        raise ValueError(f"{chip.part} requires exactly {chip.password_bits // 4} hexadecimal digits")


def login_result(memory, chip, password, *, status=None, password_submitted=True):
    """Verify C55 status, or compare readable OTP on a Customer Delivery device.

    Never infer a password match merely from accessible debug. Do not read the
    password region in protected lifecycles or change lifecycle/lock registers.
    """
    if chip.password_protocol != "jtag256":
        return {"password_verified": False, "unlock_verified": False, "method": "debug_access_only",
                "message": "Password supplied; debug session opened. An already uncensored target cannot validate the password."}
    if status is None:
        status = memory.read32(chip.flash_ctrl["PASS"] + chip.unlock["PASS_LCSTAT"])
    if password_submitted and status & 0x40000000:
        return {"password_verified": True, "unlock_verified": True, "method": "jtag_status",
                "message": "JTAG password accepted: the device reports password unlock. Debug session opened."}
    if status & 7 == 3:
        # Customer Delivery permits reading UTEST passwords. Compare in memory,
        # never use flash.verify(), whose mismatch message can disclose bytes.
        stored = memory.read(password_address(chip), len(password))
        if not hmac.compare_digest(stored, password):
            raise DriverError("Password does not match the installed JTAG slot. The device is still in Customer Delivery with debug access enabled.")
        return {"password_verified": True, "unlock_verified": False, "method": "stored_password",
                "message": "Installed JTAG password matches. Debug access is already enabled in Customer Delivery; protected-target unlock was not verified."}
    raise DriverError("JTAG password was not accepted (unlock status is clear). Check the password and power-cycle the target after installing it. No protection settings were changed.")


def installation_message(chip):
    message = "Password stored and read-back verified. Protection settings were preserved; protection was not activated."
    if chip.password_bits == 256:
        message += " Power-cycle the target before testing password login."
    if not chip.password_protocol:
        message += " JTAG login is not yet supported for this device."
    return message


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
            from .geometry import backup_blocks
            blocks = backup_blocks(flash.chip, address, len(data))
            with flash.stage("backup", 0, 0.2):
                backup([(b, flash.read(b.address, b.size)) for b in blocks])
            with flash.stage("install and verify password", 0.2, 1):
                flash.program(address, data)
    except VerificationError:
        raise VerificationError("Password read-back verification failed") from None
