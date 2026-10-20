"""Saved sessions retain legacy columns and reject incomplete rows clearly."""
import csv
import io
import tempfile
import unittest
from pathlib import Path
import ec_dashboard as ec

class SessionCSV(unittest.TestCase):
    def session(self):
        session=ec.Session('OCP')
        session.feed('OCP,0,100,1.1043')
        return session

    def test_legacy_session_without_adc_or_eis_columns(self):
        session=self.session()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'legacy.csv'
            ec.save_session(path,session)
            metadata=path.read_text().splitlines()[0]
            fields=ec.COLUMNS[:ec.COLUMNS.index('adc_code')]
            output=io.StringIO();output.write(metadata+'\n')
            writer=csv.DictWriter(output,fieldnames=fields,extrasaction='ignore')
            writer.writeheader();writer.writerows(session.records)
            path.write_text(output.getvalue())
            self.assertEqual(ec.load_session(path,'OCP').records,session.records)

    def test_incomplete_session_row_reports_value_error(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'incomplete.csv'
            ec.save_session(path,self.session())
            lines=path.read_text().splitlines()
            lines[-1]=lines[-1].rsplit(',',1)[0]
            path.write_text('\n'.join(lines)+'\n')
            with self.assertRaisesRegex(ValueError,'does not match its header'):
                ec.load_session(path,'OCP')
