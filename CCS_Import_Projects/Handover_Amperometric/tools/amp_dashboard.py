#!/usr/bin/env python3
"""Live and offline dashboard for the MSP430/AD5941 amperometry firmware.

The firmware emits a versioned header followed by framed records::

    # AMP_META_SCHEMA,MSP430_AMP_UART_V1
    # AMP_META_POTENTIAL_MV,400.0000
    AMP,0,0,20.0012
    AMP,1,100,20.0008

Each record contains sample index, scheduled acquisition time in milliseconds,
and anodic-positive current in microamps.  The explicit sample index makes a
missing UART record visible instead of silently shortening the trace.

The dashboard can start without a device, reconnect from the window, overlay
current-time exports from a laboratory potentiostat, save a complete session
(measurements, overlays, metadata, and notes), and reopen it later offline.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import serial
import serial.tools.list_ports
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button, TextBox


DEFAULT_PORT = "/dev/cu.usbmodem1403"
DEFAULT_BAUD = 9600
DEFAULT_RESISTOR_OHM = 20000.0
DEFAULT_DURATION_SECONDS = 60.0
DEFAULT_SAMPLE_RATE_HZ = 10.0
MIN_SAMPLE_RATE_HZ = 0.1
MAX_SAMPLE_RATE_HZ = 20.0
CONTROL_SCHEMAS = {"MSP430_AMP_UART_V2", "MSP430_AMP_UART_V3", "MSP430_AMP_UART_V4"}
RATE_CONTROL_SCHEMA = "MSP430_AMP_UART_V3"
RATE_CONTROL_SCHEMAS = {RATE_CONTROL_SCHEMA, "MSP430_AMP_UART_V4"}
SESSION_FORMAT_VERSION = 2
MAX_ABS_CURRENT_UA = 100000.0
AMP_RECORD_RE = re.compile(
    r"^AMP,(\d+),(\d+(?:\.\d+)?),([+-]?(?:\d+(?:\.\d*)?|\.\d+))$")
META_PREFIX = "# AMP_META_"
OVERLAY_COLORS = ["black", "darkorange", "purple", "forestgreen", "saddlebrown"]
FULL_EXPANDED_BOUNDS = (0.065, 0.13, 0.69, 0.78)
FULL_SPLIT_BOUNDS = (0.065, 0.13, 0.325, 0.78)
RECENT_EXPANDED_BOUNDS = (0.43, 0.13, 0.325, 0.78)
RECENT_COLLAPSED_BOUNDS = (0.754, 0.13, 0.001, 0.78)
LAYOUT_ANIMATION_SECONDS = 0.32


def interpolate_bounds(start, end, progress):
    """Interpolate axes bounds with a smooth start and finish."""
    progress = min(1.0, max(0.0, float(progress)))
    eased = progress * progress * (3.0 - 2.0 * progress)
    return tuple(
        start_value + (end_value - start_value) * eased
        for start_value, end_value in zip(start, end))


def empty_meta():
    return {
        "schema": "",
        "potential_mV": None,
        "internal_sensor_bias_mV": None,
        "sample_period_ms": None,
        "rtia_nominal_ohm": None,
        "rtia_calibrated_ohm": None,
        "vzero_mV": None,
        "lfosc_hz": None,
        "potential_convention": "",
        "current_convention": "",
        "current_units": "uA",
        "time_units": "ms",
        "time_source": "",
        "control_mode": "",
        "run_number": None,
        "run_duration_ms": None,
        "uart_baud": None,
    }


def empty_device_data():
    return {
        "sample_index": [],
        "device_time_ms": [],
        "host_time_ms": [],
        "current_uA": [],
    }


def list_ports():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    print("Available serial ports:")
    for port in ports:
        print(f"  {port.device:30s} {port.description}")


def serial_port_candidates(preferred):
    candidates = [preferred]
    discovered = [
        port.device for port in serial.tools.list_ports.comports()
        if port.device.startswith("/dev/cu.usbmodem")
    ]
    if preferred not in discovered and len(discovered) == 1:
        candidates.append(discovered[0])
    return candidates


def open_serial_connection(preferred, baud):
    errors = []
    for device in serial_port_candidates(preferred):
        try:
            return serial.Serial(device, baud, timeout=0), device
        except (serial.SerialException, OSError) as exc:
            errors.append(f"{device}: {exc}")
    raise serial.SerialException("; ".join(errors) or "no serial port available")


def request_metadata(ser):
    """Ask a running device to repeat its header without restarting acquisition."""
    ser.write(b"M\n")


def duration_seconds_to_ms(text):
    """Convert the dashboard duration field; zero selects a continuous run."""
    try:
        seconds = float(str(text).strip())
    except ValueError as exc:
        raise ValueError("duration must be a number of seconds") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("duration must be zero or a positive number")
    milliseconds = int(round(seconds * 1000.0))
    if milliseconds > 0xFFFFFFFF:
        raise ValueError("duration is too long for the firmware timer")
    return milliseconds


def sample_rate_to_period_ms(text):
    """Convert a requested sample rate to the firmware's integer-ms period."""
    try:
        rate_hz = float(str(text).strip())
    except ValueError as exc:
        raise ValueError("sample rate must be a number in hertz") from exc
    if (not math.isfinite(rate_hz) or rate_hz < MIN_SAMPLE_RATE_HZ or
            rate_hz > MAX_SAMPLE_RATE_HZ):
        raise ValueError(
            f"sample rate must be {MIN_SAMPLE_RATE_HZ:g} to "
            f"{MAX_SAMPLE_RATE_HZ:g} Hz")
    return int(round(1000.0 / rate_hz))


