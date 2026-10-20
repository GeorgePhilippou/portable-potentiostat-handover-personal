import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FirmwareContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = (ROOT / "main.c").read_text(errors="ignore")
        cls.config = cls.main
        cls.app = (ROOT / "Amperometric" / "Amperometric.c").read_text(
            errors="ignore")
        cls.port = (ROOT / "MSP430_FR5969_Port.c").read_text(errors="ignore")

    def test_continuous_timestamped_uart_contract(self):
        self.assertIn("pAMPCfg->NumOfData = -1", self.main)
        self.assertIn('writeStringUART("AMP,")', self.main)
        self.assertIn("amp_sample_index * amp_sample_period_ms", self.main)
        self.assertIn('"MSP430_AMP_UART_V4"', self.config)
        port = (ROOT / "MSP430_FR5969_Port.c").read_text(errors="ignore")
        self.assertIn("ec_receive_byte(UCA0RXBUF)", port)
        self.assertIn("AMPShowMetadata();", self.main)

    def test_electrochemical_conventions_are_explicit(self):
        self.assertIn(
            "# AMP_META_POTENTIAL_CONVENTION,E_WE_MINUS_E_RE", self.main)
        self.assertIn(
            "# AMP_META_CURRENT_CONVENTION,ANODIC_POSITIVE", self.main)
        self.assertRegex(self.config, r"#define\s+AMP_SENSOR_BIAS_MV\s+\(-AMP_DETECTION_POTENTIAL_MV\)")
        self.assertIn("return fCurrent * 1000000", self.app)
        self.assertNotIn("return -fCurrent * 1000000", self.app)

    def test_resistor_validation_parameters_match_metadata(self):
        self.assertRegex(self.config, r"#define\s+AMP_DETECTION_POTENTIAL_MV\s+400\b")
        self.assertRegex(self.config, r"#define\s+AMP_SAMPLE_PERIOD_MS\s+100u\b")
        self.assertRegex(self.config, r"#define\s+AMP_RTIA_NOMINAL_OHM\s+10000u\b")
        self.assertIn("pAMPCfg->LptiaRtiaSel = LPTIARTIA_10K", self.main)

    def test_live_dashboard_uart_uses_proven_9600_baud_configuration(self):
        self.assertRegex(self.config, r"#define\s+AMP_UART_BAUD\s+9600u\b")
        self.assertIn("UCA0BRW = 104", self.main)
        self.assertIn("0xD600 | (0x02 << 4)", self.main)

    def test_dashboard_controls_bias_and_timed_acquisition(self):
        self.assertIn("AMPStartMeasurement(uint32_t duration_ms,", self.main)
        self.assertIn("AppAMPInit(AppBuff, APPBUFF_SIZE)", self.main)
        self.assertIn("AppAMPCtrl(AMPCTRL_SHUTDOWN, 0)", self.main)
        self.assertIn("# AMP_STATE,", self.main)
        self.assertIn("# AMP_RUN_END,", self.main)
        self.assertIn(
            "# AMP_META_CONTROL_MODE,DASHBOARD_TIMED_RATE_RUN", self.main)
        self.assertIn("amp_target_samples", self.main)
        self.assertIn("amp_start_requested = 1", self.main)
        self.assertIn("amp_stop_requested = 1", self.main)

    def test_dashboard_can_configure_a_bounded_sample_rate(self):
        self.assertIn("AMP_MIN_SAMPLE_PERIOD_MS", self.config)
        self.assertIn("AMP_MAX_SAMPLE_PERIOD_MS", self.config)
        self.assertIn("amp_requested_sample_period_ms", self.main)
        self.assertIn("pAMPCfg->AmpODR =", self.main)
        self.assertIn("writeUIntUART(amp_sample_period_ms)", self.main)


if __name__ == "__main__":
    unittest.main()
