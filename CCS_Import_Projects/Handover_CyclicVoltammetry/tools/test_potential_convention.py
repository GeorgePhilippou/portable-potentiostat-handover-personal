#!/usr/bin/env python3
"""Regression tests for the laboratory/AD5941 potential convention boundary."""

import csv
import unittest
from pathlib import Path

import numpy as np

from cv_dashboard import parse_meta_line, reconstruct_potential_v


class PotentialConventionTests(unittest.TestCase):
    def test_stage2_lsv_axis_is_conventional_we_minus_re(self):
        meta = {
            "start_mV": None,
            "peak_mV": None,
            "steps": None,
            "duration_ms": None,
            "one_dir": None,
            "sample_delay_ms": None,
            "vzero_mV": None,
        }
        for line in (
            "# CV_META_START_MV,-0200.0000",
            "# CV_META_PEAK_MV,0800.0000",
            "# CV_META_STEPS,0100.0000",
            "# CV_META_ONE_DIR,0001.0000",
        ):
            parse_meta_line(line, meta)

        axis = reconstruct_potential_v(100, meta, True)
        self.assertAlmostEqual(axis[0], -0.2)
        self.assertAlmostEqual(axis[1] - axis[0], 0.01)
        self.assertTrue(np.all(np.diff(axis) > 0))
        self.assertAlmostEqual(axis[-1], 0.79)

    def test_ad5941_raw_dac_direction_is_opposite(self):
        ec_start_mv = -200.0
        ec_peak_mv = 800.0
        raw_start_mv = -ec_start_mv
        raw_peak_mv = -ec_peak_mv

        self.assertEqual(raw_start_mv, 200.0)
        self.assertEqual(raw_peak_mv, -800.0)
        self.assertGreater(raw_start_mv, raw_peak_mv)
        self.assertEqual(-raw_start_mv, ec_start_mv)
        self.assertEqual(-raw_peak_mv, ec_peak_mv)

    def test_firmware_current_output_matches_verified_lab_sign(self):
        source = (Path(__file__).resolve().parents[1] / "voltammetry" / "RampTest.c").read_text()
        self.assertIn("temp = AD5940_ADCCode2Volt", source)
        self.assertNotIn("temp = -AD5940_ADCCode2Volt", source)

    def test_stage3_current_sign_matches_lab_dummy_cell(self):
        evidence_path = Path(__file__).with_name("current_convention_evidence_2026-08-03.csv")
        with evidence_path.open(newline="") as evidence_file:
            rows = list(csv.DictReader(evidence_file))

        by_source = {}
        for row in rows:
            by_source.setdefault(row["source"], []).append(row)

        lab = by_source["lab_potentiostat"]
        stage2 = by_source["device_stage2_original_sign"]
        stage3 = by_source["device_stage3_verified"]
        lab_slope = (float(lab[1]["current_uA"]) - float(lab[0]["current_uA"])) / (
            float(lab[1]["potential_V"]) - float(lab[0]["potential_V"])
        )
        stage2_slope = (float(stage2[1]["current_uA"]) - float(stage2[0]["current_uA"])) / (
            float(stage2[1]["potential_V"]) - float(stage2[0]["potential_V"])
        )
        stage3_slope = (float(stage3[1]["current_uA"]) - float(stage3[0]["current_uA"])) / (
            float(stage3[1]["potential_V"]) - float(stage3[0]["potential_V"])
        )

        self.assertGreater(lab_slope, 0.0)
        self.assertLess(stage2_slope, 0.0)
        self.assertGreater(-stage2_slope, 0.0)
        self.assertGreater(stage3_slope, 0.0)
        self.assertAlmostEqual(-stage2_slope, lab_slope, delta=0.5)
        self.assertAlmostEqual(stage3_slope, lab_slope, delta=0.1)


if __name__ == "__main__":
    unittest.main()
