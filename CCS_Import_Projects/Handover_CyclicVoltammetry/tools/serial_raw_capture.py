#!/usr/bin/env python3
"""
serial_raw_capture.py
=====================
Dead-simple raw serial capture for debugging the CV/LSV firmware output.

It just prints the EXACT bytes the firmware sends, one line at a time, with
repr() so you can see hidden characters, spacing, and formatting. Use this to
tell whether the firmware is emitting current values at all, and in what format.

HOW TO USE IN SPYDER
--------------------
1. STOP the cv_dashboard.py first (Spyder: click the red square / restart the
   kernel). Only one program can open the serial port at a time.
2. Open this file in Spyder and press Run (F5).
3. Watch the console for ~15 s (one full sweep is ~10 s).
4. Copy the printed output and send it back.

If you get a "could not open port" / "Resource busy" error, the dashboard (or
another Spyder kernel) still has the port open — stop it and run this again.
"""

import sys
import time

import serial

# ── EDIT THESE IF NEEDED ─────────────────────────────────────────────────────
PORT = "/dev/cu.usbmodem1303"   # same port the dashboard uses
BAUD = 9600
CAPTURE_SECONDS = 70            # long enough to outlast boot + capture 2-3 slow (20 s) CV sweeps
# ─────────────────────────────────────────────────────────────────────────────


def main():
    print(f"Opening {PORT} @ {BAUD} baud ...")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
    except serial.SerialException as exc:
        print(f"\nCOULD NOT OPEN PORT: {exc}")
        print("Is cv_dashboard.py (or another Spyder kernel) still holding the port?")
        print("Stop it, then run this script again.")
        return

    print(f"Listening for {CAPTURE_SECONDS} s. Raw lines below (repr shows exact bytes):\n")
    t_end = time.time() + CAPTURE_SECONDS
    n_total = 0
    n_numeric = 0
    n_meta = 0
    n_other = 0
    try:
        while time.time() < t_end:
            raw = ser.readline()          # bytes up to and including '\n'
            if not raw:
                continue
            n_total += 1
            print(repr(raw))
            s = raw.decode("utf-8", errors="replace").strip()
            if s.startswith("# CV_META_"):
                n_meta += 1
            elif s and (s.lstrip("-").replace(".", "", 1).isdigit()):
                n_numeric += 1
            else:
                n_other += 1
    finally:
        ser.close()

    print("\n──────── SUMMARY ────────")
    print(f"total lines   : {n_total}")
    print(f"metadata (#)  : {n_meta}")
    print(f"numeric data  : {n_numeric}   <-- these are current samples")
    print(f"other/text    : {n_other}")
    if n_numeric == 0:
        print("\n>> No numeric current values were received. The firmware is not")
        print(">> emitting sample data (FIFO empty / zero samples per step).")
    else:
        print("\n>> Current values ARE being received. If the dashboard shows nothing,")
        print(">> the issue is format/parsing, not the firmware output.")


if __name__ == "__main__":
    main()
