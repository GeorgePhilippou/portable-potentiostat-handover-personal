import subprocess,tempfile,unittest,math
from pathlib import Path
from unittest.mock import patch
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import ec_dashboard as ec
from test_chrono_protocol import function
ROOT=Path(__file__).resolve().parent/'Handover_EIS'
class EISContract(unittest.TestCase):
 def test_actual_paired_dft_conversion_frequency_order_and_uart(self):
  app=(ROOT/'Impedance/Impedance.c').read_text();main=(ROOT/'main.c').read_text();lib=(ROOT/'ad5940_lib/ad5940.c').read_text();support=(ROOT/'support/support_functions.c').read_text()
  source='''#include <stdint.h>
#include <stdio.h>
#include <math.h>
#include <float.h>
#include <assert.h>
#include "ad5940_lib/ad5940.h"
#include "Impedance/Impedance.h"
#define EIS_BUILD_ID "HANDOVER_2026_10_13_EIS_R2"
#define EIS_SCHEMA "MSP430_EIS_UART_V2"
static AppIMPCfg_Type AppIMPCfg;
static uint32_t eis_index=0,eis_sweep=0,eis_repeats=1,eis_period_ms=1000,eis_amplitude_mvpp=20;
static uint32_t eis_origin_ms=0;
uint32_t EISMillis(void){return eis_index*1000;}
int32_t AppIMPGetCfg(void *p){*(AppIMPCfg_Type**)p=&AppIMPCfg;return 0;}
void writeChar(char c){putchar(c);}
void writeNewLine(void){putchar('\\n');}
'''
  for sig in ['void AD5940_SweepNext(', 'int32_t AppIMPDataProcess(']:source+=function(lib[lib.index('\nvoid AD5940_SweepNext('):] if sig.startswith('void') else app,sig)+'\n'
  for sig in ['void writeUIntUART(', 'void writeStringUART(', 'void writeFloatPlainUART(', 'void writeFloatUART(']:source+=function(support,sig)+'\n'
  for sig in ['static void EISMetadata(', 'static void EISRecord(']:source+=function(main,sig)+'\n'
  source+='''int main(void){
   AppIMPCfg.RcalVal=200;AppIMPCfg.SweepCfg.SweepEn=bTRUE;
   AppIMPCfg.SweepCfg.SweepStart=1000;AppIMPCfg.SweepCfg.SweepStop=20000;
   AppIMPCfg.SweepCfg.SweepPoints=3;AppIMPCfg.SweepCfg.SweepLog=bTRUE;
   AppIMPCfg.SweepCurrFreq=1000;AD5940_SweepNext(&AppIMPCfg.SweepCfg,&AppIMPCfg.SweepNextFreq);
   EISMetadata();puts("# EIS_RUN_BEGIN");
   for(unsigned j=0;j<3;j++){
    int32_t dft[4]={68000,0,2000,0};uint32_t count=4;
    assert(AppIMPDataProcess(dft,&count)==AD5940ERR_OK && count==1);
    fImpPol_Type *z=(fImpPol_Type*)dft;assert(fabsf(z->Magnitude-6800)<.01 && fabsf(z->Phase)<.001);
    EISRecord(AppIMPCfg.FreqofData,z);eis_index++;
   }
   puts("# EIS_SWEEP_END,0,3");puts("# EIS_RUN_END,COMPLETED");puts("# EIS_STATE,IDLE");
   AppIMPCfg.SweepCfg.SweepEn=bFALSE;
   int32_t cap[4]={68000,0,1960,-288};uint32_t count=4;
   assert(AppIMPDataProcess(cap,&count)==AD5940ERR_OK);
   fImpPol_Type *z=(fImpPol_Type*)cap;assert(z->Phase<0);
   float real=z->Magnitude*cosf(z->Phase),imag=z->Magnitude*sinf(z->Phase);
   assert(fabsf(real-6792.122)<.02);assert(fabsf(imag+998.026)<.02);
   int32_t zero[4]={68000,0,0,0};count=4;assert(AppIMPDataProcess(zero,&count)==AD5940ERR_CALOR);
   return 0;
  }'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.c';p.write_text(source);exe=p.with_suffix('')
   build=subprocess.run(['cc','-std=c99','-I'+str(ROOT),str(p),'-lm','-o',str(exe)],capture_output=True,text=True)
   self.assertEqual(build.returncode,0,build.stderr)
   result=subprocess.run([str(exe)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr)
  s=ec.Session('EIS')
  for line in result.stdout.splitlines():s.feed(line)
  self.assertTrue(s.compatible);self.assertFalse(s.warnings);self.assertEqual(len(s.records),3);self.assertEqual(s.state,'IDLE')
  self.assertEqual([r['zreal_ohm'] for r in s.records],[6800]*3)
  self.assertAlmostEqual(s.records[1]['frequency_hz'],math.sqrt(1000*20000),places=3)
  self.assertEqual(s.records[-1]['frequency_hz'],20000)
 def test_nyquist_bode_plot_and_lossless_roundtrip(self):
  s=ec.Session('EIS');s.feed('# EIS_RUN_BEGIN');s.feed('EIS,0,0,0,1000,6800,-100,6800.7353,-0.8425');s.feed('EIS,0,1,1000,2000,6800,-50,6800.1838,-0.4213')
  self.assertFalse(s.warnings)
  with patch.object(plt,'show'),patch('serial.tools.list_ports.comports',return_value=[]),patch.object(ec,'Session',return_value=s):ec.main('EIS',argv=['--offline'])
  f=plt.gcf();self.assertEqual(len(f.axes[0].lines[0].get_xdata()),2);self.assertEqual(list(f.axes[0].lines[0].get_ydata()),[100,50])
  self.assertEqual(f.axes[1].get_xscale(),'log');self.assertEqual(f.axes[1].get_yscale(),'log');self.assertEqual(list(f.axes[2].lines[0].get_ydata()),[-.8425,-.4213])
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'eis.csv';ec.save_session(p,s);loaded=ec.load_session(p,'EIS');self.assertEqual(s.records,loaded.records)
  plt.close(f)
 def test_command_bounds_and_invalid_records(self):
  v={k:d for k,_,d in ec.FIELDS['EIS']};self.assertEqual(ec.build_start('EIS',v),b'S,1000000,20000000,16,20,1000,1,1\n')
  for key,bad in [('start_hz',.099),('stop_hz',200001),('points',1),('amplitude_mVpp',41),('period_ms',499),('repeats',11),('log_grid',2)]:
   with self.assertRaises(ValueError):ec.build_start('EIS',{**v,key:bad})
  s=ec.Session('EIS');s.feed('EIS,0,0,0,0,6800,0,6800,0');s.feed('EIS,0,0,0,1000,6800,0,20,0');self.assertEqual(len(s.warnings),2);self.assertFalse(s.records)
