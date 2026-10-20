import subprocess,tempfile,unittest
from pathlib import Path
import ec_dashboard as ec
from test_chrono_protocol import function
ROOT=Path(__file__).resolve().parent/'Handover_Amperometric'

class AmpStartup(unittest.TestCase):
 def run_c(self,source):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.c';p.write_text(source);exe=p.with_suffix('')
   result=subprocess.run(['cc','-std=c99','-I'+str(ROOT),str(p),'-o',str(exe)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr)
   return subprocess.run([str(exe)],check=True,capture_output=True,text=True).stdout
 def test_actual_delay_supports_startup_counts_over_32767(self):
  port=(ROOT/'MSP430_FR5969_Port.c').read_text();delay=function(port,'void AD5940_Delay10us(')
  self.assertIn('uint32_t counter',delay)
  source='#include <stdint.h>\n#include <assert.h>\nstatic uint32_t cycles;\n#define __delay_cycles(n) (cycles+=(n))\n'+delay
  source+='\nint main(void){AD5940_Delay10us(50000);assert(cycles==8000000);cycles=0;AD5940_Delay10us(100000);assert(cycles==16000000);return 0;}'
  self.run_c(source)
 def test_actual_metadata_and_samples_match_dashboard(self):
  main=(ROOT/'main.c').read_text();support=(ROOT/'support/support_functions.c').read_text()
  source='''#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include "ad5940_lib/ad5940.h"
#include "Amperometric/Amperometric.h"
#define AMP_UART_SCHEMA "MSP430_AMP_UART_V4"
#define AMP_UART_BAUD 9600
#define AMP_RTIA_NOMINAL_OHM 10000
#define AMP_VZERO_MV 1100
static int32_t amp_potential_mv=100;
static uint32_t amp_sample_period_ms=100,amp_sample_index=0,amp_target_samples=20;
static float LFOSCFreq=32000;
static AppAMPCfg_Type cfg;
AD5940Err AppAMPGetCfg(void*p){*(AppAMPCfg_Type**)p=&cfg;return AD5940ERR_OK;}
void writeChar(char c){putchar(c);}
void writeNewLine(void){putchar('\\n');putchar('\\r');}
'''
  for sig in ['void writeUIntUART(', 'void writeStringUART(', 'void writeFloatPlainUART(', 'void writeFloatUART(']:source+=function(support,sig)+'\n'
  for sig in ['static void AMPShowMetadata(', 'int32_t AMPShowResult(']:
   # Skip forward declaration when extracting metadata.
   text=main[main.index('/* Report the active settings and firmware identity',main.index('static AD5940Err AMPStartMeasurement')):] if sig.startswith('static') else main
   source+=function(text,sig)+'\n'
  source+='int main(void){cfg.SensorBias=-100;cfg.RtiaCalValue.Magnitude=10000;AMPShowMetadata();writeStringUART("# AMP_RUN_BEGIN,1,2000");writeNewLine();float readings[20];for(int i=0;i<20;i++)readings[i]=14.7059f;AMPShowResult(readings,20);writeStringUART("# AMP_RUN_END,COMPLETED,20");writeNewLine();return 0;}'
  result=self.run_c(source);session=ec.Session('AMP')
  for line in result.splitlines():session.feed(line.strip())
  self.assertTrue(session.compatible);self.assertEqual(len(session.records),20);self.assertFalse(session.warnings)
  self.assertEqual(session.meta['UART_BAUD'],'9600');self.assertEqual(session.records[-1]['time_ms'],1900)
  self.assertEqual(ec.build_start('AMP',{'duration_s':5,'rate_hz':10,'potential_mV':100}),b'S,5000,100,100\n')
 def test_internal_rtia_survives_repeated_runs_and_rejects_invalid_values(self):
  app=(ROOT/'Amperometric/Amperometric.c').read_text()
  source='''#include <stdint.h>
#include <assert.h>
#include <float.h>
#include <math.h>
#include "ad5940_lib/ad5940.h"
#include "Amperometric/Amperometric.h"
static AppAMPCfg_Type AppAMPCfg;
static unsigned calibration_calls;
static AD5940Err calibration_status=AD5940ERR_OK;
static AD5940Err AppAMPRtiaCal(void){calibration_calls++;AppAMPCfg.RtiaCalValue.Magnitude=10300;return calibration_status;}
'''+function(app,'static AD5940Err AppAMPPrepareRtia(')
  source+='''
int main(void){
 AppAMPCfg.ExtRtia=bFALSE;AppAMPCfg.ExtRtiaVal=0;
 assert(AppAMPPrepareRtia()==AD5940ERR_OK);assert(calibration_calls==1);
 AppAMPCfg.AMPInited=bTRUE;
 for(int run=0;run<3;run++){
  assert(AppAMPPrepareRtia()==AD5940ERR_OK);
  assert(AppAMPCfg.RtiaCalValue.Magnitude==10300);
 }
 assert(calibration_calls==1);
 AppAMPCfg.ReDoRtiaCal=bTRUE;assert(AppAMPPrepareRtia()==AD5940ERR_OK);assert(calibration_calls==2);
 calibration_status=AD5940ERR_CALOR;AppAMPCfg.ReDoRtiaCal=bTRUE;
 assert(AppAMPPrepareRtia()==AD5940ERR_CALOR);assert(AppAMPCfg.ReDoRtiaCal==bTRUE);
 AppAMPCfg.ReDoRtiaCal=bFALSE;
 AppAMPCfg.RtiaCalValue.Magnitude=0;assert(AppAMPPrepareRtia()==AD5940ERR_CALOR);
 AppAMPCfg.RtiaCalValue.Magnitude=NAN;assert(AppAMPPrepareRtia()==AD5940ERR_CALOR);
 AppAMPCfg.RtiaCalValue.Magnitude=INFINITY;assert(AppAMPPrepareRtia()==AD5940ERR_CALOR);
 AppAMPCfg.ExtRtia=bTRUE;AppAMPCfg.ExtRtiaVal=2000;
 assert(AppAMPPrepareRtia()==AD5940ERR_OK);assert(AppAMPCfg.RtiaCalValue.Magnitude==2000);
 AppAMPCfg.ExtRtiaVal=0;assert(AppAMPPrepareRtia()==AD5940ERR_CALOR);
 return 0;
}
'''
  self.run_c(source)