def start_measurement(ser, duration_ms, sample_period_ms=None):
    if sample_period_ms is None:
        ser.write(f"S,{duration_ms}\n".encode("ascii"))
    else:
        ser.write(
            f"S,{duration_ms},{sample_period_ms}\n".encode("ascii"))


def stop_measurement(ser):
    ser.write(b"X\n")


def firmware_supports_control(meta):
    return meta.get("schema") in CONTROL_SCHEMAS


def firmware_supports_rate_control(meta):
    return meta.get("schema") in RATE_CONTROL_SCHEMAS


class LineReader:
    def __init__(self, ser):
        self.ser = ser
        self.buffer = b""

    def read_lines(self):
        waiting = self.ser.in_waiting
        if waiting:
            self.buffer += self.ser.read(waiting)
        lines = []
        while b"\n" in self.buffer:
            raw, self.buffer = self.buffer.split(b"\n", 1)
            lines.append(raw.decode("utf-8", errors="ignore").strip())
        return lines


NUMERIC_META_FIELDS = {
    "POTENTIAL_MV": "potential_mV",
    "INTERNAL_SENSOR_BIAS_MV": "internal_sensor_bias_mV",
    "SAMPLE_PERIOD_MS": "sample_period_ms",
    "RTIA_NOMINAL_OHM": "rtia_nominal_ohm",
    "RTIA_CALIBRATED_OHM": "rtia_calibrated_ohm",
    "VZERO_MV": "vzero_mV",
    "LFOSC_HZ": "lfosc_hz",
    "UART_BAUD": "uart_baud",
}

TEXT_META_FIELDS = {
    "SCHEMA": "schema",
    "POTENTIAL_CONVENTION": "potential_convention",
    "CURRENT_CONVENTION": "current_convention",
    "CURRENT_UNITS": "current_units",
    "TIME_UNITS": "time_units",
    "TIME_SOURCE": "time_source",
    "CONTROL_MODE": "control_mode",
}


def parse_meta_line(line, meta):
    """Parse one metadata line, returning True only when it is recognized."""
    if not line.startswith(META_PREFIX) or "," not in line:
        return False
    name, value = line[len(META_PREFIX):].split(",", 1)
    name, value = name.strip(), value.strip()
    if name in NUMERIC_META_FIELDS:
        try:
            number = float(value)
        except ValueError:
            return False
        meta[NUMERIC_META_FIELDS[name]] = number
        return True
    if name in TEXT_META_FIELDS:
        meta[TEXT_META_FIELDS[name]] = value
        return True
    return False


def parse_amp_record(line):
    """Return ``(index, time_ms, current_uA)`` or None for a non-data line."""
    match = AMP_RECORD_RE.fullmatch(line.strip())
    if not match:
        return None
    sample_index = int(match.group(1))
    device_time_ms = float(match.group(2))
    current_uA = float(match.group(3))
    if not (math.isfinite(device_time_ms) and math.isfinite(current_uA)):
        return None
    if device_time_ms < 0 or abs(current_uA) > MAX_ABS_CURRENT_UA:
        return None
    return sample_index, device_time_ms, current_uA


def record_matches_schedule(record, sample_period_ms):
    """Reject parseable UART corruption using the redundant index/time fields."""
    if record is None or sample_period_ms is None or sample_period_ms <= 0:
        return True
    sample_index, device_time_ms, _current_uA = record
    expected_ms = sample_index * float(sample_period_ms)
    tolerance_ms = max(0.5, float(sample_period_ms) * 0.01)
    return abs(device_time_ms - expected_ms) <= tolerance_ms


def format_rtia(meta):
    """Return a calibrated RTIA label, flagging implausible UART values."""
    nominal = meta.get("rtia_nominal_ohm")
    calibrated = meta.get("rtia_calibrated_ohm")
    if calibrated is not None and calibrated > 0:
        if nominal and not (0.5 * nominal <= calibrated <= 2.0 * nominal):
            return f"INVALID ({calibrated / 1e6:.3g} MΩ)"
        return f"{calibrated / 1000.0:.3f} kΩ calibrated"
    if nominal is not None:
        return f"{nominal / 1000.0:.3f} kΩ nominal"
    return "--"


def sample_diagnostics(device_data):
    indices = np.asarray(device_data["sample_index"], dtype=np.int64)
    times = np.asarray(device_data["device_time_ms"], dtype=float)
    missing = 0
    order_errors = 0
    time_order_errors = 0
    largest_index_jump = 1
    if indices.size > 1:
        index_diff = np.diff(indices)
        missing = int(np.maximum(index_diff - 1, 0).sum())
        order_errors = int(np.count_nonzero(index_diff <= 0))
        largest_index_jump = int(max(1, index_diff.max()))
    if times.size > 1:
        time_order_errors = int(np.count_nonzero(np.diff(times) <= 0))
    return {
        "missing_samples": missing,
        "index_order_errors": order_errors,
        "time_order_errors": time_order_errors,
        "largest_index_jump": largest_index_jump,
    }


