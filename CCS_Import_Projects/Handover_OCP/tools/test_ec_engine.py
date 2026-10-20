import importlib.util
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('ec_dashboard',ROOT/'ec_dashboard.py')
ec=importlib.util.module_from_spec(spec);spec.loader.exec_module(ec)

class ExperimentCommands(unittest.TestCase):
 def defaults(self,t):return {key:value for key,_,value in ec.FIELDS[t]}
 def test_all_defaults_and_exact_wire_contracts(self):
  expected={'CV':b'S,-200,800,100,50,10,1,0,500\n','LSV':b'S,-200,800,100,50,10,1,1,500\n',
            'AMP':b'S,60000,100,400\n','OCP':b'S,60000,100\n','CA':b'S,0,400,1000,500\n'}
  for t,w in expected.items():self.assertEqual(ec.build_start(t,self.defaults(t)),w)
 def test_invalid_parameters_cannot_be_transmitted(self):
  for t,key,value in [('CV','steps',99),('CV','scan_rate',1000),('CV','delay_ms',500),
                      ('AMP','potential_mV',801),('AMP','rate_hz',21),('AMP','duration_s',-1),
                      ('CA','duration_s',61),('OCP','rate_hz',float('nan')),('CA','step_mV',0)]:
   p=self.defaults(t);p[key]=value
   with self.subTest(t=t,key=key),self.assertRaises(ValueError):ec.build_start(t,p)
 def test_scan_direction_and_duration_boundaries(self):
  p=self.defaults('LSV');p.update(start_mV=800,peak_mV=-200)
  self.assertTrue(ec.build_start('LSV',p).startswith(b'S,800,-200'))
  p=self.defaults('AMP');p['duration_s']=0;self.assertEqual(ec.build_start('AMP',p),b'S,0,100,400\n')
  p['rate_hz']=0.1;self.assertEqual(ec.build_start('AMP',p),b'S,0,10000,400\n')

class SessionEvidence(unittest.TestCase):
 def test_records_are_retained_with_gaps_flagged_and_no_startup_skip(self):
  s=ec.Session('LSV')
  for line in ['# CV_META_SCHEMA,MSP430_CV_UART_V1','# CV_RUN_BEGIN','CV,0,0,10,-200,-2',
               'CV,0,2,410,-180,-1.8','CV,0,2,410,-180,-1.8','CV,1,0,10,-200,-2']:
   s.feed(line,arrival=1)
  self.assertTrue(s.compatible);self.assertEqual(len(s.records),4)
  self.assertEqual(s.records[0]['potential_mV'],-200)
  self.assertEqual(len(s.warnings),3)
 def test_wrong_firmware_does_not_enable_start(self):
  s=ec.Session('CA');s.feed('# CA_META_SCHEMA,MSP430_AMP_UART_V4');self.assertFalse(s.compatible)
 def test_real_units_for_ocp_and_ca(self):
  s=ec.Session('OCP');s.feed('OCP,0,20,-0.3',arrival=1)
  self.assertEqual(s.records[0]['voltage_V'],-0.3)
  s=ec.Session('CA');s.feed('# CA_META_STEP_MV,400');s.feed('CA,0,0,20',arrival=1)
  self.assertEqual(s.records[0]['current_uA'],20);self.assertEqual(s.records[0]['potential_mV'],400)
 def test_metadata_changes_and_command_events_survive_roundtrip(self):
  s=ec.Session('AMP')
  for line in ['# AMP_META_POTENTIAL_MV,400','# AMP_RUN_BEGIN,1,1000','AMP,0,0,20',
               '# AMP_RUN_END,COMPLETED,1','# AMP_META_POTENTIAL_MV,200','# AMP_RUN_BEGIN,2,1000','AMP,0,0,10']:
   s.feed(line,arrival=1)
  s.notes=['Blank then AA'];s.pending_parameters={'duration_s':'1'}
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'test.csv';ec.save_session(path,s);loaded=ec.load_session(path,'AMP')
   self.assertEqual(loaded.records,s.records);self.assertEqual(loaded.events,s.events)
   self.assertEqual(loaded.notes,s.notes);self.assertEqual(loaded.pending_parameters,s.pending_parameters)
   self.assertEqual([r['potential_mV'] for r in loaded.records],[400,200])
   with self.assertRaises(FileExistsError):ec.save_session(path,s)
   with self.assertRaises(ValueError):ec.load_session(path,'OCP')
 def test_bad_records_are_flagged_instead_of_silent_discard(self):
  s=ec.Session('OCP');s.feed('OCP,1,2,nan');s.feed('OCP,0,-1,1')
  self.assertEqual(len(s.warnings),2);self.assertFalse(s.records)
 def test_reference_unit_conversion_and_rejection(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'lab.csv';p.write_text('Potential (V);Current (A)\n0.4;0.00002\n')
   records=ec.load_reference(p,'LSV');self.assertEqual(records[0]['potential_mV'],400);self.assertAlmostEqual(records[0]['current_uA'],20)
   p.write_text('time (s),voltage (V)\n1,0.2\n')
   records=ec.load_reference(p,'OCP');self.assertEqual(records[0]['time_ms'],1000)
   p.write_text('Potential,Current\n0.4,20\n')
   with self.assertRaises(ValueError):ec.load_reference(p,'LSV')
 def test_date_purpose_names_never_overwrite(self):
  with tempfile.TemporaryDirectory() as d:
   p=ec.filename(d,'CA','AA precheck',date='2026-10-06')
   self.assertEqual(p.name,'2026-10-06_CA_AA_precheck_Run01.csv')
   p.with_suffix('.png').touch()
   self.assertEqual(ec.filename(d,'CA','AA precheck',date='2026-10-06').name,'2026-10-06_CA_AA_precheck_Run02.csv')

if __name__=='__main__':unittest.main(verbosity=2)
