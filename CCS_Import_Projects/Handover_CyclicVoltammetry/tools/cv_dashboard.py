#!/usr/bin/env python3
"""
cv_dashboard.py
================
Live dashboard for MSP430FR5994_AD5941_CyclicVoltammetry.

Reads the cyclic-voltammetry data the firmware streams over its backchannel
UART (MSP debug probe, 9600 baud 8N1) and plots it live while the
debugger is connected - no SD card needed.

  - Left panel : anodic-positive current (uA) vs conventional WE-minus-RE
                 potential (V), the classic CV loop. The last few sweeps are
                 overlaid in fading blue, the
                 in-progress sweep is drawn live in red.
  - Right panel: current (uA) vs time-in-sweep (ms) for the in-progress
                 sweep, useful for spotting ADC glitches/noise that get
                 hidden once the data is folded into the I-V loop.

The firmware sends a one-time metadata header right after
AD5940RampStructInit():

    # CV_META_START_MV,<RampStartVolt>
    # CV_META_PEAK_MV,<RampPeakVolt>
    # CV_META_STEPS,<StepNumber>
    # CV_META_DURATION_MS,<RampDuration>
    # CV_META_POTENTIAL_CONVENTION,E_WE_MINUS_E_RE
    # CV_META_CURRENT_CONVENTION,ANODIC_POSITIVE

followed by one ASCII float (current in uA) per ADC sample, with a
"Next Cycle." line marking the end of each sweep. The potential axis is
reconstructed in the conventional laboratory WE-minus-RE polarity from this
metadata, so this script stays correct while you
tune AD5940RampStructInit() without needing to edit it here too.

Requires: pyserial, numpy, matplotlib   (pip install pyserial)

Usage:
    python3 cv_dashboard.py --list-ports
    python3 cv_dashboard.py --port /dev/tty.usbmodemXXXX
    python3 cv_dashboard.py --port /dev/tty.usbmodemXXXX --csv run1.csv
    python3 cv_dashboard.py --offline
    python3 cv_dashboard.py --session saved_session.csv

Use the Save session button to export completed device sweeps, loaded Metrohm
references, saved-session overlays, firmware metadata, persistent notes, and a
PNG of the current comparison view. Overlay session adds the device sweeps from
a previous dashboard session without replacing the current run. Use Review
session (or --session) to reopen a complete comparison without a connected
device. If the configured port is unavailable, the dashboard opens offline so
Connect device can retry after it is attached.
"""

import argparse
import csv
import json
import re
import sys
import time
import textwrap
import unicodedata
from datetime import datetime
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import serial
import serial.tools.list_ports
from matplotlib.widgets import Button, TextBox

# ── Serial port for the XDS110 backchannel UART ──────────────────────────────
PORT = "/dev/cu.usbmodem11303"
BAUD = 9600
MAX_CURRENT_UA = 2000.0  # physical max for 1kΩ RTIA / 1820 mV ADC ref ≈ 1820 µA
MAX_CYCLES = 4           # number of cycles to collect and overlay; change this to taste
SKIP_INITIAL_SAMPLES = 10 # drop this many samples from the start of each sweep to remove
                          # the capacitive charging transient; set to 0 to keep all data
# Firmware sends exactly DDDD.DDDD (4 integer digits, 4 fractional). Reject anything
# that does not match this pattern — it is a UART corruption artefact.
VALUE_RE = re.compile(r"^-?\d{4}\.\d{4}$")
# One distinct colour per cycle (matplotlib tab10 palette, first MAX_CYCLES entries).
CYCLE_COLORS = [
    "tab:blue", "tab:orange", "tab:green", "tab:red", "tab:purple",
    "tab:brown", "tab:pink", "tab:gray", "tab:olive", "tab:cyan",
]
# Distinct colours for static reference overlays (e.g. Metrohm exports), kept
# visually separate from the tab10 cycle palette above.
OVERLAY_COLORS = ["black", "dimgray", "saddlebrown", "darkmagenta"]
# Dashed colours reserved for device traces imported from saved sessions.
SESSION_OVERLAY_COLORS = [
    "#7B2CBF",  # purple
    "#00897B",  # teal
    "#E67E22",  # orange
    "#C62828",  # red
    "#455A64",  # blue-grey
    "#AD1457",  # magenta
]
SESSION_FORMAT_VERSION = 3
# ─────────────────────────────────────────────────────────────────────────────


def _normalise_textbox_key(key, shift_down=False):
    """Translate backend-specific Shift+letter keys into normal capitals."""
    if not key:
        return key
    lowered = key.lower()
    if lowered.startswith("shift+") and len(key.split("+", 1)[1]) == 1:
        return key.split("+", 1)[1].upper()
    if shift_down and len(key) == 1 and key.isalpha():
        return key.upper()
    return key


class NotesTextBox(TextBox):
    """TextBox that accepts capital letters consistently on macOS backends."""

    def __init__(self, *args, **kwargs):
        self._shift_down = False
        super().__init__(*args, **kwargs)
        self.connect_event("key_release_event", self._keyrelease)

    def _keypress(self, event):
        key = event.key or ""
        if key in {"shift", "shift_l", "shift_r"}:
            self._shift_down = True
            return
        original_key = event.key
        event.key = _normalise_textbox_key(event.key, self._shift_down)
        try:
            super()._keypress(event)
        finally:
            event.key = original_key

    def _keyrelease(self, event):
        if (event.key or "") in {"shift", "shift_l", "shift_r"}:
            self._shift_down = False


def _filename_slug(value, max_length=56):
    """Return a readable, filesystem-safe fragment without losing capitals."""
    normalised = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")
    return slug[:max_length].rstrip("_")


def session_default_filename(meta, purpose="AA_precheck", timestamp=None,
                             directory=None):
    """Suggest the next date/technique/purpose/run name in a directory."""
    timestamp = timestamp or datetime.now()
    technique = "LSV" if meta.get("one_dir") else "CV"
    purpose_slug = _filename_slug(purpose) or "session"
    stem = f"{timestamp:%Y-%m-%d}_{technique}_{purpose_slug}"
    directory = Path(directory or Path.cwd())
    run = 1
    while any((directory / f"{stem}_Run{run:02d}{suffix}").exists()
              for suffix in (".csv", ".CSV", ".png")):
        run += 1
    return f"{stem}_Run{run:02d}.csv"


def parse_cycle_selection(specification, available_cycles):
    """Parse selections such as ``1-4``, ``2,5,8`` or ``all``."""
    available = [int(cycle) for cycle in available_cycles]
    available_set = set(available)
    text = str(specification or "").strip().lower()
    if text == "all":
        return available
    if not text:
        raise ValueError("enter a cycle selection")

    selected = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-", 1)
            try:
                start, stop = (int(part.strip()) for part in parts)
            except ValueError as exc:
                raise ValueError(f"invalid cycle range: {token!r}") from exc
            if stop < start:
                raise ValueError(f"cycle range is backwards: {token!r}")
            selected.update(range(start, stop + 1))
        else:
            try:
                selected.add(int(token))
            except ValueError as exc:
                raise ValueError(f"invalid cycle: {token!r}") from exc

    unavailable = sorted(selected - available_set)
    if unavailable:
        raise ValueError(
            "cycle(s) not present: " + ", ".join(map(str, unavailable)))
    return [cycle for cycle in available if cycle in selected]


def default_cycle_selection(available_cycles, maximum=MAX_CYCLES):
    """Return a concise default selecting the first dashboard-visible cycles."""
    cycles = [int(cycle) for cycle in available_cycles[:maximum]]
    if not cycles:
        return ""
    if cycles == list(range(cycles[0], cycles[-1] + 1)):
        return str(cycles[0]) if len(cycles) == 1 else f"{cycles[0]}-{cycles[-1]}"
    return ",".join(map(str, cycles))


def session_overlay_group(label):
    """Return the session-level portion of a stored overlay trace label."""
    return str(label).rsplit(" — Cycle ", 1)[0]


def session_overlay_group_count(session_overlays):
    return len({session_overlay_group(label) for label, _x, _y in session_overlays})


def lighten_color(color, amount):
    """Blend a colour towards white by ``amount`` in the range 0..1."""
    rgb = np.asarray(matplotlib.colors.to_rgb(color), dtype=float)
    amount = float(np.clip(amount, 0.0, 1.0))
    return tuple(rgb + (1.0 - rgb) * amount)


