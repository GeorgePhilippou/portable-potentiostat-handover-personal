import subprocess,tempfile,unittest
from pathlib import Path
import ec_dashboard as ec
from test_chrono_protocol import function
ROOT=Path(__file__).resolve().parent/'Handover_CyclicVoltammetry'
class CVContract(unittest.TestCase):
 def test_actual_dac_steps_and_uart_potential_stay_aligned_in_both_modes(self):
  ramp=(ROOT/'voltammetry/RampTest.c').read_text();main=(ROOT/'main.c').read_text();support=(ROOT/'support/support_functions.c').read_text()
  source='''#include <stdint.h>
#include <stdio.h>
#include <assert.h>
#include <math.h>
#include "ad5940_lib/ad5940.h"
#include "voltammetry/RampTest.h"
static AppRAMPCfg_Type AppRAMPCfg;
static uint32_t cv_index=0,cv_sweep=0;
void writeChar(char ch){putchar(ch);}
void writeNewLine(void){putchar('\\n');}
'''
  for sig in ['void writeUIntUART(', 'void writeStringUART(', 'void writeFloatPlainUART(', 'void writeFloatUART(']:source+=function(support,sig)+'\n'
  source+=function(ramp,'static AD5940Err RampDacRegUpdate(')+'\n'
  source+=function(main,'static void WriteSweepMetadata(')+'\n'+function(main,'static void CVRecords(')+'\n'
  source+='''int main(int argc,char**argv){
  AppRAMPCfg.RampStartVolt=200;AppRAMPCfg.RampPeakVolt=-200;
  AppRAMPCfg.StepNumber=argc>1?40:80;AppRAMPCfg.bRampOneDir=argc>1?bTRUE:bFALSE;
  AppRAMPCfg.RampDuration=argc>1?4000:8000;AppRAMPCfg.SampleDelay=10;
  AppRAMPCfg.VzeroStart=1300;AppRAMPCfg.VzeroPeak=1300;
  AppRAMPCfg.DACCodePerStep=(400.0f/(AppRAMPCfg.StepNumber/(argc>1?1:2)))/DAC12BITVOLT_1LSB;
  AppRAMPCfg.CurrRampCode=200/DAC12BITVOLT_1LSB;AppRAMPCfg.bDACCodeInc=bFALSE;
  AppRAMPCfg.RampState=RAMP_STATE0;
  WriteSweepMetadata(&AppRAMPCfg);puts("# CV_RUN_BEGIN");puts("# CV_SWEEP_BEGIN,0");
  for(unsigned i=0;i<AppRAMPCfg.StepNumber;i++){
   uint32_t dac;assert(RampDacRegUpdate(&dac)==AD5940ERR_OK);
   float fraction=(float)(i+1)/AppRAMPCfg.StepNumber;
   if(!AppRAMPCfg.bRampOneDir)fraction=fraction<=.5f?fraction*2:2-fraction*2;
   float expected=-200+400*fraction;
   float actual=-((int32_t)(dac&4095)-(int32_t)((dac>>12)*64))*DAC12BITVOLT_1LSB;
   assert(fabsf(actual-expected)<2*DAC12BITVOLT_1LSB);
   float current=expected/6.8f;CVRecords(&current,1,&AppRAMPCfg);
  }
  printf("# CV_SWEEP_END,0,%lu\\n",(unsigned long)AppRAMPCfg.StepNumber);puts("# CV_STATE,IDLE");return 0;
 }'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.c';p.write_text(source);exe=p.with_suffix('')
   build=subprocess.run(['cc','-std=c99','-I'+str(ROOT),str(p),'-o',str(exe)],capture_output=True,text=True)
   self.assertEqual(build.returncode,0,build.stderr)
   for technique,args,count in [('CV',[],80),('LSV',['LSV'],40)]:
    output=subprocess.run([str(exe),*args],capture_output=True,text=True,check=True).stdout
    session=ec.Session(technique)
    for line in output.splitlines():session.feed(line)
    self.assertTrue(session.compatible);self.assertFalse(session.warnings);self.assertEqual(len(session.records),count)
    self.assertEqual(session.records[0]['potential_mV'],-190)
    self.assertEqual(session.records[-1]['potential_mV'],200 if technique=='LSV' else -200)
    self.assertEqual(session.records[-1]['time_ms'],(count-1)*100+10)
