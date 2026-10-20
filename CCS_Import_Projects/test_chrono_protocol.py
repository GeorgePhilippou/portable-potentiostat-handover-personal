"""Cross-check actual compiled chrono metadata/sample output against Python."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
import ec_dashboard as ec
import potentiostat_dashboard as launcher

BASE=Path(__file__).resolve().parent
ROOT=BASE/'Handover_Chronoamperometric'
def function(source,signature):
 start=source.index(signature);opening=source.index('{',start);end=opening+1;depth=1
 while depth:depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[start:end]

class ActualChronoProtocol(unittest.TestCase):
 def test_compiled_metadata_and_buffered_samples_are_consumed_without_loss(self):
  firmware=(ROOT/'main.c').read_text();support=(ROOT/'support/support_functions.c').read_text()
  source='''#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <assert.h>
#include "ad5940_lib/ad5940.h"
#define CA_SAMPLE_HZ (800000.0f/4.0f/1333.0f)
#define CHRONOAMPCTRL_SHUTDOWN 6
#define BIT3 8
static uint8_t ca_running=1,ca_platform_ready=1,ec_stop=0;
static uint32_t ca_index=0,ca_count=200,ca_duration_ms=1000;
static int32_t ca_base_mv=0,ca_step_mv=100;
static float ca_samples[10000];static unsigned P2OUT;
uint32_t AD5940_ClrMCUIntFlag(void){return 1;}
void AD5940_SEQCtrlS(BoolFlag flag){}
int AppCHRONOAMPCtrl(int c,void*p){return 0;}
void writeChar(char c){putchar(c);}
void writeNewLine(void){writeChar('\\n');writeChar('\\r');}
'''
  for signature in ['void writeUIntUART(', 'void writeIntUART(', 'void writeStringUART(', 'void writeFloatPlainUART(', 'void writeFloatUART(']:
   source+=function(support,signature)+'\n'
  for signature in ['static void CAState(', 'static void CAMetadata(', 'static void CAStop(', 'static void CATransmit(']:
   source+=function(firmware,signature)+'\n'
  source+='int main(void){for(unsigned i=0;i<ca_count;i++)ca_samples[i]=14.7f/(i+1);CAMetadata();CAState();writeStringUART("# CA_RUN_BEGIN");writeNewLine();CATransmit("COMPLETED");assert(ca_index==200);assert(ca_running==0);return 0;}'
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'actual.c';p.write_text(source);exe=p.with_suffix('')
   subprocess.run(['cc','-std=c99','-I'+str(ROOT),str(p),'-o',str(exe)],check=True,capture_output=True)
   result=subprocess.run([str(exe)],check=True,capture_output=True,text=True).stdout
  session=ec.Session('CA')
  for line in result.splitlines():session.feed(line.strip())
  self.assertTrue(session.compatible);self.assertEqual(session.meta['UART_BAUD'],'9600')
  self.assertEqual(session.meta['TRANSFER_MODE'],'BUFFERED_AFTER_ACQUISITION')
  self.assertEqual(len(session.records),200);self.assertFalse(session.warnings);self.assertEqual(session.state,'IDLE')
  self.assertAlmostEqual(session.records[1]['time_ms'],1000/(800000/4/1333),places=3)
  self.assertEqual(launcher.SCHEMA_TECHNIQUES[session.meta['SCHEMA']],'CA')
  self.assertEqual(ec.build_start('CA',{'base_mV':0,'step_mV':100,'duration_s':1,'hold_ms':500}),b'S,0,100,1000,500\n')
 def test_firmware_delay_and_startup_are_bounded(self):
  port=(ROOT/'MSP430_FR5969_Port.c').read_text();delay=function(port,'void AD5940_Delay10us(')
  self.assertIn('uint32_t counter',delay);self.assertIn('__delay_cycles(160)',delay)
  main=(ROOT/'main.c').read_text();start=main.index('int main(void)')
  self.assertLess(main.index('CAMetadata();',start),main.index('AD5940PlatformCfg()',start))
  library=(ROOT/'ad5940_lib/ad5940.c').read_text();lfosc=function(library,'AD5940Err AD5940_LFOSCMeasure(')
  self.assertEqual(lfosc.count('if (++attempts > wait_limit)'),2);self.assertIn('return AD5940ERR_TIMEOUT',lfosc)
