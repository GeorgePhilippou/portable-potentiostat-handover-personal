#!/usr/bin/env python3
"""Capture and tabulate Stage 1 AD5941 potential-path diagnostics."""

import argparse
import csv
import glob
import sys
from pathlib import Path

import serial


FIELDS = [
    "raw_bias_cmd_mV",
    "desired_Ewe_re_mV",
    "vbias12_code",
    "vzero6_code",
    "vbias_minus_vzero_mV",
    "vse_minus_vzero_mV",
    "vre_minus_vzero_mV",
    "measured_Ewe_re_mV",
    "bias_error_mV",
    "control_error_mV",
]

UART_KEYS = {
    "POT_DIAG_RAW_BIAS_CMD_MV": "raw_bias_cmd_mV",
    "POT_DIAG_DESIRED_EWE_RE_MV": "desired_Ewe_re_mV",
    "POT_DIAG_VBIAS12_CODE": "vbias12_code",
    "POT_DIAG_VZERO6_CODE": "vzero6_code",
    "POT_DIAG_VBIAS_MINUS_VZERO_MV": "vbias_minus_vzero_mV",
    "POT_DIAG_VSE_MINUS_VZERO_MV": "vse_minus_vzero_mV",
    "POT_DIAG_VRE_MINUS_VZERO_MV": "vre_minus_vzero_mV",
    "POT_DIAG_MEASURED_EWE_RE_MV": "measured_Ewe_re_mV",
    "POT_DIAG_BIAS_ERROR_MV": "bias_error_mV",
    "POT_DIAG_CONTROL_ERROR_MV": "control_error_mV",
}


def resolve_port(requested):
    if requested:
        return requested
    matches = sorted(glob.glob("/dev/cu.usbmodem*"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit("No /dev/cu.usbmodem* serial port found.")
    raise SystemExit("Multiple serial ports found; use --port:\n  " + "\n  ".join(matches))


def print_row(row):
    print(
        "raw={raw_bias_cmd_mV:8.2f} mV  desired EWE-RE={desired_Ewe_re_mV:8.2f} mV  "
        "measured={measured_Ewe_re_mV:8.2f} mV  control error={control_error_mV:7.2f} mV".format(
            **row
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", help="Serial port; auto-detected when exactly one usbmodem exists")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--csv", type=Path, default=Path("potential_diagnostic.csv"))
    args = parser.parse_args()

    port = resolve_port(args.port)
    print(f"Listening on {port} at {args.baud} baud. Press the MSP430 reset button now.")

    rows = []
    current = None
    with serial.Serial(port, args.baud, timeout=2) as ser:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line.startswith("# "):
                continue
            payload = line[2:]
            key, separator, value = payload.partition(",")
            if not separator:
                continue

            if key == "POT_DIAG_BEGIN":
                current = {}
            elif key in UART_KEYS and current is not None:
                try:
                    current[UART_KEYS[key]] = float(value)
                except ValueError:
                    print(f"Ignoring malformed diagnostic value: {line}", file=sys.stderr)
            elif key == "POT_DIAG_END" and current is not None:
                missing = [field for field in FIELDS if field not in current]
                if missing:
                    print("Incomplete diagnostic row, missing: " + ", ".join(missing), file=sys.stderr)
                else:
                    rows.append(current)
                    print_row(current)
                current = None
            elif key == "POT_DIAG_DONE":
                break

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} diagnostic points to {args.csv.resolve()}")

    if len(rows) != 5:
        raise SystemExit("Expected 5 complete diagnostic points.")


if __name__ == "__main__":
    main()