def update_axis_limits(axis, has_data, window_seconds):
    """Autoscale acquired data after using fixed, friendly empty-state limits."""
    if has_data:
        axis.set_autoscalex_on(True)
        axis.set_autoscaley_on(True)
        axis.relim()
        axis.autoscale_view()
        axis.margins(x=0.03, y=0.08)
    else:
        axis.set_xlim(0.0, window_seconds)
        axis.set_ylim(-1.0, 1.0)


def _split_fields(line):
    if ";" in line:
        return [field.strip().strip('"') for field in line.split(";")]
    if "\t" in line:
        return [field.strip().strip('"') for field in line.split("\t")]
    return [field.strip().strip('"') for field in line.split(",")]


def _current_scale_to_uA(header):
    text = header.lower().replace("µ", "u").replace("μ", "u")
    if "na" in text:
        return 0.001
    if "ma" in text:
        return 1000.0
    if "ua" in text:
        return 1.0
    if "(a)" in text or text.endswith(" a") or text == "a":
        return 1_000_000.0
    return 1.0


def _time_scale_to_seconds(header):
    text = header.lower()
    if "ms" in text:
        return 0.001
    if "min" in text:
        return 60.0
    return 1.0


def load_overlay_csv(path):
    """Load current-versus-time data from a plain or Metrohm-style export."""
    path = Path(path).expanduser()
    header = None
    numeric_rows = []
    with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            fields = _split_fields(line)
            if len(fields) < 2:
                continue
            try:
                numeric = [float(value) for value in fields[:2]]
            except ValueError:
                joined = " ".join(fields).lower()
                if "time" in joined and "current" in joined:
                    header = fields[:2]
                continue
            numeric_rows.append(numeric)
    if not numeric_rows:
        raise ValueError(f"no numeric time/current rows found in {path.name}")
    values = np.asarray(numeric_rows, dtype=float)
    time_scale = _time_scale_to_seconds(header[0] if header else "time (s)")
    current_scale = _current_scale_to_uA(header[1] if header else "current (uA)")
    return path.stem, values[:, 0] * time_scale, values[:, 1] * current_scale


def session_csv_path(path):
    path = Path(path).expanduser()
    return path if path.suffix.lower() == ".csv" else path.with_suffix(".csv")


SESSION_FIELDS = [
    "source", "label", "sample_index", "device_time_ms", "host_time_ms",
    "time_s", "current_uA", "schema", "potential_mV",
    "internal_sensor_bias_mV", "sample_period_ms", "rtia_nominal_ohm",
    "rtia_calibrated_ohm", "vzero_mV", "lfosc_hz",
    "potential_convention", "current_convention", "current_units",
    "time_units", "time_source", "control_mode", "run_number",
    "run_duration_ms", "uart_baud",
    "session_format_version",
    "saved_at", "session_notes_json",
]


def write_session_csv(path, device_data, overlays, meta, notes=None, saved_at=None):
    path = session_csv_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    notes = list(notes or [])
    saved_at = saved_at or datetime.now().astimezone().isoformat(timespec="seconds")
    common = {
        key: meta.get(key, "") for key in empty_meta()
    }
    common.update({
        "session_format_version": SESSION_FORMAT_VERSION,
        "saved_at": saved_at,
        "session_notes_json": json.dumps(notes, ensure_ascii=False),
    })
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SESSION_FIELDS)
        writer.writeheader()
        for idx, device_ms, host_ms, current in zip(
                device_data["sample_index"], device_data["device_time_ms"],
                device_data["host_time_ms"], device_data["current_uA"]):
            writer.writerow({
                "source": "device", "label": "Device amperometry",
                "sample_index": idx, "device_time_ms": f"{device_ms:.9g}",
                "host_time_ms": f"{host_ms:.9g}",
                "time_s": f"{device_ms / 1000.0:.9g}",
                "current_uA": f"{current:.9g}", **common,
            })
        for label, times_s, currents_uA in overlays:
            for time_s, current_uA in zip(times_s, currents_uA):
                writer.writerow({
                    "source": "overlay", "label": label,
                    "time_s": f"{float(time_s):.9g}",
                    "current_uA": f"{float(current_uA):.9g}", **common,
                })
    return path


def _optional_float(row, key):
    value = (row.get(key) or "").strip()
    return float(value) if value else None


