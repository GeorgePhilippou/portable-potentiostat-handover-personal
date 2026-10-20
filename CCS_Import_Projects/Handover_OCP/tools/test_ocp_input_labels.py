import tempfile
import unittest
from pathlib import Path

import ec_dashboard as ec


class OCPInputEvidence(unittest.TestCase):
    def test_internal_reference_session_keeps_its_identity_after_reopening(self):
        session = ec.Session('OCP')
        for line in (
            '# OCP_META_SCHEMA,MSP430_OCP_UART_V1',
            '# OCP_META_INPUT_ROUTING,INTERNAL_1P1_VOLTAGE_TEST',
            '# OCP_META_POTENTIAL_CONVENTION,INTERNAL_REFERENCE_VOLTAGE',
            '# OCP_META_ADC_MUX_POSITIVE,35',
            '# OCP_META_ADC_MUX_NEGATIVE,8',
            '# OCP_RUN_BEGIN',
            'OCP,0,100,1.11',
            '# OCP_RUN_END,COMPLETED,1',
        ):
            session.feed(line, arrival=1.0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'internal_test.csv'
            ec.save_session(path, session)
            reopened = ec.load_session(path, 'OCP')
        self.assertEqual(reopened.records[0]['voltage_V'], 1.11)
        self.assertEqual(reopened.meta['INPUT_ROUTING'], 'INTERNAL_1P1_VOLTAGE_TEST')
        self.assertEqual(ec.ocp_voltage_label(reopened.meta), 'Internal 1.1 V reference (V)')

    def test_electrode_and_raw_differential_labels_remain_distinct(self):
        self.assertEqual(ec.ocp_voltage_label({}), 'WE − RE potential (V)')
        self.assertEqual(
            ec.ocp_voltage_label({'POTENTIAL_CONVENTION': 'ADC_POSITIVE_MINUS_NEGATIVE'}),
            'ADC differential voltage (V)',
        )


if __name__ == '__main__':
    unittest.main()
