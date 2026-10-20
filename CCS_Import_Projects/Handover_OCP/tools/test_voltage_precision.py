"""Preserve the ADC code and transport precision without breaking old sessions."""
import csv
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import ec_dashboard as ec
from test_adc_conversion import function_source, ROOT

class VoltagePrecision(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler unavailable')
    def test_actual_formatter_sign_rounding_and_no_embedded_newline(self):
        source = '''#include <stdint.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
static char buf[100]; static unsigned pos;
void writeChar(char c) { buf[pos++]=c; buf[pos]=0; }
void writeUIntUART(uint32_t n) {
 char b[20]; sprintf(b,"%u",n);
 for(char *p=b; *p; p++) writeChar(*p);
}
'''
        source += function_source((ROOT/'main.c').read_text(), 'static void OCPWriteVoltageUART(')
        source += '''
int main(void) {
 float v[]={0,1.1043f,-.14f,1.9999998f};
 const char *expected[]={"0.000000","1.104300","-0.140000","2.000000"};
 for(int i=0;i<4;i++) {pos=0; OCPWriteVoltageUART(v[i]); assert(strcmp(buf,expected[i])==0);}
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'precision.c';p.write_text(source);exe=p.with_suffix('')
            subprocess.run(['cc','-std=c99',str(p),'-o',str(exe)],check=True,capture_output=True)
            subprocess.run([str(exe)],check=True,capture_output=True)

    def test_adc_code_csv_roundtrip_and_invalid_codes(self):
        session=ec.Session('OCP');session.feed('# OCP_RUN_BEGIN')
        session.feed('OCP,0,100,-0.140012,30268')
        session.feed('OCP,1,200,-0.140013,30269')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'raw.csv';ec.save_session(p,session)
            self.assertEqual(ec.load_session(p,'OCP').records,session.records)
        for bad in ('-1','65536','1.5'):
            session.feed('OCP,2,300,0.1,'+bad)
        self.assertEqual(len(session.records),2)
        self.assertEqual(len(session.warnings),3)

    def test_legacy_uart_and_csv(self):
        session=ec.Session('OCP');session.feed('OCP,0,100,1.1043')
        self.assertEqual(session.records[0]['adc_code'],'')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'old.csv';ec.save_session(p,session)
            lines=p.read_text().splitlines()
            # Legacy sessions predate ADC-code and EIS columns. Keep header and row aligned.
            fields=ec.COLUMNS[:ec.COLUMNS.index('adc_code')]
            output=io.StringIO();output.write(lines[0]+'\n')
            writer=csv.DictWriter(output,fieldnames=fields,extrasaction='ignore')
            writer.writeheader();writer.writerows(session.records)
            p.write_text(output.getvalue())
            self.assertEqual(ec.load_session(p,'OCP').records,session.records)