def session_overlay_style(group_index, trace_index=0, trace_count=1):
    """Return one session colour, progressively lightened across its sweeps."""
    base_color = SESSION_OVERLAY_COLORS[
        group_index % len(SESSION_OVERLAY_COLORS)]
    if trace_count <= 1:
        lightening = 0.0
    else:
        lightening = 0.55 * trace_index / (trace_count - 1)
    return lighten_color(base_color, lightening), "-"


def draw_session_overlay_curves(ax, session_overlays):
    """Draw many imported cycles but create only one legend item per session."""
    group_indices = {}
    labelled_groups = set()
    group_counts = {}
    group_positions = {}
    for label, _potentials, _currents in session_overlays:
        group = session_overlay_group(label)
        group_counts[group] = group_counts.get(group, 0) + 1
    for label, potentials, currents in session_overlays:
        group = session_overlay_group(label)
        group_index = group_indices.setdefault(group, len(group_indices))
        trace_index = group_positions.get(group, 0)
        group_positions[group] = trace_index + 1
        legend_label = "_nolegend_"
        if group not in labelled_groups:
            legend_label = f"{group} (session)"
            labelled_groups.add(group)
        color, linestyle = session_overlay_style(
            group_index, trace_index, group_counts[group])
        ax.plot(
            potentials, currents,
            color=color, linewidth=1.8, linestyle=linestyle, alpha=0.92,
            label=legend_label)


def store_visible_session_sweep(session_sweeps, sweep, maximum=MAX_CYCLES):
    """Store only sweeps represented by the dashboard's fixed comparison."""
    if len(session_sweeps) >= maximum:
        return False
    session_sweeps.append(sweep)
    return True

def list_ports():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    print("Available serial ports:")
    for p in ports:
        print(f"  {p.device:30s} {p.description}")


def serial_port_candidates(preferred):
    """Return safe connection candidates, preferring the configured port.

    A single discovered macOS ``/dev/cu.usbmodem*`` port is accepted as a
    fallback because XDS110 identifiers can change after reconnecting. Multiple
    USB modem ports are not guessed between; the configured value remains the
    only candidate in that ambiguous case.
    """
    candidates = [preferred]
    discovered = [
        port.device for port in serial.tools.list_ports.comports()
        if port.device.startswith("/dev/cu.usbmodem")
    ]
    if preferred in discovered:
        return candidates
    if len(discovered) == 1:
        candidates.append(discovered[0])
    return candidates


def open_serial_connection(preferred, baud):
    """Open the configured serial port, with a safe single-port fallback."""
    errors = []
    for device in serial_port_candidates(preferred):
        try:
            return serial.Serial(device, baud, timeout=0), device
        except (serial.SerialException, OSError) as exc:
            errors.append(f"{device}: {exc}")
    raise serial.SerialException("; ".join(errors) or "no serial port available")


class LineReader:
    """Non-blocking newline-delimited reader for a pyserial port."""

    def __init__(self, ser):
        self.ser = ser
        self.buf = b""

    def read_lines(self):
        n = self.ser.in_waiting
        if n:
            self.buf += self.ser.read(n)
        lines = []
        while b"\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n", 1)
            lines.append(raw.decode("utf-8", errors="ignore").strip())
        return lines


def parse_meta_line(line, meta):
    try:
        if line.startswith("# CV_META_START_MV,"):
            meta["start_mV"] = float(line.split(",", 1)[1])
        elif line.startswith("# CV_META_PEAK_MV,"):
            meta["peak_mV"] = float(line.split(",", 1)[1])
        elif line.startswith("# CV_META_STEPS,"):
            meta["steps"] = int(float(line.split(",", 1)[1]))
        elif line.startswith("# CV_META_DURATION_MS,"):
            meta["duration_ms"] = float(line.split(",", 1)[1])
        elif line.startswith("# CV_META_ONE_DIR,"):
            meta["one_dir"] = float(line.split(",", 1)[1]) > 0.5
        elif line.startswith("# CV_META_SAMPLE_DELAY_MS,"):
            meta["sample_delay_ms"] = float(line.split(",", 1)[1])
        elif line.startswith("# CV_META_VZERO_MV,"):
            meta["vzero_mV"] = float(line.split(",", 1)[1])
        elif line.startswith("# CV_META_POTENTIAL_CONVENTION,"):
            meta["potential_convention"] = line.split(",", 1)[1].strip()
        elif line.startswith("# CV_META_CURRENT_CONVENTION,"):
            meta["current_convention"] = line.split(",", 1)[1].strip()
    except (ValueError, IndexError):
        pass


def reconstruct_potential_v(n, meta, one_direction, skip=0):
    """Reconstruct the potential axis (in volts) for n samples starting after skip samples.

    Uses the same DAC step arithmetic as the AD5941 firmware so the forward and
    reverse sweeps in CV mode share a consistent potential axis (no false loop).
      LSV firmware: DACCodePerStep = range / StepNumber
      CV  firmware: DACCodePerStep = range * 2 / StepNumber, turnaround at StepNumber//2

    Firmware metadata is expressed in the conventional electrochemical
    potential E_WE - E_RE. The firmware performs the required sign conversion
    to the AD5941's internal VBIAS - VZERO ramp before programming the DAC.
    VzeroStart sets the internal common-mode point, not an offset on this axis.
    """
    if n <= 0 or meta["start_mV"] is None or meta["peak_mV"] is None:
        return np.arange(n, dtype=float)

    start = meta["start_mV"] / 1000.0
    peak  = meta["peak_mV"]  / 1000.0
    total = max(meta["steps"] or (n + skip), n + skip)

    if one_direction:
        step = (peak - start) / total          # matches firmware: range / StepNumber
        full = start + np.arange(total) * step
    else:
        step  = (peak - start) * 2.0 / total  # matches firmware: range*2 / StepNumber
        fwd_n = total // 2                     # firmware turnaround: StepNumber // 2
        rev_n = total - fwd_n
        rising  = start + np.arange(fwd_n) * step
        falling = (start + fwd_n * step) - np.arange(rev_n) * step
        full = np.concatenate([rising, falling])

    return full[skip: skip + n]


def load_overlay_csv(path):
    """Load a reference CV/LSV CSV (e.g. a Metrohm DropView export) for overlay.

    Handles the DropView format: a title line and blank line precede a
    'Potential (V)';'Current (uA)' header, then data rows. Fields are
    double-quoted, semicolon-delimited (comma fallback), decimal point.
    Non-numeric lines (title, header, blanks) are skipped automatically, so
    this also accepts a plain two-column potential,current CSV.

    Returns (label, potential_V_array, current_uA_array). Units are assumed
    already in volts and microamps, matching the dashboard axes.
    """
    path = Path(path)
    potentials, currents = [], []
    with path.open("r", encoding="utf-8-sig", errors="ignore") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            fields = line.split(";")
            if len(fields) < 2:
                fields = line.split(",")
            if len(fields) < 2:
                continue
            try:
                p = float(fields[0].strip().strip('"').strip())
                i = float(fields[1].strip().strip('"').strip())
            except ValueError:
                continue  # title / header / any non-numeric row
            potentials.append(p)
            currents.append(i)
    if not potentials:
        raise ValueError(f"no numeric 'potential;current' rows found in {path.name}")
    return path.stem, np.array(potentials), np.array(currents)


def session_csv_path(path):
    """Return a Path with a .csv suffix for a user-selected session filename."""
    path = Path(path).expanduser()
    return path if path.suffix.lower() == ".csv" else path.with_suffix(".csv")


