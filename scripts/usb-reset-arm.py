#!/usr/bin/env python3
"""Re-arm ESP32-P4 USB-UART reset immediately before an esptool flash."""

from __future__ import annotations

import argparse
import sys
import time

import serial


USB_RESET_SYSFS = "/sys/kernel/micronux/usb_reset"


def open_serial(port: str) -> serial.Serial:
    device = serial.Serial()
    device.port = port
    device.baudrate = 115200
    device.timeout = 0.05
    device.write_timeout = 2.0
    device.dsrdtr = False
    device.rtscts = False
    device.dtr = False
    device.rts = False
    device.open()
    return device


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if args.timeout < 1.0 or args.timeout > 60.0:
        parser.error("--timeout must be between 1 and 60 seconds")

    try:
        device = open_serial(args.port)
    except serial.SerialException as error:
        print(
            "MICRONUX:USB-RESET-ARM state=unavailable "
            f"port={args.port} detail={str(error).replace(' ', '-')}"
        )
        return 0

    token = f"MICRONUX_USB_RESET_ARM_{time.monotonic_ns()}"
    command = (
        f"if [ -w {USB_RESET_SYSFS} ]; then "
        f"echo enable >{USB_RESET_SYSFS}; fi; echo {token}\n"
    ).encode("ascii")
    deadline = time.monotonic() + args.timeout
    next_write = 0.0
    captured = bytearray()
    armed = False
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_write:
                device.write(command)
                device.flush()
                next_write = now + 0.5
            chunk = device.read(4096)
            if chunk:
                captured.extend(chunk)
                if token.encode("ascii") in captured:
                    armed = True
                    break
                if len(captured) > 16384:
                    del captured[:-8192]
    except (serial.SerialException, serial.SerialTimeoutException):
        pass
    finally:
        try:
            device.dtr = False
            device.rts = False
        except serial.SerialException:
            pass
        device.close()

    if armed:
        print(f"MICRONUX:USB-RESET-ARM state=armed port={args.port}")
    else:
        print(
            "MICRONUX:USB-RESET-ARM state=not-running-or-no-shell "
            f"port={args.port}"
        )
    time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
