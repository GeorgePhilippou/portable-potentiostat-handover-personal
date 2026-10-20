#!/usr/bin/env python3
"""Regression tests for dashboard session export and macOS focus behaviour."""

import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import matplotlib
import numpy as np

matplotlib.use("Agg", force=True)
from matplotlib.figure import Figure
from matplotlib.backend_bases import KeyEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg

import cv_dashboard
from cv_dashboard import (
    SESSION_FORMAT_VERSION,
    _normalise_textbox_key,
    default_cycle_selection,
    draw_session_overlay_curves,
    load_session_csv,
    open_serial_connection,
    parse_cycle_selection,
    save_session_files,
    serial_port_candidates,
    session_default_filename,
    session_csv_path,
    session_overlay_style,
    store_visible_session_sweep,
    write_session_csv,
)


class DashboardSessionTests(unittest.TestCase):
    def setUp(self):
        self.meta = {
            "start_mV": -200.0,
            "peak_mV": 800.0,
            "steps": 100,
            "duration_ms": 20000.0,
            "one_dir": True,
            "sample_delay_ms": 10.0,
            "vzero_mV": 1300.0,
            "potential_convention": "E_WE_MINUS_E_RE",
            "current_convention": "ANODIC_POSITIVE",
        }
        self.device_sweeps = [{
            "cycle": 1,
            "potential_V": np.array([-0.1, 0.0, 0.1]),
            "current_uA": np.array([-1.0, 0.0, 1.0]),
        }]
        self.overlays = [(
            "Metrohm reference",
            np.array([-0.1, 0.0]),
            np.array([-1.1, 0.1]),
        )]

    def test_session_csv_contains_device_reference_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_session_csv(
                Path(tmpdir) / "comparison", self.device_sweeps,
                self.overlays, self.meta)
            self.assertEqual(path.suffix, ".csv")
            with path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["source"], "device")
        self.assertEqual(rows[0]["label"], "Device Cycle 1")
        self.assertEqual(rows[0]["technique"], "LSV")
        self.assertEqual(rows[0]["potential_convention"], "E_WE_MINUS_E_RE")
        self.assertEqual(rows[0]["current_convention"], "ANODIC_POSITIVE")
        self.assertEqual(rows[-1]["source"], "metrohm")
        self.assertEqual(rows[-1]["label"], "Metrohm reference")
        self.assertEqual(rows[-1]["technique"], "")

    def test_save_session_writes_csv_and_png(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fig = Figure(figsize=(3, 2))
            axis = fig.subplots()
            axis.plot([0, 1], [0, 1])
            csv_path, png_path = save_session_files(
                Path(tmpdir) / "saved.csv", self.device_sweeps,
                self.overlays, self.meta, fig)
            self.assertTrue(csv_path.is_file())
            self.assertTrue(png_path.is_file())
            self.assertGreater(png_path.stat().st_size, 0)

    def test_session_csv_path_preserves_or_adds_csv_suffix(self):
        self.assertEqual(session_csv_path("run").name, "run.csv")
        self.assertEqual(session_csv_path("run.CSV").name, "run.CSV")

    def test_session_round_trip_restores_curves_metadata_notes_and_time(self):
        notes = [
            "Fresh SPE; 10 mM ascorbic acid.",
            "Cycle 1 peak agrees with the Metrohm trace.",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_session_csv(
                Path(tmpdir) / "round_trip.csv", self.device_sweeps,
                self.overlays, self.meta, notes=notes,
                saved_at="2026-08-07T12:34:56+01:00")
            loaded = load_session_csv(path)

        self.assertEqual(loaded["format_version"], SESSION_FORMAT_VERSION)
        self.assertEqual(loaded["saved_at"], "2026-08-07T12:34:56+01:00")
        self.assertEqual(loaded["notes"], notes)
        self.assertEqual(loaded["meta"]["start_mV"], -200.0)
        self.assertTrue(loaded["meta"]["one_dir"])
        np.testing.assert_allclose(
            loaded["device_sweeps"][0]["potential_V"],
            self.device_sweeps[0]["potential_V"])
        np.testing.assert_allclose(
            loaded["device_sweeps"][0]["current_uA"],
            self.device_sweeps[0]["current_uA"])
        np.testing.assert_allclose(
            loaded["device_sweeps"][0]["time_ms"], [2000, 2200, 2400])
        self.assertEqual(loaded["overlays"][0][0], "Metrohm reference")
        np.testing.assert_allclose(
            loaded["overlays"][0][2], self.overlays[0][2])

    def test_capital_letters_are_normalised_for_notes_box(self):
        self.assertEqual(_normalise_textbox_key("shift+a"), "A")
        self.assertEqual(_normalise_textbox_key("a", shift_down=True), "A")
        self.assertEqual(_normalise_textbox_key("A"), "A")
        self.assertEqual(_normalise_textbox_key("backspace"), "backspace")

    def test_notes_box_inserts_backend_shift_letter_as_capital(self):
        fig = Figure(figsize=(2, 1))
        FigureCanvasAgg(fig)
        box = cv_dashboard.NotesTextBox(fig.add_axes([0.1, 0.2, 0.8, 0.5]), "")
        box.capturekeystrokes = True
        box._keypress(KeyEvent("key_press_event", fig.canvas, key="shift+a"))
        self.assertEqual(box.text, "A")

    def test_default_session_name_uses_purpose_and_next_available_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = session_default_filename(
                self.meta, "AA precheck", datetime(2026, 8, 28, 14, 5, 6),
                directory=tmpdir)
            self.assertEqual(first, "2026-08-28_LSV_AA_precheck_Run01.csv")
            (Path(tmpdir) / first).touch()
            second = session_default_filename(
                self.meta, "AA precheck", datetime(2026, 8, 28, 16, 8, 9),
                directory=tmpdir)
            self.assertEqual(second, "2026-08-28_LSV_AA_precheck_Run02.csv")

    def test_saved_session_overlay_round_trip_remains_distinct(self):
        session_overlays = [(
            "Participant 1 AA reference — Cycle 1",
            np.array([-0.2, 0.4, 0.8]),
            np.array([0.1, 13.2, 7.0]),
        )]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_session_csv(
                Path(tmpdir) / "overlay_round_trip.csv",
                self.device_sweeps, self.overlays, self.meta,
                session_overlays=session_overlays)
            loaded = load_session_csv(path)

        self.assertEqual(len(loaded["session_overlays"]), 1)
        self.assertEqual(loaded["session_overlays"][0][0], session_overlays[0][0])
        np.testing.assert_allclose(
            loaded["session_overlays"][0][1], session_overlays[0][1])
        np.testing.assert_allclose(
            loaded["session_overlays"][0][2], session_overlays[0][2])

    def test_cycle_selection_supports_ranges_lists_and_all(self):
        available = list(range(1, 27))
        self.assertEqual(parse_cycle_selection("1-4", available), [1, 2, 3, 4])
        self.assertEqual(parse_cycle_selection("2, 5, 8", available), [2, 5, 8])
        self.assertEqual(parse_cycle_selection("all", available), available)
        self.assertEqual(default_cycle_selection(available), "1-4")
        with self.assertRaisesRegex(ValueError, "not present"):
            parse_cycle_selection("27", available)

    def test_session_storage_stops_at_visible_four_sweeps(self):
        stored = []
        for cycle in range(1, 27):
            store_visible_session_sweep(stored, {"cycle": cycle})
        self.assertEqual([sweep["cycle"] for sweep in stored], [1, 2, 3, 4])

    def test_imported_session_cycles_share_one_compact_legend_entry(self):
        fig = Figure(figsize=(3, 2))
        axis = fig.subplots()
        overlays = [
            (f"AA precheck [cycles 1-4] — Cycle {cycle}",
             np.array([-0.2, 0.8]), np.array([cycle, cycle + 1]))
            for cycle in range(1, 5)
        ]
        draw_session_overlay_curves(axis, overlays)
        _handles, labels = axis.get_legend_handles_labels()
        self.assertEqual(labels, ["AA precheck [cycles 1-4] (session)"])

    def test_adjacent_saved_sessions_use_distinct_colours_and_solid_lines(self):
        first_color, first_style = session_overlay_style(0)
        second_color, second_style = session_overlay_style(1)
        self.assertNotEqual(first_color, second_color)
        self.assertEqual(first_style, "-")
        self.assertEqual(second_style, "-")

    def test_sweeps_within_session_progress_from_dark_to_light(self):
        colours = [session_overlay_style(0, index, 4)[0] for index in range(4)]
        brightness = [sum(colour) for colour in colours]
        self.assertEqual(brightness, sorted(brightness))
        self.assertGreater(brightness[-1], brightness[0])

    def test_legacy_session_without_notes_or_time_remains_loadable(self):
        legacy_fields = [
            "source", "label", "cycle", "potential_V", "current_uA",
            "technique", "start_mV", "peak_mV", "steps", "duration_ms",
            "sample_delay_ms", "vzero_mV", "potential_convention",
            "current_convention",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "legacy.csv"
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=legacy_fields)
                writer.writeheader()
                writer.writerow({
                    "source": "device", "label": "Device Cycle 1", "cycle": 1,
                    "potential_V": -0.2, "current_uA": 1.2, "technique": "LSV",
                    "start_mV": -200, "peak_mV": 800, "steps": 100,
                    "duration_ms": 20000, "sample_delay_ms": 190,
                    "vzero_mV": 1300, "potential_convention": "E_WE_MINUS_E_RE",
                    "current_convention": "ANODIC_POSITIVE",
                })
            loaded = load_session_csv(path)

        self.assertEqual(loaded["format_version"], 1)
        self.assertEqual(loaded["notes"], [])
        self.assertNotIn("time_ms", loaded["device_sweeps"][0])
        self.assertEqual(loaded["device_sweeps"][0]["cycle"], 1)

    def test_main_loop_does_not_repeatedly_call_pyplot_pause(self):
        source = Path(__file__).with_name("cv_dashboard.py").read_text()
        executable_source = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("plt.pause(", executable_source)
        self.assertIn("fig.canvas.flush_events()", executable_source)

    def test_single_usbmodem_is_safe_fallback_for_changed_port_name(self):
        ports = [
            SimpleNamespace(device="/dev/cu.Bluetooth-Incoming-Port"),
            SimpleNamespace(device="/dev/cu.usbmodem11203"),
        ]
        with mock.patch.object(
                cv_dashboard.serial.tools.list_ports, "comports",
                return_value=ports):
            candidates = serial_port_candidates("/dev/cu.usbmodem1303")

        self.assertEqual(
            candidates, ["/dev/cu.usbmodem1303", "/dev/cu.usbmodem11203"])

    def test_connection_uses_discovered_usbmodem_after_preferred_fails(self):
        fake_serial = object()
        ports = [SimpleNamespace(device="/dev/cu.usbmodem11203")]

        def open_port(device, _baud, timeout):
            self.assertEqual(timeout, 0)
            if device == "/dev/cu.usbmodem1303":
                raise cv_dashboard.serial.SerialException("not present")
            return fake_serial

        with mock.patch.object(
                cv_dashboard.serial.tools.list_ports, "comports",
                return_value=ports), \
             mock.patch.object(
                 cv_dashboard.serial, "Serial", side_effect=open_port):
            opened, device = open_serial_connection(
                "/dev/cu.usbmodem1303", 9600)

        self.assertIs(opened, fake_serial)
        self.assertEqual(device, "/dev/cu.usbmodem11203")

    def test_dashboard_starts_updates_and_closes_with_nonblocking_serial(self):
        class FakeSerial:
            in_waiting = 0

            def __init__(self):
                self.closed = False

            def read(self, _count):
                return b""

            def close(self):
                self.closed = True

        fake_serial = FakeSerial()
        with mock.patch.object(cv_dashboard.serial, "Serial", return_value=fake_serial), \
             mock.patch.object(cv_dashboard.sys, "argv", ["cv_dashboard.py"]), \
             mock.patch.object(cv_dashboard.plt, "show"), \
             mock.patch.object(cv_dashboard.plt, "fignum_exists", side_effect=[True, False]), \
             mock.patch.object(cv_dashboard.time, "sleep"):
            cv_dashboard.main()

        self.assertTrue(fake_serial.closed)
        cv_dashboard.plt.close("all")

    def test_review_mode_opens_saved_session_without_serial_device(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_session_csv(
                Path(tmpdir) / "review.csv", self.device_sweeps,
                self.overlays, self.meta, notes=["Review-mode note"])
            with mock.patch.object(cv_dashboard.serial, "Serial") as serial_open, \
                 mock.patch.object(
                     cv_dashboard.sys, "argv",
                     ["cv_dashboard.py", "--session", str(path)]), \
                 mock.patch.object(cv_dashboard.plt, "show"), \
                 mock.patch.object(
                     cv_dashboard.plt, "fignum_exists", side_effect=[True, False]), \
                 mock.patch.object(cv_dashboard.time, "sleep"):
                cv_dashboard.main()

        serial_open.assert_not_called()
        cv_dashboard.plt.close("all")

    def test_explicit_offline_mode_opens_dashboard_without_serial(self):
        with mock.patch.object(cv_dashboard.serial, "Serial") as serial_open, \
             mock.patch.object(
                 cv_dashboard.sys, "argv", ["cv_dashboard.py", "--offline"]), \
             mock.patch.object(cv_dashboard.plt, "show"), \
             mock.patch.object(
                 cv_dashboard.plt, "fignum_exists", side_effect=[True, False]), \
             mock.patch.object(cv_dashboard.time, "sleep"):
            cv_dashboard.main()

        serial_open.assert_not_called()
        cv_dashboard.plt.close("all")

    def test_missing_port_automatically_falls_back_to_offline_dashboard(self):
        with mock.patch.object(
                 cv_dashboard.serial.tools.list_ports, "comports",
                 return_value=[]), \
             mock.patch.object(
                 cv_dashboard.serial, "Serial",
                 side_effect=cv_dashboard.serial.SerialException("missing")), \
             mock.patch.object(
                 cv_dashboard.sys, "argv", ["cv_dashboard.py"]), \
             mock.patch.object(cv_dashboard.plt, "show"), \
             mock.patch.object(
                 cv_dashboard.plt, "fignum_exists", side_effect=[True, False]), \
             mock.patch.object(cv_dashboard.time, "sleep"):
            cv_dashboard.main()

        cv_dashboard.plt.close("all")

    def test_connect_device_button_opens_serial_from_offline_dashboard(self):
        class FakeSerial:
            in_waiting = 0

            def __init__(self):
                self.closed = False

            def read(self, _count):
                return b""

            def close(self):
                self.closed = True

        fake_serial = FakeSerial()
        callbacks = {}
        original_on_clicked = cv_dashboard.Button.on_clicked

        def capture_callback(button, callback):
            callbacks[button.label.get_text()] = callback
            return original_on_clicked(button, callback)

        window_checks = [0]

        def window_exists(_figure_number):
            if window_checks[0] == 0:
                callbacks["Connect device"](None)
                window_checks[0] += 1
                return True
            return False

        with mock.patch.object(
                 cv_dashboard.serial.tools.list_ports, "comports",
                 return_value=[]), \
             mock.patch.object(
                 cv_dashboard.serial, "Serial", return_value=fake_serial
             ) as serial_open, \
             mock.patch.object(
                 cv_dashboard.sys, "argv", ["cv_dashboard.py", "--offline"]), \
             mock.patch.object(
                 cv_dashboard.Button, "on_clicked", new=capture_callback), \
             mock.patch.object(cv_dashboard.plt, "show"), \
             mock.patch.object(
                 cv_dashboard.plt, "fignum_exists", side_effect=window_exists), \
             mock.patch.object(cv_dashboard.time, "sleep"):
            cv_dashboard.main()

        serial_open.assert_called_once_with(
            cv_dashboard.PORT, 9600, timeout=0)
        self.assertTrue(fake_serial.closed)
        cv_dashboard.plt.close("all")


if __name__ == "__main__":
    unittest.main()