def write_session_csv(path, device_sweeps, overlays, meta, notes=None,
                      saved_at=None, session_overlays=None):
    """Write completed device sweeps and loaded references to one long-form CSV.

    Device sweeps are dictionaries containing ``cycle``, ``potential_V`` and
    ``current_uA``. Overlay tuples use the same shape returned by
    :func:`load_overlay_csv`. Firmware metadata is repeated on device rows so
    every measurement remains interpretable when rows are filtered or copied.
    """
    path = session_csv_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "source", "label", "cycle", "potential_V", "current_uA",
        "sample_index", "time_ms",
        "technique", "start_mV", "peak_mV", "steps", "duration_ms",
        "sample_delay_ms", "vzero_mV", "potential_convention",
        "current_convention", "session_format_version", "saved_at",
        "session_notes_json",
    ]
    notes = list(notes or [])
    session_overlays = list(session_overlays or [])
    saved_at = saved_at or datetime.now().astimezone().isoformat(timespec="seconds")
    session_values = {
        "session_format_version": SESSION_FORMAT_VERSION,
        "saved_at": saved_at,
        "session_notes_json": json.dumps(notes, ensure_ascii=False),
    }
    technique = "LSV" if meta.get("one_dir") else "CV"
    device_meta = {
        "technique": technique,
        "start_mV": meta.get("start_mV"),
        "peak_mV": meta.get("peak_mV"),
        "steps": meta.get("steps"),
        "duration_ms": meta.get("duration_ms"),
        "sample_delay_ms": meta.get("sample_delay_ms"),
        "vzero_mV": meta.get("vzero_mV"),
        "potential_convention": meta.get("potential_convention", "E_WE_MINUS_E_RE"),
        "current_convention": meta.get("current_convention", "ANODIC_POSITIVE"),
    }

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for sweep in device_sweeps:
            cycle = int(sweep["cycle"])
            stored_times = sweep.get("time_ms")
            for sample_index, (potential, current) in enumerate(
                    zip(sweep["potential_V"], sweep["current_uA"])):
                if stored_times is not None and sample_index < len(stored_times):
                    time_ms = float(stored_times[sample_index])
                elif meta.get("duration_ms") and meta.get("steps"):
                    time_ms = (float(meta["duration_ms"])
                               * (SKIP_INITIAL_SAMPLES + sample_index)
                               / float(meta["steps"]))
                else:
                    time_ms = ""
                writer.writerow({
                    "source": "device",
                    "label": f"Device Cycle {cycle}",
                    "cycle": cycle,
                    "potential_V": f"{float(potential):.9g}",
                    "current_uA": f"{float(current):.9g}",
                    "sample_index": sample_index,
                    "time_ms": f"{time_ms:.9g}" if time_ms != "" else "",
                    **device_meta,
                    **session_values,
                })
        for label, potentials, currents in overlays:
            for potential, current in zip(potentials, currents):
                writer.writerow({
                    "source": "metrohm",
                    "label": label,
                    "cycle": "",
                    "potential_V": f"{float(potential):.9g}",
                    "current_uA": f"{float(current):.9g}",
                    **session_values,
                })
        for label, potentials, currents in session_overlays:
            for potential, current in zip(potentials, currents):
                writer.writerow({
                    "source": "session_overlay",
                    "label": label,
                    "cycle": "",
                    "potential_V": f"{float(potential):.9g}",
                    "current_uA": f"{float(current):.9g}",
                    **session_values,
                })
    return path