def load_session_csv(path):
    path = Path(path).expanduser()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not {"source", "current_uA"}.issubset(reader.fieldnames or []):
            raise ValueError("not an amperometry dashboard session")
        rows = list(reader)
    if not rows:
        raise ValueError("session contains no rows")

    device_data = empty_device_data()
    overlay_groups = {}
    for row_number, row in enumerate(rows, start=2):
        try:
            current = float(row["current_uA"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid current on row {row_number}") from exc
        source = (row.get("source") or "").lower().strip()
        if source == "device":
            device_data["sample_index"].append(int(float(row["sample_index"])))
            device_data["device_time_ms"].append(float(row["device_time_ms"]))
            device_data["host_time_ms"].append(
                float(row.get("host_time_ms") or row["device_time_ms"]))
            device_data["current_uA"].append(current)
        elif source == "overlay":
            label = (row.get("label") or "Reference").strip()
            group = overlay_groups.setdefault(label, [[], []])
            group[0].append(float(row["time_s"]))
            group[1].append(current)

    first = rows[0]
    meta = empty_meta()
    for key in meta:
        value = (first.get(key) or "").strip()
        if not value:
            continue
        meta[key] = value if isinstance(meta[key], str) else float(value)
    notes = []
    raw_notes = first.get("session_notes_json") or ""
    if raw_notes:
        try:
            decoded = json.loads(raw_notes)
            if isinstance(decoded, list):
                notes = [str(note) for note in decoded if str(note).strip()]
        except json.JSONDecodeError:
            notes = [raw_notes]
    overlays = [
        (label, np.asarray(values[0], dtype=float), np.asarray(values[1], dtype=float))
        for label, values in overlay_groups.items()
    ]
    return {
        "path": path,
        "device_data": device_data,
        "overlays": overlays,
        "meta": meta,
        "notes": notes,
        "saved_at": first.get("saved_at") or "",
    }


def save_session_files(path, device_data, overlays, meta, fig, notes=None):
    csv_path = write_session_csv(path, device_data, overlays, meta, notes=notes)
    png_path = csv_path.with_suffix(".png")
    fig.savefig(png_path, dpi=200, bbox_inches="tight")
    return csv_path, png_path


def open_file_dialog(prompt="Select CSV"):
    try:
        from matplotlib.backends.qt_compat import QtWidgets
        if QtWidgets.QApplication.instance() is not None:
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, prompt, "", "CSV files (*.csv);;All files (*)")
            return filename or None
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            import subprocess
            script = f'POSIX path of (choose file with prompt "{prompt}")'
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        filename = filedialog.askopenfilename(
            title=prompt, filetypes=[("CSV files", "*.csv"), ("All files", "*")])
        root.destroy()
        return filename or None
    except Exception:
        return None


def save_file_dialog(default_name):
    try:
        from matplotlib.backends.qt_compat import QtWidgets
        if QtWidgets.QApplication.instance() is not None:
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                None, "Save amperometry session", default_name,
                "CSV files (*.csv);;All files (*)")
            return filename or None
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            import subprocess
            script = (
                'POSIX path of (choose file name with prompt "Save amperometry session" '
                f'default name "{default_name}")')
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        filename = filedialog.asksaveasfilename(
            title="Save amperometry session", initialfile=default_name,
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        root.destroy()
        return filename or None
    except Exception:
        return None


def _relative_device_seconds(device_data):
    values = np.asarray(device_data["device_time_ms"], dtype=float)
    return (values - values[0]) / 1000.0 if values.size else values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--session", help="Open a saved session without live data")
    parser.add_argument("--list-ports", action="store_true")
    parser.add_argument("--window-seconds", type=float, default=60.0)
    parser.add_argument("--resistor-ohm", type=float, default=DEFAULT_RESISTOR_OHM)
    args = parser.parse_args(argv)
    if args.list_ports:
        list_ports()
        return 0

    meta = empty_meta()
    device_data = empty_device_data()
    overlays = []
    notes = []
    counters = {
        "malformed": 0,
        "inconsistent": 0,
        "firmware_errors": 0,
        "firmware_restarts": 0,
    }
    review_name = [""]
    saved_at = [""]
    pending = {
        "overlay": False, "save": False, "session": False,
        "quit": False,
    }
    auto_scale = [True]
    legend_visible = [True]
    expected_visible = [False]
    recent_visible = [False]
    host_origin = [time.monotonic()]
    active_port = [args.port]
    firmware_state = ["UNKNOWN"]
    ser = None
    reader = None

    if args.session:
        loaded = load_session_csv(args.session)
        device_data.update(loaded["device_data"])
        overlays.extend(loaded["overlays"])
        meta.update(loaded["meta"])
        notes.extend(loaded["notes"])
        review_name[0] = loaded["path"].name
        saved_at[0] = loaded["saved_at"]
    elif not args.offline:
        try:
            ser, active_port[0] = open_serial_connection(args.port, args.baud)
            reader = LineReader(ser)
            request_metadata(ser)
            print(f"[serial] connected to {active_port[0]} @ {args.baud} baud")
        except (serial.SerialException, OSError) as exc:
            print(f"[serial] unavailable; starting offline: {exc}")

    fig = plt.figure(figsize=(14, 7.5))
    try:
        fig.canvas.manager.set_window_title("AD5941 amperometry dashboard")
    except Exception:
        pass
    ax_full = fig.add_axes(FULL_EXPANDED_BOUNDS)
    ax_recent = fig.add_axes(RECENT_COLLAPSED_BOUNDS)
    ax_recent.set_visible(False)
    ax_info = fig.add_axes([0.785, 0.545, 0.19, 0.375])
    ax_info.axis("off")
    full_line, = ax_full.plot([], [], color="#1677b8", lw=1.5, label="Device")
    recent_line, = ax_recent.plot([], [], color="#1677b8", lw=1.5, label="Device")
    expected_full = ax_full.axhline(0, color="#e67e22", ls="--", lw=1.2,
                                    visible=False, label="Expected V/R")
    expected_recent = ax_recent.axhline(0, color="#e67e22", ls="--", lw=1.2,
                                        visible=False, label="Expected V/R")
    overlay_lines = []
    overlay_signature = [None]
    legend_signature = [None]

    for axis, title in ((ax_full, "Complete experiment"),
                        (ax_recent, f"Most recent {args.window_seconds:g} s")):
        axis.set_title(title)
        axis.set_xlabel("Elapsed time (s)")
        axis.grid(True, alpha=0.25)
    ax_full.set_ylabel("Current (µA, anodic positive)")
    ax_recent.set_ylabel("")
    fig.suptitle("Amperometry dashboard", fontsize=15)

    button_specs = [
        ("start", 0.785, 0.425, "Start", "#d8f1d8"),
        ("stop", 0.885, 0.425, "Stop", "#f4cccc"),
        ("connect", 0.785, 0.375,
         "Connect device" if ser is None else "Reconnect", "#d9ebff"),
        ("overlay", 0.885, 0.375, "Load overlay", "#d9ebff"),
        ("clear", 0.785, 0.325, "Clear overlays", "#e8e8e8"),
        ("session", 0.885, 0.325, "Load session", "#d9ebff"),
        ("save", 0.785, 0.275, "Save session", "#fff0b8"),
        ("expected", 0.885, 0.275, "V/R line: off", "#ffe1c2"),
        ("legend", 0.785, 0.225, "Hide legend", "#e8e8e8"),
        ("scale", 0.885, 0.225, "Auto scale: on", "#e8e8e8"),
        ("recent", 0.785, 0.175, "Show recent", "#e8e8e8"),
        ("quit", 0.885, 0.175, "Quit", "#f4cccc"),
    ]
    buttons = {}
    for name, x, y, label, color in button_specs:
        width = 0.09 if name != "quit" else 0.09
        axis = fig.add_axes([x, y, width, 0.038])
        buttons[name] = Button(axis, label, color=color, hovercolor="white")

    fig.text(0.785, 0.525, "Duration (s)", fontsize=8.8,
             ha="left", va="bottom")
    fig.text(0.885, 0.525, "Sample rate (Hz)", fontsize=8.8,
             ha="left", va="bottom")
    duration_axis = fig.add_axes([0.785, 0.485, 0.09, 0.04])
    duration_box = TextBox(
        duration_axis, "",
        initial=f"{DEFAULT_DURATION_SECONDS:g}")
    rate_axis = fig.add_axes([0.885, 0.485, 0.09, 0.04])
    rate_box = TextBox(
        rate_axis, "", initial=f"{DEFAULT_SAMPLE_RATE_HZ:g}")
    note_axis = fig.add_axes([0.82, 0.11, 0.095, 0.04])
    note_box = TextBox(note_axis, "Note: ", initial="")
    note_box.set_active(True)
    add_note_axis = fig.add_axes([0.925, 0.11, 0.05, 0.04])
    buttons["add_note"] = Button(
        add_note_axis, "Add", color="#e8e8e8", hovercolor="white")
    status_axis = fig.add_axes([0.785, 0.035, 0.19, 0.06])
    status_axis.axis("off")
    status_text = status_axis.text(
        0.02, 0.82, "Status: Ready", fontsize=8.2, va="top", ha="left",
        transform=status_axis.transAxes,
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#f3f4f6",
                  edgecolor="#cccccc", linewidth=0.8))

    layout_transition = {
        "active": False,
        "started": 0.0,
        "full_start": FULL_EXPANDED_BOUNDS,
        "full_end": FULL_EXPANDED_BOUNDS,
        "recent_start": RECENT_COLLAPSED_BOUNDS,
        "recent_end": RECENT_COLLAPSED_BOUNDS,
        "show_recent": False,
    }
    layout_timer = fig.canvas.new_timer(interval=16)

    def current_bounds(axis):
        bounds = axis.get_position().bounds
        return tuple(float(value) for value in bounds)

    def animate_layout():
        if not layout_transition["active"]:
            layout_timer.stop()
            return
        elapsed = time.monotonic() - layout_transition["started"]
        progress = min(1.0, elapsed / LAYOUT_ANIMATION_SECONDS)
        ax_full.set_position(interpolate_bounds(
            layout_transition["full_start"],
            layout_transition["full_end"], progress))
        ax_recent.set_position(interpolate_bounds(
            layout_transition["recent_start"],
            layout_transition["recent_end"], progress))
        if progress >= 1.0:
            layout_transition["active"] = False
            if not layout_transition["show_recent"]:
                ax_recent.set_visible(False)
            layout_timer.stop()
        fig.canvas.draw_idle()

    layout_timer.add_callback(animate_layout)
    fig._amp_layout_timer = layout_timer

    def set_status(message):
        status_text.set_text("Status: " + textwrap.fill(message, width=34))
        print(f"[dashboard] {message}")

    def close_serial():
        nonlocal ser, reader
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        ser, reader = None, None
        buttons["connect"].label.set_text("Connect device")
        firmware_state[0] = "OFFLINE"

    def clear_measurement(message="New run started; previous device trace cleared"):
        for values in device_data.values():
            values.clear()
        counters["malformed"] = 0
        counters["inconsistent"] = 0
        counters["firmware_errors"] = 0
        host_origin[0] = time.monotonic()
        review_name[0] = ""
        saved_at[0] = ""
        buttons["connect"].label.set_text("Reconnect" if ser is not None else "Connect device")
        set_status(message)

    def connect_device(_event):
        nonlocal ser, reader
        if ser is not None:
            close_serial()
        try:
            ser, active_port[0] = open_serial_connection(args.port, args.baud)
            reader = LineReader(ser)
            ser.reset_input_buffer()
            request_metadata(ser)
            firmware_state[0] = "CONNECTING"
            buttons["connect"].label.set_text("Reconnect")
            set_status(f"Connected to {active_port[0]}; waiting for device state")
        except (serial.SerialException, OSError) as exc:
            set_status(f"Connection failed: {exc}")

    def start_request(_event):
        if ser is None:
            set_status("Connect the device before starting a measurement")
            return
        if not firmware_supports_control(meta):
            if meta.get("schema"):
                set_status(
                    f"Firmware {meta['schema']} does not support dashboard "
                    f"Start/Stop; flash {RATE_CONTROL_SCHEMA}")
            else:
                set_status("Waiting for firmware metadata; try Start again shortly")
                request_metadata(ser)
            return
        try:
            duration_ms = duration_seconds_to_ms(duration_box.text)
            sample_period_ms = sample_rate_to_period_ms(rate_box.text)
            if not firmware_supports_rate_control(meta):
                default_period_ms = int(round(1000.0 / DEFAULT_SAMPLE_RATE_HZ))
                if sample_period_ms != default_period_ms:
                    set_status(
                        f"Firmware {meta['schema']} cannot change sample rate; "
                        f"flash {RATE_CONTROL_SCHEMA}")
                    return
            ser.reset_input_buffer()
            if reader is not None:
                reader.buffer = b""
            clear_measurement("Starting measurement; waiting for the first sample")
            firmware_state[0] = "STARTING"
            if firmware_supports_rate_control(meta):
                start_measurement(ser, duration_ms, sample_period_ms)
                rate_box.set_val(f"{1000.0 / sample_period_ms:g}")
            else:
                start_measurement(ser, duration_ms)
        except (ValueError, serial.SerialException, OSError) as exc:
            set_status(f"Measurement not started: {exc}")

    def stop_request(_event):
        if ser is None:
            set_status("No device is connected")
            return
        try:
            stop_measurement(ser)
            firmware_state[0] = "STOPPING"
            set_status("Stop requested; waiting for firmware confirmation")
        except (serial.SerialException, OSError) as exc:
            set_status(f"Stop command failed: {exc}")

    def load_overlay_request(_event):
        pending["overlay"] = True

    def clear_overlays(_event):
        overlays.clear()
        set_status("All overlays removed")

    def load_session_request(_event):
        pending["session"] = True

    def save_request(_event):
        if note_box.text.strip():
            add_note(note_box.text)
        pending["save"] = True

    def expected_toggle(_event):
        expected_visible[0] = not expected_visible[0]
        buttons["expected"].label.set_text(
            "V/R line: on" if expected_visible[0] else "V/R line: off")

    def legend_toggle(_event):
        legend_visible[0] = not legend_visible[0]
        buttons["legend"].label.set_text(
            "Hide legend" if legend_visible[0] else "Show legend")

    def scale_toggle(_event):
        auto_scale[0] = not auto_scale[0]
        buttons["scale"].label.set_text(
            "Auto scale: on" if auto_scale[0] else "Auto scale: off")
        set_status("Automatic scaling enabled" if auto_scale[0]
                   else "Automatic scaling disabled; zoom and pan will be preserved")

    def recent_toggle(_event):
        show_recent = not recent_visible[0]
        recent_visible[0] = show_recent
        layout_timer.stop()
        if show_recent:
            ax_recent.set_visible(True)
        layout_transition.update({
            "active": True,
            "started": time.monotonic(),
            "full_start": current_bounds(ax_full),
            "full_end": (FULL_SPLIT_BOUNDS if show_recent
                         else FULL_EXPANDED_BOUNDS),
            "recent_start": current_bounds(ax_recent),
            "recent_end": (RECENT_EXPANDED_BOUNDS if show_recent
                           else RECENT_COLLAPSED_BOUNDS),
            "show_recent": show_recent,
        })
        buttons["recent"].label.set_text(
            "Hide recent" if show_recent else "Show recent")
        layout_timer.start()

    def quit_request(_event):
        pending["quit"] = True

    def add_note(text):
        note = text.strip()
        if note:
            notes.append(note)
            note_box.set_val("")
            set_status(f"Note added ({len(notes)} total)")

    buttons["start"].on_clicked(start_request)
    buttons["stop"].on_clicked(stop_request)
    buttons["connect"].on_clicked(connect_device)
    buttons["overlay"].on_clicked(load_overlay_request)
    buttons["clear"].on_clicked(clear_overlays)
    buttons["session"].on_clicked(load_session_request)
    buttons["save"].on_clicked(save_request)
    buttons["expected"].on_clicked(expected_toggle)
    buttons["legend"].on_clicked(legend_toggle)
    buttons["scale"].on_clicked(scale_toggle)
    buttons["recent"].on_clicked(recent_toggle)
    buttons["add_note"].on_clicked(lambda _event: add_note(note_box.text))
    buttons["quit"].on_clicked(quit_request)
    note_box.on_submit(add_note)

    def rebuild_overlay_lines():
        while overlay_lines:
            line = overlay_lines.pop()
            line.remove()
        for number, (label, times_s, currents_uA) in enumerate(overlays):
            color = OVERLAY_COLORS[number % len(OVERLAY_COLORS)]
            line_full, = ax_full.plot(times_s - times_s[0], currents_uA,
                                      color=color, lw=1.7, alpha=0.9,
                                      label=f"{label} (ref)")
            overlay_lines.append(line_full)

    def refresh_legends(show_expected):
        signature = (
            legend_visible[0], show_expected, overlay_signature[0])
        if signature == legend_signature[0]:
            return
        legend_signature[0] = signature
        for axis in (ax_full, ax_recent):
            legend = axis.get_legend()
            if legend is not None:
                legend.remove()
        if not legend_visible[0]:
            return
        full_handles = [full_line, *overlay_lines]
        if show_expected:
            full_handles.append(expected_full)
        ax_full.legend(
            handles=full_handles,
            labels=[handle.get_label() for handle in full_handles],
            loc="best", fontsize=8, framealpha=0.85)
        recent_handles = [recent_line]
        if show_expected:
            recent_handles.append(expected_recent)
        ax_recent.legend(
            handles=recent_handles,
            labels=[handle.get_label() for handle in recent_handles],
            loc="best", fontsize=8, framealpha=0.85)

    def draw_info():
        diagnostic = sample_diagnostics(device_data)
        currents = np.asarray(device_data["current_uA"], dtype=float)
        duration = (_relative_device_seconds(device_data)[-1]
                    if currents.size else 0.0)
        current_now = f"{currents[-1]:.4f} µA" if currents.size else "--"
        mean = f"{currents.mean():.4f} µA" if currents.size else "--"
        standard_deviation = f"{currents.std():.4f} µA" if currents.size else "--"
        potential = meta.get("potential_mV")
        period = meta.get("sample_period_ms")
        sample_rate = 1000.0 / period if period is not None and period > 0 else None
        lines = [
            "MEASUREMENT",
            f"Potential: {potential:.0f} mV" if potential is not None else "Potential: --",
            (f"Rate:      {sample_rate:.3f} Hz" if sample_rate is not None
             else "Rate:      --"),
            f"Period:    {period:.1f} ms" if period is not None else "Period:    --",
            f"RTIA:      {format_rtia(meta)}",
            f"Vzero:     {meta['vzero_mV']:.0f} mV" if meta.get("vzero_mV") is not None else "Vzero:     --",
            f"State:     {firmware_state[0].lower()}",
            ("Requested: --" if meta.get("run_duration_ms") is None else
             "Requested: continuous" if meta["run_duration_ms"] == 0 else
             f"Requested: {meta['run_duration_ms'] / 1000.0:g} s"),
            "",
            "LIVE DIAGNOSTICS",
            f"Samples:   {currents.size}",
            f"Elapsed:   {duration:.1f} s",
            f"Current:   {current_now}",
            f"Mean:      {mean}",
            f"Std dev:   {standard_deviation}",
            f"Missing:   {diagnostic['missing_samples']}",
            f"Bad/reject:{counters['malformed']} / {counters['inconsistent']}",
            f"FW errors: {counters['firmware_errors']}",
        ]
        if review_name[0]:
            lines.extend(["", f"Session: {review_name[0]}"])
        if notes:
            lines.extend(["", f"Notes: {len(notes)}", textwrap.shorten(notes[-1], 34)])
        ax_info.clear()
        ax_info.axis("off")
        ax_info.text(
            0.02, 0.98, "\n".join(lines), va="top", ha="left",
            transform=ax_info.transAxes, fontsize=7.8, family="monospace",
            bbox=dict(boxstyle="round,pad=0.55", facecolor="#fffde7",
                      edgecolor="#cccccc", linewidth=0.8))

    def update(_frame):
        nonlocal ser, reader
        if pending["quit"]:
            close_serial()
            plt.close(fig)
            return []
        if pending["overlay"]:
            pending["overlay"] = False
            filename = open_file_dialog("Select amperometry reference CSV")
            if filename:
                try:
                    overlays.append(load_overlay_csv(filename))
                    set_status(f"Loaded overlay: {Path(filename).name}")
                except Exception as exc:
                    set_status(f"Overlay not loaded: {exc}")
        if pending["session"]:
            pending["session"] = False
            filename = open_file_dialog("Select amperometry dashboard session")
            if filename:
                try:
                    loaded = load_session_csv(filename)
                    close_serial()
                    for key in device_data:
                        device_data[key][:] = loaded["device_data"][key]
                    overlays[:] = loaded["overlays"]
                    meta.clear(); meta.update(loaded["meta"])
                    notes[:] = loaded["notes"]
                    review_name[0] = loaded["path"].name
                    saved_at[0] = loaded["saved_at"]
                    set_status(f"Loaded session: {review_name[0]}")
                except Exception as exc:
                    set_status(f"Session not loaded: {exc}")
        if pending["save"]:
            pending["save"] = False
            filename = save_file_dialog(
                f"amp_session_{datetime.now():%Y%m%d_%H%M%S}.csv")
            if filename:
                try:
                    csv_path, png_path = save_session_files(
                        filename, device_data, overlays, meta, fig, notes=notes)
                    set_status(f"Saved {csv_path.name} and {png_path.name}")
                except Exception as exc:
                    set_status(f"Session not saved: {exc}")

        if reader is not None:
            try:
                for line in reader.read_lines():
                    if not line:
                        continue
                    if parse_meta_line(line, meta):
                        if (meta.get("schema") and
                                not firmware_supports_control(meta) and
                                firmware_state[0] in {"UNKNOWN", "CONNECTING"}):
                            firmware_state[0] = "LEGACY"
                        continue
                    if line.startswith("# AMP_ERROR") or line.startswith("# AMP_FATAL"):
                        counters["firmware_errors"] += 1
                        set_status(line.lstrip("# "))
                        continue
                    if line.startswith("# AMP_BOOT"):
                        if device_data["sample_index"]:
                            counters["firmware_restarts"] += 1
                            clear_measurement(
                                "Firmware restarted; beginning a fresh device trace")
                        firmware_state[0] = "IDLE"
                        continue
                    if line.startswith("# AMP_RUN_BEGIN"):
                        if device_data["sample_index"]:
                            clear_measurement(
                                "New firmware run started; previous trace cleared")
                        firmware_state[0] = "RUNNING"
                        fields = line.split(",")
                        duration_text = "continuous"
                        if len(fields) >= 2:
                            try:
                                meta["run_number"] = float(fields[1])
                            except ValueError:
                                meta["run_number"] = None
                        meta["run_duration_ms"] = 0.0
                        if len(fields) >= 3 and fields[2] != "0":
                            try:
                                meta["run_duration_ms"] = float(fields[2])
                                duration_text = f"{meta['run_duration_ms'] / 1000.0:g} s"
                            except ValueError:
                                meta["run_duration_ms"] = None
                                duration_text = "configured duration"
                        set_status(f"Measurement running ({duration_text})")
                        continue
                    if line.startswith("# AMP_RUN_END"):
                        firmware_state[0] = "IDLE"
                        fields = line.split(",")
                        reason = fields[1].replace("_", " ").lower() if len(fields) > 1 else "ended"
                        samples = fields[2] if len(fields) > 2 else "?"
                        set_status(f"Measurement {reason}; {samples} samples captured")
                        continue
                    if line.startswith("# AMP_STATE,"):
                        firmware_state[0] = line.split(",", 1)[1].strip().upper()
                        continue
                    if line.startswith("#"):
                        continue
                    record = parse_amp_record(line)
                    if record is None:
                        counters["malformed"] += 1
                        if counters["malformed"] <= 10:
                            print(f"[serial rejected malformed] {line!r}")
                        continue
                    if not record_matches_schedule(
                            record, meta.get("sample_period_ms")):
                        counters["inconsistent"] += 1
                        if counters["inconsistent"] <= 10:
                            print(f"[serial rejected inconsistent] {line!r}")
                        continue
                    sample_index, device_ms, current_uA = record
                    if (device_data["sample_index"] and
                            (sample_index <= device_data["sample_index"][-1] or
                             device_ms <= device_data["device_time_ms"][-1])):
                        counters["firmware_restarts"] += 1
                        clear_measurement(
                            "Sample clock restarted; beginning a fresh device trace")
                    device_data["sample_index"].append(sample_index)
                    device_data["device_time_ms"].append(device_ms)
                    device_data["host_time_ms"].append(
                        (time.monotonic() - host_origin[0]) * 1000.0)
                    device_data["current_uA"].append(current_uA)
            except (serial.SerialException, OSError) as exc:
                close_serial()
                set_status(f"Serial connection lost: {exc}")

        times = _relative_device_seconds(device_data)
        currents = np.asarray(device_data["current_uA"], dtype=float)
        full_line.set_data(times, currents)
        if times.size:
            recent_mask = times >= max(0.0, times[-1] - args.window_seconds)
            recent_line.set_data(times[recent_mask], currents[recent_mask])
        else:
            recent_line.set_data([], [])

        expected_current = None
        if meta.get("potential_mV") is not None and args.resistor_ohm > 0:
            expected_current = meta["potential_mV"] / 1000.0 / args.resistor_ohm * 1e6
        show_expected = expected_visible[0] and expected_current is not None
        for line in (expected_full, expected_recent):
            line.set_visible(show_expected)
            if show_expected:
                line.set_ydata([expected_current, expected_current])

        current_overlay_signature = tuple(
            (label, len(times_s), len(currents_uA))
            for label, times_s, currents_uA in overlays)
        if current_overlay_signature != overlay_signature[0]:
            overlay_signature[0] = current_overlay_signature
            rebuild_overlay_lines()
            legend_signature[0] = None
        refresh_legends(show_expected)
        for axis in (ax_full, ax_recent):
            if auto_scale[0]:
                update_axis_limits(axis, bool(times.size), args.window_seconds)
        draw_info()
        title_mode = f"review: {review_name[0]}" if review_name[0] else (
            f"{active_port[0]} @ {args.baud} baud" if ser is not None else "offline")
        fig.suptitle(f"Amperometry dashboard — {title_mode}", fontsize=14)
        return [full_line, recent_line, expected_full, expected_recent, *overlay_lines]

    animation = FuncAnimation(fig, update, interval=150, blit=False,
                              cache_frame_data=False)
    fig._amp_animation = animation

    def on_close(_event):
        layout_timer.stop()
        close_serial()

    fig.canvas.mpl_connect("close_event", on_close)
    update(0)
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
