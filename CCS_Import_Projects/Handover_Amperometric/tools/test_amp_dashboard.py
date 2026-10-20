import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import matplotlib
matplotlib.use("Agg")

import numpy as np
import matplotlib.pyplot as plt

from amp_dashboard import (
    empty_device_data,
    empty_meta,
    duration_seconds_to_ms,
    firmware_supports_control,
    firmware_supports_rate_control,
    interpolate_bounds,
    load_overlay_csv,
    load_session_csv,
    main,
    parse_amp_record,
    parse_meta_line,
    record_matches_schedule,
    sample_diagnostics,
    sample_rate_to_period_ms,
    start_measurement,
    stop_measurement,
    update_axis_limits,
    format_rtia,
    write_session_csv,
)


class ParserTests(unittest.TestCase):
    def test_metadata_and_record_contract(self):
        meta = empty_meta()
        self.assertTrue(parse_meta_line("# AMP_META_POTENTIAL_MV,400.0000", meta))
        self.assertTrue(parse_meta_line(
            "# AMP_META_POTENTIAL_CONVENTION,E_WE_MINUS_E_RE", meta))
        self.assertEqual(meta["potential_mV"], 400.0)
        self.assertEqual(meta["potential_convention"], "E_WE_MINUS_E_RE")
        self.assertEqual(parse_amp_record("AMP,42,4200,20.0123"),
                         (42, 4200.0, 20.0123))

    def test_malformed_and_unbounded_records_are_rejected(self):
        self.assertIsNone(parse_amp_record("20.0"))
        self.assertIsNone(parse_amp_record("AMP,1,nope,20.0"))
        self.assertIsNone(parse_amp_record("AMP,1,100,999999.0"))

    def test_missing_sample_diagnostics(self):
        data = empty_device_data()
        data["sample_index"][:] = [0, 1, 4, 5]
        data["device_time_ms"][:] = [0, 100, 400, 500]
        result = sample_diagnostics(data)
        self.assertEqual(result["missing_samples"], 2)
        self.assertEqual(result["largest_index_jump"], 3)

    def test_redundant_time_field_rejects_parseable_uart_corruption(self):
        self.assertTrue(record_matches_schedule((42, 4200.0, 20.0), 100.0))
        self.assertFalse(record_matches_schedule((6375, 1500.0, 20.0), 100.0))

    def test_implausible_calibrated_rtia_is_flagged(self):
        meta = empty_meta()
        meta["rtia_nominal_ohm"] = 10000.0
        meta["rtia_calibrated_ohm"] = 10886.2705
        self.assertIn("calibrated", format_rtia(meta))
        meta["rtia_calibrated_ohm"] = 108862705.0
        self.assertIn("INVALID", format_rtia(meta))

    def test_duration_and_control_commands(self):
        self.assertEqual(duration_seconds_to_ms("600"), 600000)
        self.assertEqual(duration_seconds_to_ms("0"), 0)
        self.assertEqual(sample_rate_to_period_ms("10"), 100)
        self.assertEqual(sample_rate_to_period_ms("5"), 200)
        with self.assertRaises(ValueError):
            duration_seconds_to_ms("-1")
        with self.assertRaises(ValueError):
            sample_rate_to_period_ms("25")
        serial_port = Mock()
        start_measurement(serial_port, 600000)
        start_measurement(serial_port, 600000, 100)
        stop_measurement(serial_port)
        self.assertEqual(
            serial_port.write.call_args_list[0].args[0], b"S,600000\n")
        self.assertEqual(
            serial_port.write.call_args_list[1].args[0], b"S,600000,100\n")
        self.assertEqual(serial_port.write.call_args_list[2].args[0], b"X\n")

    def test_control_schema_versions(self):
        meta = empty_meta()
        meta["schema"] = "MSP430_AMP_UART_V1"
        self.assertFalse(firmware_supports_control(meta))
        meta["schema"] = "MSP430_AMP_UART_V2"
        self.assertTrue(firmware_supports_control(meta))
        self.assertFalse(firmware_supports_rate_control(meta))
        meta["schema"] = "MSP430_AMP_UART_V3"
        self.assertTrue(firmware_supports_control(meta))
        self.assertTrue(firmware_supports_rate_control(meta))

    def test_data_autoscale_recovers_from_empty_state_limits(self):
        figure, axis = plt.subplots()
        line, = axis.plot([], [])
        update_axis_limits(axis, False, 60.0)
        self.assertEqual(tuple(axis.get_ylim()), (-1.0, 1.0))
        line.set_data([0.0, 1.0], [19.9, 20.1])
        update_axis_limits(axis, True, 60.0)
        lower, upper = axis.get_ylim()
        self.assertLess(lower, 19.9)
        self.assertGreater(upper, 20.1)
        plt.close(figure)

    def test_layout_bounds_interpolation_is_smooth_and_clamped(self):
        start = (0.0, 0.1, 0.7, 0.8)
        end = (0.0, 0.1, 0.3, 0.8)
        self.assertEqual(interpolate_bounds(start, end, -1.0), start)
        self.assertEqual(interpolate_bounds(start, end, 2.0), end)
        self.assertAlmostEqual(interpolate_bounds(start, end, 0.5)[2], 0.5)


class FileTests(unittest.TestCase):
    def test_overlay_units_are_normalized(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reference.csv"
            path.write_text('"Time (ms)";"Current (nA)"\n0;1000\n500;2000\n')
            label, seconds, current_uA = load_overlay_csv(path)
            self.assertEqual(label, "reference")
            np.testing.assert_allclose(seconds, [0.0, 0.5])
            np.testing.assert_allclose(current_uA, [1.0, 2.0])

    def test_session_round_trip(self):
        data = empty_device_data()
        data["sample_index"][:] = [0, 1]
        data["device_time_ms"][:] = [0.0, 100.0]
        data["host_time_ms"][:] = [1.0, 101.5]
        data["current_uA"][:] = [20.0, 20.1]
        meta = empty_meta()
        meta.update({
            "schema": "MSP430_AMP_UART_V1",
            "potential_mV": 400.0,
            "potential_convention": "E_WE_MINUS_E_RE",
            "current_convention": "ANODIC_POSITIVE",
        })
        overlay = ("lab", np.asarray([0.0, 0.1]), np.asarray([19.9, 20.0]))
        with tempfile.TemporaryDirectory() as folder:
            path = write_session_csv(
                Path(folder) / "session.csv", data, [overlay], meta,
                notes=["20 kOhm resistor"])
            loaded = load_session_csv(path)
        self.assertEqual(loaded["device_data"]["sample_index"], [0, 1])
        self.assertEqual(loaded["notes"], ["20 kOhm resistor"])
        self.assertEqual(loaded["meta"]["potential_mV"], 400.0)
        self.assertEqual(loaded["overlays"][0][0], "lab")


class InterfaceSmokeTests(unittest.TestCase):
    def test_offline_dashboard_constructs_without_a_device(self):
        with patch("amp_dashboard.plt.show"), patch("amp_dashboard.FuncAnimation"):
            self.assertEqual(main(["--offline"]), 0)
        plt.close("all")


if __name__ == "__main__":
    unittest.main()