def load_session_csv(path):
    """Load a dashboard session CSV, including legacy version-1 exports."""
    path = Path(path).expanduser()
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = set(reader.fieldnames or [])
        required = {"source", "label", "potential_V", "current_uA"}
        if not required.issubset(fieldnames):
            raise ValueError("not a dashboard session CSV")
        rows = list(reader)

    if not rows:
        raise ValueError("session CSV contains no measurements")

    device_groups = {}
    overlay_groups = {}
    session_overlay_groups = {}
    first_device = None
    first_session = rows[0]
    for row_number, row in enumerate(rows, start=2):
        source = (row.get("source") or "").strip().lower()
        try:
            potential = float(row["potential_V"])
            current = float(row["current_uA"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid measurement on CSV row {row_number}") from exc

        if source == "device":
            if first_device is None:
                first_device = row
            try:
                cycle = int(float(row.get("cycle") or 0))
            except ValueError as exc:
                raise ValueError(f"invalid device cycle on CSV row {row_number}") from exc
            group = device_groups.setdefault(cycle, {
                "cycle": cycle, "potential_V": [], "current_uA": [],
                "time_ms": [], "has_time": True,
            })
            group["potential_V"].append(potential)
            group["current_uA"].append(current)
            try:
                group["time_ms"].append(float(row.get("time_ms") or ""))
            except ValueError:
                group["has_time"] = False
        elif source == "metrohm":
            label = (row.get("label") or "Metrohm reference").strip()
            group = overlay_groups.setdefault(label, [[], []])
            group[0].append(potential)
            group[1].append(current)
        elif source == "session_overlay":
            label = (row.get("label") or "Saved session").strip()
            group = session_overlay_groups.setdefault(label, [[], []])
            group[0].append(potential)
            group[1].append(current)

    if not device_groups and not overlay_groups and not session_overlay_groups:
        raise ValueError("session contains no device or overlay measurements")

    device_sweeps = []
    for group in device_groups.values():
        sweep = {
            "cycle": group["cycle"],
            "potential_V": np.asarray(group["potential_V"], dtype=float),
            "current_uA": np.asarray(group["current_uA"], dtype=float),
        }
        if group["has_time"] and len(group["time_ms"]) == len(group["current_uA"]):
            sweep["time_ms"] = np.asarray(group["time_ms"], dtype=float)
        device_sweeps.append(sweep)

    overlays = [
        (label, np.asarray(values[0], dtype=float),
         np.asarray(values[1], dtype=float))
        for label, values in overlay_groups.items()
    ]
    session_overlays = [
        (label, np.asarray(values[0], dtype=float),
         np.asarray(values[1], dtype=float))
        for label, values in session_overlay_groups.items()
    ]

    source_meta = first_device or first_session
    numeric_fields = {
        "start_mV": float, "peak_mV": float, "steps": int,
        "duration_ms": float, "sample_delay_ms": float, "vzero_mV": float,
    }
    meta = {}
    for key, converter in numeric_fields.items():
        value = (source_meta.get(key) or "").strip()
        if value:
            meta[key] = converter(float(value)) if converter is int else converter(value)
    technique = (source_meta.get("technique") or "CV").strip().upper()
    meta["one_dir"] = technique == "LSV"
    meta["potential_convention"] = (
        source_meta.get("potential_convention") or "E_WE_MINUS_E_RE")
    meta["current_convention"] = (
        source_meta.get("current_convention") or "ANODIC_POSITIVE")

    notes = []
    notes_json = first_session.get("session_notes_json") or ""
    if notes_json:
        try:
            decoded = json.loads(notes_json)
            if isinstance(decoded, list):
                notes = [str(note) for note in decoded if str(note).strip()]
        except json.JSONDecodeError:
            notes = [notes_json]

    try:
        format_version = int(float(
            first_session.get("session_format_version") or 1))
    except ValueError:
        format_version = 1

    return {
        "path": path,
        "device_sweeps": device_sweeps,
        "overlays": overlays,
        "session_overlays": session_overlays,
        "meta": meta,
        "notes": notes,
        "saved_at": first_session.get("saved_at") or "",
        "format_version": format_version,
    }


def save_session_files(path, device_sweeps, overlays, meta, fig, notes=None,
                       saved_at=None, session_overlays=None):
    """Save session data as CSV and the current dashboard view as a PNG."""
    csv_path = write_session_csv(
        path, device_sweeps, overlays, meta, notes=notes, saved_at=saved_at,
        session_overlays=session_overlays)
    png_path = csv_path.with_suffix(".png")
    fig.savefig(png_path, dpi=200, bbox_inches="tight")
    return csv_path, png_path


def open_file_dialog(prompt="Select reference CSV"):
    """Open a native 'select file' dialog and return the chosen path, or None.

    Backend-aware, because the wrong toolkit is fatal, not just unavailable:
      1. Qt  — only if a QApplication is already running (Qt matplotlib backend).
      2. macOS — AppleScript (osascript) native dialog. This runs in a separate
         process, so it works with matplotlib's macOS (Cocoa) backend. tkinter
         must NOT be used here: Tk and the Cocoa backend both try to own the
         NSApplication and the process crashes with an NSException.
      3. Other platforms — tkinter.
    Returns None on cancel or if no usable dialog is available.
    """
    # 1. Qt (Spyder/Qt backends) — reuse the running QApplication if present.
    try:
        from matplotlib.backends.qt_compat import QtWidgets
        if QtWidgets.QApplication.instance() is not None:
            fname, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, prompt, "",
                "CSV files (*.csv);;All files (*)")
            return fname or None
    except Exception:
        pass

    # 2. macOS: AppleScript dialog (safe with any backend, incl. Cocoa).
    if sys.platform == "darwin":
        try:
            import subprocess
            script = f'POSIX path of (choose file with prompt "{prompt}")'
            res = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True)
            if res.returncode == 0:          # non-zero == user cancelled
                return res.stdout.strip() or None
            return None
        except Exception:
            return None

    # 3. Other platforms: tkinter.
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        fname = filedialog.askopenfilename(
            title=prompt,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        root.destroy()
        return fname or None
    except Exception:
        return None


def save_file_dialog(default_name, directory=None):
    """Open a native Save dialog and return the selected path, or None."""
    try:
        from matplotlib.backends.qt_compat import QtWidgets
        if QtWidgets.QApplication.instance() is not None:
            fname, _ = QtWidgets.QFileDialog.getSaveFileName(
                None, "Save dashboard session", str(Path(directory or Path.cwd()) / default_name),
                "CSV files (*.csv);;All files (*)")
            return fname or None
    except Exception:
        pass

    if sys.platform == "darwin":
        try:
            import subprocess
            script = (
                'POSIX path of (choose file name with prompt "Save dashboard session" '
                f'default name "{default_name}" '
                f'default location POSIX file "{str(Path(directory or Path.cwd()))}")'
            )
            res = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True)
            if res.returncode == 0:
                return res.stdout.strip() or None
            return None
        except Exception:
            return None

    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        fname = filedialog.asksaveasfilename(
            title="Save dashboard session",
            initialfile=default_name,
            initialdir=str(directory or Path.cwd()),
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        root.destroy()
        return fname or None
    except Exception:
        return None


def text_input_dialog(title, prompt, default="", accept_label="OK"):
    """Open a native single-line text prompt and return text, or None on cancel."""
    try:
        from matplotlib.backends.qt_compat import QtWidgets
        if QtWidgets.QApplication.instance() is not None:
            value, accepted = QtWidgets.QInputDialog.getText(
                None, title, prompt, text=str(default))
            return str(value) if accepted else None
    except Exception:
        pass

    if sys.platform == "darwin":
        try:
            import subprocess

            def apple_quote(value):
                return str(value).replace("\\", "\\\\").replace('"', '\\"')

            script = (
                f'text returned of (display dialog "{apple_quote(prompt)}" '
                f'with title "{apple_quote(title)}" '
                f'default answer "{apple_quote(default)}" '
                f'buttons {{"Cancel", "{apple_quote(accept_label)}"}} '
                f'default button "{apple_quote(accept_label)}" '
                'cancel button "Cancel")'
            )
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    try:
        import tkinter
        from tkinter import simpledialog
        root = tkinter.Tk()
        root.withdraw()
        value = simpledialog.askstring(
            title, prompt, initialvalue=str(default), parent=root)
        root.destroy()
        return value
    except Exception:
        return None


def draw_info_panel(ax_info, meta, baud, notes=None, review_name="",
                    saved_at=""):
    """Draw measurement parameters and the most recent persistent notes."""
    technique = "LSV" if meta.get("one_dir") else "CV"
    vzero = meta.get("vzero_mV") or 0.0
    start = meta.get("start_mV") or 0.0
    peak = meta.get("peak_mV") or 0.0
    steps = meta.get("steps") or 0
    dur_ms = meta.get("duration_ms") or 0.0
    v_range = abs(peak - start)
    if meta.get("one_dir"):
        step_mv = v_range / steps if steps else 0.0
    else:
        step_mv = v_range * 2 / steps if steps else 0.0
    if dur_ms and v_range:
        divisor = dur_ms / 1000.0 if meta.get("one_dir") else dur_ms / 2000.0
        scan_rate = v_range / divisor
    else:
        scan_rate = 0.0
    step_period_ms = dur_ms / steps if steps else 0.0
    sample_delay_ms = meta.get("sample_delay_ms")
    if sample_delay_ms is not None:
        delay_ok = sample_delay_ms < step_period_ms
        delay_str = f"{sample_delay_ms:.0f} ms {'✓' if delay_ok else '✗ TOO LARGE'}"
    else:
        delay_str = "-- ms"

    def row(label, value):
        return f"{label:<12}{value}"

    info_lines = [
        row("Technique:", technique),
        "─" * 22,
        row("Start:", f"{start:.0f} mV"),
        row("Peak:", f"{peak:.0f} mV"),
        row("Range:", f"{v_range:.0f} mV"),
        row("Vzero:", f"{vzero:.0f} mV"),
        "─" * 22,
        row("Steps:", f"{steps}"),
        row("Step size:", f"{step_mv:.2f} mV"),
        row("Scan rate:", f"{scan_rate:.1f} mV/s"),
        row("Duration:", f"{dur_ms/1000:.1f} s"),
        "─" * 22,
        row("Step period:", f"{step_period_ms:.1f} ms"),
        row("Smpl delay:", delay_str),
        "─" * 22,
        row("Skip:", f"{SKIP_INITIAL_SAMPLES} samples"),
        row("Max cycles:", f"{MAX_CYCLES}"),
        row("Port:", "review only" if review_name else f"{baud} baud"),
    ]
    ax_info.clear()
    ax_info.axis("off")
    ax_info.text(
        0.5, 0.98, "\n".join(info_lines), transform=ax_info.transAxes,
        fontsize=8.2, verticalalignment="top", ha="center",
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.55", facecolor="#fffde7",
                  edgecolor="#cccccc", linewidth=0.8),
    )
    ax_info.set_title(
        "Session review" if review_name else "Parameters",
        fontsize=10, fontweight="bold")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--port", default=PORT,
        help=f"Serial port for the XDS110 backchannel UART (default: {PORT})."
    )
    parser.add_argument(
        "--baud", type=int, default=BAUD,
        help=f"Baud rate (default: {BAUD}, matches initUart())."
    )
    parser.add_argument(
        "--csv", help="Optional path to append each completed sweep's (potential_V, current_uA) to as CSV."
    )
    parser.add_argument(
        "--overlay", action="append", metavar="CSV", default=[],
        help="Overlay a static reference CV/LSV CSV (e.g. a Metrohm export) on the "
             "loop panel for comparison. Repeat the flag to overlay several references."
    )
    parser.add_argument(
        "--session", metavar="CSV",
        help="Open a previously saved dashboard session in review mode. No serial "
             "device is required until Return to live is selected."
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="Open without attempting a serial connection. Connect device can "
             "start acquisition later from the dashboard."
    )
    parser.add_argument("--list-ports", action="store_true", help="List available serial ports and exit.")
    args = parser.parse_args()

    if args.list_ports:
        list_ports()
        return

    # Seeded with the values from AD5940RampStructInit() so the plot works even
    # if the dashboard connects after the one-time boot header was already sent.
    # These are overwritten whenever a # CV_META_* line arrives or a session is
    # restored.
    meta = {
        "start_mV": -1000.0, "peak_mV": 1000.0, "steps": 150,
        "duration_ms": 2000.0, "one_dir": False,
        "sample_delay_ms": None, "vzero_mV": 1300.0,
        "potential_convention": "E_WE_MINUS_E_RE",
        "current_convention": "ANODIC_POSITIVE",
    }
    overlays = []          # (label, potential_V_array, current_uA_array)
    session_overlays = []  # device traces imported from prior saved sessions
    session_sweeps = []    # all completed sweeps since New run / in loaded file
    session_notes = []
    session_saved_at = [""]
    session_purpose = ["AA_precheck"]
    save_directory = [Path.cwd()]
    review_mode = [False]
    review_name = [""]
    active_port = [args.port]
    connection_error = [""]

    # Load any reference overlays up front; a bad file warns but doesn't abort.
    for ov_path in args.overlay:
        try:
            overlays.append(load_overlay_csv(ov_path))
            print(f"[overlay] loaded {ov_path} ({len(overlays[-1][1])} points)")
        except Exception as exc:
            print(f"[overlay] failed to load {ov_path}: {exc}")

    if args.session:
        try:
            loaded = load_session_csv(args.session)
        except Exception as exc:
            parser.error(f"could not load session {args.session!r}: {exc}")
        session_sweeps.extend(loaded["device_sweeps"])
        overlays[:] = loaded["overlays"]
        session_overlays[:] = loaded.get("session_overlays", [])
        meta.update(loaded["meta"])
        session_notes.extend(loaded["notes"])
        session_saved_at[0] = loaded["saved_at"]
        review_mode[0] = True
        review_name[0] = loaded["path"].name
        print(f"[session] loaded {loaded['path']} "
              f"({len(session_sweeps)} device cycles, {len(overlays)} references, "
              f"{session_overlay_group_count(session_overlays)} overlay sessions / "
              f"{len(session_overlays)} traces)")

    ser = None
    reader = None
    if not review_mode[0] and not args.offline:
        try:
            ser, active_port[0] = open_serial_connection(args.port, args.baud)
            reader = LineReader(ser)
            print(f"[serial] connected to {active_port[0]} @ {args.baud} baud")
        except (serial.SerialException, OSError) as exc:
            connection_error[0] = str(exc)
            print(f"[serial] unavailable; dashboard starting offline: {exc}")

    current_cycle = []
    history = []          # grows up to MAX_CYCLES, then stays fixed
    cycle_count = 0
    csv_cycle_count = 0   # never reset, so an active CSV keeps unique cycle IDs
    synced = [False]  # becomes True after first complete cycle boundary is seen
    awaiting_fresh_boundary = [False]
    pending_new_run = [False]

    csv_file = None
    csv_writer = None
    if args.csv:
        csv_path = Path(args.csv)
        is_new = not csv_path.exists() or csv_path.stat().st_size == 0
        csv_file = csv_path.open("a", newline="")
        csv_writer = csv.writer(csv_file)
        if is_new:
            csv_writer.writerow(["cycle", "potential_V", "current_uA"])

    fig, (ax_cv, ax_time, ax_info) = plt.subplots(
        1, 3, figsize=(14, 5.5),
        gridspec_kw={"width_ratios": [3, 3, 1.5]},
    )
    fig.subplots_adjust(left=0.15, bottom=0.15, wspace=0.35)
    # Reserve a dedicated upper-right panel for metadata. Controls and notes
    # occupy the lower-right column, so neither can cover the information box.
    ax_info.set_position([0.78, 0.52, 0.16, 0.35])
    ax_info.axis("off")
    fig.suptitle(
        f"CV dashboard - {active_port[0]} @ {args.baud} baud"
        if ser is not None else "CV dashboard — offline")

    # --- Quit button: closes the figure, which the main loop below already
    # treats as the signal to stop reading and clean up (serial port, CSV).
    # Keep all controls in one aligned column below the Parameters panel. This
    # avoids covering either data axes, including on narrower desktop windows.
    control_x = 0.79
    control_w = 0.13
    control_h = 0.040

    ax_quit = fig.add_axes([control_x, 0.01, control_w, control_h])
    quit_button = Button(ax_quit, "Quit", color="#f5c6c6", hovercolor="#e57373")
    quit_button.on_clicked(lambda _event: plt.close(fig))

    def finalize_cycle():
        nonlocal cycle_count, csv_cycle_count
        # --- axis-integrity diagnostic (handover step 2) --------------------
        # The reconstructed potential axis is only correct if the number of
        # valid sample-lines received this sweep equals StepNumber. A shortfall
        # means UART lines were dropped/rejected and every point's assigned
        # voltage is stretched — which would masquerade as a peak-potential
        # shift. Print raw-vs-expected each sweep so a mismatch is obvious.
        raw_count = len(current_cycle)                     # valid samples this sweep (pre-skip)
        expected = meta["steps"]                           # StepNumber from firmware metadata
        data = np.array(current_cycle[SKIP_INITIAL_SAMPLES:])
        pot = reconstruct_potential_v(len(data), meta, meta["one_dir"], skip=SKIP_INITIAL_SAMPLES)
        v_lo, v_hi = (float(pot.min()), float(pot.max())) if len(pot) else (float("nan"), float("nan"))
        if len(data):
            imax, imin = int(np.argmax(data)), int(np.argmin(data))
            # Anodic (oxidation) wave = most-positive current; report its E, plus
            # the most-negative for reference so the sign convention is unambiguous.
            imax_str = f"Imax {data[imax]:+.2f}uA @ E={float(pot[imax]):+.3f}V"
            imin_str = f"Imin {data[imin]:+.2f}uA @ E={float(pot[imin]):+.3f}V"
        else:
            imax_str = imin_str = "no data"
        flag = "" if (expected and raw_count == expected) else "  <-- MISMATCH (dropped lines?)"
        print(f"[sweep {cycle_count}] valid samples: {raw_count} / expected {expected}"
              f"  | plotted {len(data)}  | E range {v_lo:+.3f}..{v_hi:+.3f} V"
              f"  | {imax_str}  | {imin_str}{flag}")
        # -------------------------------------------------------------------
        last_time_data[0] = (cycle_count, data)  # keep for right panel until next sweep has data
        if len(history) < MAX_CYCLES:
            history.append(data)
        # The dashboard intentionally represents MAX_CYCLES sweeps. Keep the
        # saved session consistent with that visible comparison instead of
        # silently accumulating every later loop for as long as it is open.
        store_visible_session_sweep(
            session_sweeps, {
                "cycle": cycle_count + 1,
                "potential_V": np.array(pot, copy=True),
                "current_uA": np.array(data, copy=True),
            })
        if csv_writer:
            for e, i in zip(pot, data):
                csv_writer.writerow([csv_cycle_count, f"{e:.5f}", f"{i:.5f}"])
            csv_file.flush()
        csv_cycle_count += 1
        cycle_count += 1
        current_cycle.clear()

    lines_received = [0]
    cv_frozen = [False]   # True once all MAX_CYCLES are drawn; history stops redrawing
    cv_live_line = [None]  # persistent Line2D for the live sweep on the frozen panel
    last_time_data = [None]  # holds the most recently completed sweep for the right panel
    cv_legend_artist = [None]
    pending_overlay = [False]  # set by the Load-overlay button, serviced in update()
    pending_session_overlay = [False]
    pending_save = [False]     # keeps native dialogs outside widget callbacks
    pending_session = [False]  # load a complete dashboard session

    def request_new_run(_event):
        pending_new_run[0] = True

    def start_new_run():
        """Clear the displayed run without interrupting serial or CSV logging."""
        nonlocal cycle_count, ser, reader

        if ser is None:
            try:
                ser, active_port[0] = open_serial_connection(
                    args.port, args.baud)
                reader = LineReader(ser)
                connection_error[0] = ""
                print(f"[serial] connected to {active_port[0]} @ {args.baud} baud")
            except (serial.SerialException, OSError) as exc:
                connection_error[0] = str(exc)
                print(f"[dashboard] cannot connect to device: {exc}")
                draw_offline_view()
                return

        review_mode[0] = False
        review_name[0] = ""
        session_saved_at[0] = ""
        history.clear()
        session_sweeps.clear()
        session_notes.clear()
        current_cycle.clear()
        cycle_count = 0
        lines_received[0] = 0
        synced[0] = False
        awaiting_fresh_boundary[0] = True
        cv_frozen[0] = False
        cv_live_line[0] = None
        last_time_data[0] = None
        save_button.label.set_text("Save session")
        new_run_button.label.set_text("New run")
        load_session_button.label.set_text("Review session")
        session_overlay_button.label.set_text(
            f"Overlay session ({session_overlay_group_count(session_overlays)})"
            if session_overlays else "Overlay session")
        note_prompt_text.set_text("Add a session note")
        note_prompt_text.set_visible(True)
        ax_note.set_visible(True)

        if cv_legend_artist[0] is not None:
            cv_legend_artist[0].remove()
            cv_legend_artist[0] = None

        # Drop only a locally buffered partial UART line. Do not flush the serial
        # input buffer: it may already contain fresh metadata from a device reset.
        if reader is not None:
            reader.buf = b""

        ax_cv.clear()
        ax_cv.set_title("Waiting for next complete sweep boundary...")
        ax_cv.set_xlabel("Potential (V)")
        ax_cv.set_ylabel("Current (µA)")
        ax_cv.grid(True, alpha=0.3)

        ax_time.clear()
        ax_time.set_title("Waiting for a fresh sweep")
        ax_time.set_xlabel("Time in sweep (ms)")
        ax_time.set_ylabel("Current (µA)")
        ax_time.grid(True, alpha=0.3)

        print("[dashboard] new run requested; waiting for next sweep boundary")
        fig.canvas.draw_idle()

    def draw_offline_view():
        """Render a usable dashboard when no serial device is connected."""
        if cv_legend_artist[0] is not None:
            cv_legend_artist[0].remove()
            cv_legend_artist[0] = None
        ax_cv.clear()
        ax_cv.set_title("Offline — no device connected")
        ax_cv.set_xlabel("Potential (V)")
        ax_cv.set_ylabel("Current (µA)")
        ax_cv.grid(True, alpha=0.25)
        ax_cv.text(
            0.5, 0.53, "Connect the device and press\nConnect device",
            transform=ax_cv.transAxes, ha="center", va="center", fontsize=12,
            color="#555555")

        ax_time.clear()
        ax_time.set_title("Saved sessions are available offline")
        ax_time.set_xlabel("Time in sweep (ms)")
        ax_time.set_ylabel("Current (µA)")
        ax_time.grid(True, alpha=0.25)
        ax_time.text(
            0.5, 0.53, "Press Review session to open\na previous experiment",
            transform=ax_time.transAxes, ha="center", va="center", fontsize=12,
            color="#555555")

        ax_info.clear()
        ax_info.axis("off")
        error_summary = connection_error[0] or "Started in offline mode"
        if len(error_summary) > 100:
            error_summary = error_summary[:97] + "..."
        info_lines = [
            "No serial connection",
            "─" * 22,
            "Configured port:",
            *textwrap.wrap(args.port, width=22),
            "",
            f"Baud: {args.baud}",
            "",
            *textwrap.wrap(error_summary, width=22),
            "",
            "Attach the device, then",
            "press Connect device.",
        ]
        ax_info.text(
            0.5, 0.95, "\n".join(info_lines), transform=ax_info.transAxes,
            fontsize=8.5, verticalalignment="top", ha="center",
            fontfamily="monospace",
            bbox=dict(boxstyle="round,pad=0.6", facecolor="#f2f2f2",
                      edgecolor="#bbbbbb", linewidth=0.8))
        ax_info.set_title("Offline", fontsize=10, fontweight="bold")
        fig.suptitle("CV/LSV dashboard — offline | session review available")
        new_run_button.label.set_text("Connect device")
        load_session_button.label.set_text("Review session")
        note_prompt_text.set_visible(False)
        ax_note.set_visible(False)
        fig.canvas.draw_idle()

    def cv_legend():
        # Figure-level legend, tightly anchored just left of the CV loop axes
        # (left=0.15), so it sits in its own column instead of covering the
        # traces (which otherwise fill the whole potential range top-to-bottom)
        # or leaving a big dead gap. fig.legend() doesn't self-replace like
        # ax.legend() does, so the previous one is removed by hand each call.
        handles, labels = ax_cv.get_legend_handles_labels()
        if cv_legend_artist[0] is not None:
            cv_legend_artist[0].remove()
            cv_legend_artist[0] = None
        if handles:
            cv_legend_artist[0] = fig.legend(
                handles, labels, loc="upper left", bbox_to_anchor=(0.01, 0.90), fontsize=8,
            )

    def draw_review_view():
        """Render the stored session without any live-measurement artists."""
        ax_cv.clear()
        for k, (label, potentials, currents) in enumerate(overlays):
            ax_cv.plot(
                potentials, currents,
                color=OVERLAY_COLORS[k % len(OVERLAY_COLORS)],
                linewidth=2.0, alpha=0.9, label=f"{label} (ref)")
        draw_session_overlay_curves(ax_cv, session_overlays)
        for idx, sweep in enumerate(session_sweeps):
            ax_cv.plot(
                sweep["potential_V"], sweep["current_uA"],
                color=CYCLE_COLORS[idx % len(CYCLE_COLORS)], linewidth=1.5,
                label=f"Cycle {sweep['cycle']}")
        ax_cv.set_title(
            f"Saved comparison [{len(session_sweeps)} device cycles] — zoom/pan freely")
        ax_cv.set_xlabel("Potential (V)")
        ax_cv.set_ylabel("Current (µA)")
        ax_cv.grid(True, alpha=0.3)
        cv_legend()

        ax_time.clear()
        if session_sweeps:
            sweep = session_sweeps[-1]
            currents = sweep["current_uA"]
            times = sweep.get("time_ms")
            if times is None or len(times) != len(currents):
                if meta.get("duration_ms") and meta.get("steps"):
                    times = (float(meta["duration_ms"])
                             * (SKIP_INITIAL_SAMPLES + np.arange(len(currents)))
                             / float(meta["steps"]))
                    time_label = "Time in sweep (ms)"
                else:
                    times = np.arange(len(currents))
                    time_label = "Sample index"
            else:
                time_label = "Time in sweep (ms)"
            idx = len(session_sweeps) - 1
            ax_time.plot(
                times, currents, color=CYCLE_COLORS[idx % len(CYCLE_COLORS)],
                linewidth=1.5)
            ax_time.set_title(f"Saved Cycle {sweep['cycle']} (last cycle)")
            ax_time.set_xlabel(time_label)
        else:
            ax_time.set_title("No saved device cycles")
            ax_time.set_xlabel("Time in sweep (ms)")
        ax_time.set_ylabel("Current (µA)")
        ax_time.grid(True, alpha=0.3)

        if session_notes:
            visible_notes = session_notes[-4:]
            note_lines = []
            if len(session_notes) > len(visible_notes):
                note_lines.append(
                    f"{len(session_notes) - len(visible_notes)} earlier note(s) not shown")
            for note in visible_notes:
                wrapped = textwrap.wrap(str(note), width=43) or [""]
                note_lines.append("• " + "\n  ".join(wrapped[:2]))
            ax_time.text(
                0.02, 0.98, "Session notes\n" + "\n".join(note_lines),
                transform=ax_time.transAxes, va="top", ha="left", fontsize=8,
                bbox=dict(boxstyle="round,pad=0.45", facecolor="white",
                          edgecolor="#aaaaaa", alpha=0.88),
            )

        draw_info_panel(
            ax_info, meta, args.baud, session_notes,
            review_name=review_name[0], saved_at=session_saved_at[0])
        technique = "LSV" if meta.get("one_dir") else "CV"
        fig.suptitle(
            f"{technique} session review — {review_name[0]}  |  "
            f"device cycles: {len(session_sweeps)}  |  references: {len(overlays)}"
            f"  |  overlay sessions: "
            f"{session_overlay_group_count(session_overlays)}"
            + (f"  |  saved: {session_saved_at[0][:10]}"
               if session_saved_at[0] else ""))
        new_run_button.label.set_text("Return to live")
        load_session_button.label.set_text("Review another")
        session_overlay_button.label.set_text(
            f"Overlay session ({session_overlay_group_count(session_overlays)})"
            if session_overlays else "Overlay session")
        note_prompt_text.set_text(
            f"Notes: {len(session_notes)} — add another below"
            if session_notes else "Add a session note")
        note_prompt_text.set_visible(True)
        ax_note.set_visible(True)
        fig.canvas.draw_idle()

    # --- "Load overlay" button: pick a reference CSV at runtime and add it to
    # the loop panel live, without restarting or using the CLI flag.
    #
    # The button only *requests* a load (sets a flag). The blocking file dialog
    # is opened later from update(), NOT from this callback: this callback runs
    # inside matplotlib's (Cocoa) event loop, and opening a blocking modal dialog
    # re-entrantly from there is unstable — it crashes/hangs while the panel is
    # still live-redrawing. update() runs in the main loop where blocking is safe.
    def request_overlay(_event):
        pending_overlay[0] = True

    def request_session_overlay(_event):
        pending_session_overlay[0] = True

    def request_save(_event):
        pending_save[0] = True

    def request_session(_event):
        pending_session[0] = True

    def load_overlay_now():
        path = open_file_dialog("Select Metrohm/reference CSV")
        if not path:
            return
        try:
            ov = load_overlay_csv(path)
        except Exception as exc:
            print(f"[overlay] failed to load {path}: {exc}")
            return
        overlays.append(ov)
        print(f"[overlay] loaded {path} ({len(ov[1])} points)")
        if review_mode[0]:
            draw_review_view()
        elif cv_frozen[0]:
            # Frozen panel no longer redraws, so draw the new overlay directly.
            # (While not frozen, the redraw below repaints it from the list.)
            k = len(overlays) - 1
            ov_label, ov_pot, ov_cur = ov
            ax_cv.plot(ov_pot, ov_cur, color=OVERLAY_COLORS[k % len(OVERLAY_COLORS)],
                       linewidth=2.0, alpha=0.9, label=f"{ov_label} (ref)")
            cv_legend()
            fig.canvas.draw_idle()

    def load_session_overlay_now():
        """Add device cycles from a saved session without replacing this run."""
        path = open_file_dialog("Select saved dashboard session to overlay")
        if not path:
            return
        try:
            loaded = load_session_csv(path)
        except Exception as exc:
            print(f"[session overlay] failed to load {path}: {exc}")
            session_overlay_button.label.set_text("Overlay failed")
            fig.canvas.draw_idle()
            return
        if not loaded["device_sweeps"]:
            print(f"[session overlay] {path} contains no device sweeps")
            session_overlay_button.label.set_text("No device data")
            fig.canvas.draw_idle()
            return

        available_cycles = [
            int(sweep["cycle"]) for sweep in loaded["device_sweeps"]]
        default_selection = default_cycle_selection(available_cycles)
        error_prefix = ""
        while True:
            prompt = (
                f"{error_prefix}Session contains {len(available_cycles)} cycles "
                f"({min(available_cycles)}-{max(available_cycles)}).\n"
                "Enter cycles to overlay, for example: 1-4, 2,5,8, or all.")
            selection_text = text_input_dialog(
                "Choose saved-session cycles", prompt, default_selection)
            if selection_text is None:
                return
            try:
                selected_cycles = parse_cycle_selection(
                    selection_text, available_cycles)
            except ValueError as exc:
                error_prefix = f"Invalid selection: {exc}.\n\n"
                default_selection = selection_text
                continue
            if selected_cycles:
                break
            error_prefix = "Select at least one cycle.\n\n"

        selected_set = set(selected_cycles)
        chosen_sweeps = [
            sweep for sweep in loaded["device_sweeps"]
            if int(sweep["cycle"]) in selected_set]
        selection_label = str(selection_text).strip().replace(" ", "")
        base_label = f"{loaded['path'].stem} [cycles {selection_label}]"
        added = []
        for sweep in chosen_sweeps:
            added.append((
                f"{base_label} — Cycle {sweep['cycle']}",
                np.asarray(sweep["potential_V"], dtype=float),
                np.asarray(sweep["current_uA"], dtype=float),
            ))
        session_overlays.extend(added)
        print(
            f"[session overlay] loaded {path} "
            f"(cycles {', '.join(map(str, selected_cycles))})")
        session_overlay_button.label.set_text(
            f"Overlay session ({session_overlay_group_count(session_overlays)})")

        if review_mode[0]:
            draw_review_view()
        elif cv_frozen[0]:
            group_index = session_overlay_group_count(session_overlays) - 1
            for offset, (label, potentials, currents) in enumerate(added):
                color, linestyle = session_overlay_style(
                    group_index, offset, len(added))
                ax_cv.plot(
                    potentials, currents,
                    color=color, linewidth=1.8,
                    linestyle=linestyle, alpha=0.92,
                    label=(f"{session_overlay_group(label)} (session)"
                           if offset == 0 else "_nolegend_"))
            cv_legend()
            fig.canvas.draw_idle()

    def load_session_now():
        path = open_file_dialog("Select saved dashboard session")
        if not path:
            return
        try:
            loaded = load_session_csv(path)
        except Exception as exc:
            print(f"[session] failed to load {path}: {exc}")
            load_session_button.label.set_text("Load failed")
            fig.canvas.draw_idle()
            return

        history.clear()
        current_cycle.clear()
        session_sweeps[:] = loaded["device_sweeps"]
        overlays[:] = loaded["overlays"]
        session_overlays[:] = loaded.get("session_overlays", [])
        session_notes[:] = loaded["notes"]
        meta.update(loaded["meta"])
        session_saved_at[0] = loaded["saved_at"]
        review_name[0] = loaded["path"].name
        review_mode[0] = True
        cv_frozen[0] = True
        cv_live_line[0] = None
        last_time_data[0] = None
        print(f"[session] loaded {loaded['path']} "
              f"({len(session_sweeps)} device cycles, {len(overlays)} references, "
              f"{session_overlay_group_count(session_overlays)} overlay sessions / "
              f"{len(session_overlays)} traces, "
              f"{len(session_notes)} notes)")
        draw_review_view()

    def save_session_now():
        if not session_sweeps and not overlays and not session_overlays:
            print("[save] no completed device sweeps or overlays to save")
            save_button.label.set_text("Nothing to save")
            fig.canvas.draw_idle()
            return

        purpose = text_input_dialog(
            "Session purpose", "Short purpose for the filename:",
            session_purpose[0], accept_label="Continue")
        if purpose is None:
            print("[save] cancelled")
            return
        session_purpose[0] = purpose.strip()
        default_name = session_default_filename(
            meta, session_purpose[0], directory=save_directory[0])
        path = save_file_dialog(default_name, save_directory[0])
        if not path:
            print("[save] cancelled")
            return
        save_directory[0] = Path(path).expanduser().parent
        saved_at = (session_saved_at[0]
                    or datetime.now().astimezone().isoformat(timespec="seconds"))
        try:
            csv_path, png_path = save_session_files(
                path, session_sweeps, overlays, meta, fig,
                notes=session_notes,
                saved_at=saved_at,
                session_overlays=session_overlays)
        except Exception as exc:
            print(f"[save] failed: {exc}")
            save_button.label.set_text("Save failed")
            fig.canvas.draw_idle()
            return
        print(f"[save] data: {csv_path}")
        print(f"[save] plot: {png_path}")
        session_saved_at[0] = saved_at
        save_button.label.set_text("Saved ✓")
        fig.canvas.draw_idle()

    ax_overlay_btn = fig.add_axes([control_x, 0.151, control_w, control_h])
    overlay_button = Button(
        ax_overlay_btn, "Load reference", color="#cfe8ff",
        hovercolor="#9ecbff")
    overlay_button.on_clicked(request_overlay)

    ax_new_run = fig.add_axes([control_x, 0.245, control_w, control_h])
    new_run_button = Button(
        ax_new_run,
        ("Return to live" if review_mode[0]
         else "Connect device" if ser is None else "New run"),
        color="#d9f2d9", hovercolor="#a8dca8")
    new_run_button.on_clicked(request_new_run)

    ax_session_overlay = fig.add_axes([control_x, 0.198, control_w, control_h])
    session_overlay_button = Button(
        ax_session_overlay,
        (f"Overlay session ({session_overlay_group_count(session_overlays)})"
         if session_overlays else "Overlay session"),
        color="#e6dcf7", hovercolor="#c8b2ed")
    session_overlay_button.on_clicked(request_session_overlay)

    ax_session = fig.add_axes([control_x, 0.104, control_w, control_h])
    load_session_button = Button(
        ax_session, "Review another" if review_mode[0] else "Review session",
        color="#ddd5f7", hovercolor="#b9a8ee")
    load_session_button.on_clicked(request_session)

    ax_save = fig.add_axes([control_x, 0.057, control_w, control_h])
    save_button = Button(ax_save, "Save session", color="#fff1b8", hovercolor="#ffd966")
    save_button.on_clicked(request_save)

    note_prompt_text = fig.text(
        control_x, 0.34,
        (f"Notes: {len(session_notes)} — add another below"
         if session_notes else "Add a session note"),
        fontsize=8, color="#444444")
    ax_note = fig.add_axes([control_x, 0.295, control_w, 0.035])
    note_button = Button(
        ax_note, "Add note...", color="#ffffff", hovercolor="#f4f4f4")
    pending_note = [False]
    note_button.on_clicked(lambda _event: pending_note.__setitem__(0, True))

    def add_note(text):
        note = text.strip()
        if not note:
            return
        session_notes.append(note)
        print(f"[notes] added: {note}")
        note_prompt_text.set_text(
            f"Notes: {len(session_notes)} — add another below")
        save_button.label.set_text("Save session")
        if review_mode[0]:
            draw_review_view()
        else:
            draw_info_panel(ax_info, meta, args.baud, session_notes)
            fig.canvas.draw_idle()

    def update(_frame):
        nonlocal ser, reader
        if pending_note[0]:
            pending_note[0] = False
            note = text_input_dialog(
                "Session note", "Enter a note for this session:",
                accept_label="Add note")
            if note is not None:
                add_note(note)
        # Perform reset work in the main loop, not inside matplotlib's callback.
        if pending_new_run[0]:
            pending_new_run[0] = False
            start_new_run()

        # Service a pending Load-overlay request here (main-loop context), so the
        # blocking file dialog never opens from inside the backend event callback.
        if pending_overlay[0]:
            pending_overlay[0] = False
            load_overlay_now()

        if pending_session_overlay[0]:
            pending_session_overlay[0] = False
            load_session_overlay_now()

        if pending_session[0]:
            pending_session[0] = False
            load_session_now()

        if pending_save[0]:
            pending_save[0] = False
            save_session_now()

        if review_mode[0]:
            # Keep a connected live port drained so it cannot build a stale
            # backlog while a historical session is being inspected. These
            # measurements are deliberately not added to the loaded session.
            if reader is not None:
                try:
                    reader.read_lines()
                except (serial.SerialException, OSError) as exc:
                    connection_error[0] = str(exc)
                    print(f"[serial] connection lost during session review: {exc}")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                    reader = None
            return []

        if ser is None or reader is None:
            return []

        try:
            incoming_lines = reader.read_lines()
        except (serial.SerialException, OSError) as exc:
            connection_error[0] = str(exc)
            print(f"[serial] connection lost: {exc}")
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            reader = None
            draw_offline_view()
            return []

        for line in incoming_lines:
            lines_received[0] += 1
            if not line:
                continue
            if line.startswith("# CV_META_"):
                parse_meta_line(line, meta)
            elif line.startswith("# CV_SWEEP_BEGIN,"):
                current_cycle.clear()
                synced[0] = True
                awaiting_fresh_boundary[0] = False
            elif line.startswith("CV,"):
                fields = line.split(",")
                if len(fields) == 6:
                    try:
                        val = float(fields[5])
                        if synced[0] and not awaiting_fresh_boundary[0] and abs(val) <= MAX_CURRENT_UA:
                            current_cycle.append(val)
                    except ValueError:
                        pass
            elif line.startswith("#"):
                continue
            elif line.startswith("Next ") or line == "# END":
                if awaiting_fresh_boundary[0]:
                    current_cycle.clear()
                    synced[0] = True
                    awaiting_fresh_boundary[0] = False
                    print("[dashboard] fresh sweep boundary found; capture restarted")
                elif synced[0] and current_cycle:
                    finalize_cycle()
                else:
                    current_cycle.clear()  # discard partial data before first sync
                    synced[0] = True
            elif VALUE_RE.match(line):
                val = float(line)
                if not awaiting_fresh_boundary[0] and abs(val) <= MAX_CURRENT_UA:
                    current_cycle.append(val)

        live_idx = len(history)
        live_data = current_cycle[SKIP_INITIAL_SAMPLES:]
        technique = "LSV" if meta["one_dir"] else "CV"

        # --- left panel: CV/LSV loop ---
        # While collecting history: clear and redraw everything each tick.
        # Once all MAX_CYCLES are captured: freeze the history (stops clearing so
        # zoom/pan works) but keep a single persistent live-sweep line updated.
        if not cv_frozen[0]:
            ax_cv.clear()
            # Static reference overlays first, so live device traces draw on top.
            # Drawn on the tick the panel freezes too, so they persist afterwards.
            for k, (ov_label, ov_pot, ov_cur) in enumerate(overlays):
                ax_cv.plot(ov_pot, ov_cur,
                           color=OVERLAY_COLORS[k % len(OVERLAY_COLORS)],
                           linewidth=2.0, alpha=0.9, label=f"{ov_label} (ref)")
            draw_session_overlay_curves(ax_cv, session_overlays)
            for idx, cycle in enumerate(history):
                color = CYCLE_COLORS[idx % len(CYCLE_COLORS)]
                x = reconstruct_potential_v(len(cycle), meta, meta["one_dir"], skip=SKIP_INITIAL_SAMPLES)
                ax_cv.plot(x, cycle, color=color, linewidth=1.5, label=f"Cycle {idx + 1}")

            if live_data and live_idx < MAX_CYCLES:
                color = CYCLE_COLORS[live_idx % len(CYCLE_COLORS)]
                x_partial = reconstruct_potential_v(len(live_data), meta, meta["one_dir"], skip=SKIP_INITIAL_SAMPLES)
                ax_cv.plot(
                    x_partial, live_data,
                    color=color, linewidth=1, linestyle="--", alpha=0.6,
                    label=f"Cycle {live_idx + 1} (live)",
                )

            if history or live_data or overlays or session_overlays:
                cv_legend()

            ax_cv.set_xlabel("Potential (V)")
            ax_cv.set_ylabel("Current (µA)")
            ax_cv.grid(True, alpha=0.3)

            if live_idx >= MAX_CYCLES:
                ax_cv.set_title(f"{technique} loop  [{MAX_CYCLES}/{MAX_CYCLES} cycles]  — zoom/pan freely")
                cv_frozen[0] = True
                cv_live_line[0], = ax_cv.plot([], [], color="black", linewidth=1,
                                               linestyle="--", alpha=0.5, label="Live sweep")
                cv_legend()
            else:
                ax_cv.set_title(f"{technique} loop  [{live_idx}/{MAX_CYCLES} cycles]")
        else:
            if cv_live_line[0] is not None:
                if live_data:
                    x_partial = reconstruct_potential_v(len(live_data), meta, meta["one_dir"], skip=SKIP_INITIAL_SAMPLES)
                    cv_live_line[0].set_data(x_partial, live_data)
                else:
                    cv_live_line[0].set_data([], [])

        wait_status = "  |  waiting for boundary" if awaiting_fresh_boundary[0] else ""
        fig.suptitle(
            f"{technique} dashboard — {active_port[0]} @ {args.baud} baud"
            f"  |  lines rx: {lines_received[0]}  |  cycles: {cycle_count}{wait_status}"
        )

        # --- right panel: live time series ---
        # Shows the current in-progress sweep. When no live data is available yet
        # (between sweeps), holds the last completed sweep so it doesn't flash blank.
        if not cv_frozen[0]:
            ax_time.clear()
            time_data = current_cycle[SKIP_INITIAL_SAMPLES:]
            if time_data:
                color = CYCLE_COLORS[live_idx % len(CYCLE_COLORS)]
                n = len(time_data)
                if meta["duration_ms"] and meta["steps"]:
                    t_offset = meta["duration_ms"] * SKIP_INITIAL_SAMPLES / meta["steps"]
                    t = np.linspace(t_offset, meta["duration_ms"] * (SKIP_INITIAL_SAMPLES + n) / meta["steps"], n)
                    ax_time.set_xlabel("Time in sweep (ms)")
                else:
                    t = np.arange(n)
                    ax_time.set_xlabel("Sample index")
                ax_time.plot(t, time_data, color=color, linewidth=1)
                ax_time.set_title(f"Cycle {live_idx + 1} (live)")
            elif last_time_data[0] is not None:
                last_num, last_data = last_time_data[0]
                n = len(last_data)
                color = CYCLE_COLORS[last_num % len(CYCLE_COLORS)]
                if meta["duration_ms"] and meta["steps"] and n:
                    t_offset = meta["duration_ms"] * SKIP_INITIAL_SAMPLES / meta["steps"]
                    t = np.linspace(t_offset, meta["duration_ms"] * (SKIP_INITIAL_SAMPLES + n) / meta["steps"], n)
                    ax_time.set_xlabel("Time in sweep (ms)")
                else:
                    t = np.arange(n)
                    ax_time.set_xlabel("Sample index")
                ax_time.plot(t, last_data, color=color, linewidth=1, alpha=0.5)
                ax_time.set_title(f"Cycle {last_num + 1} (completed)")
            else:
                ax_time.set_title("Current sweep (raw)")
                ax_time.set_xlabel("Time in sweep (ms)")
            ax_time.set_ylabel("Current (µA)")
            ax_time.grid(True, alpha=0.3)

        draw_info_panel(ax_info, meta, args.baud, session_notes)

        return []

    if review_mode[0]:
        cv_frozen[0] = True
        draw_review_view()
    elif ser is None:
        draw_offline_view()

    plt.show(block=False)
    try:
        while plt.fignum_exists(fig.number):
            try:
                update(None)
            except Exception as exc:
                print(f"[dashboard] update error: {exc}")
            # pyplot.pause() calls show(block=False) on every iteration. On the
            # macOS backend that repeatedly reactivates this window and prevents
            # normal work in Metrohm DropView, Finder, or other applications.
            if fig.stale:
                fig.canvas.draw_idle()
            fig.canvas.flush_events()
            time.sleep(0.1)
    finally:
        if ser is not None:
            ser.close()
        if csv_file:
            csv_file.close()


if __name__ == "__main__":
    main()
