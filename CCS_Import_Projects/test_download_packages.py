import hashlib,json,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class Downloads(unittest.TestCase):
 def test_matching_packages_are_complete_and_verified(self):
  manifest=json.loads((ROOT/'downloads/MANIFEST.json').read_text());sets=[]
  for package in manifest['packages']:
   p=ROOT/'downloads'/package['path'];self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),package['sha256'])
   with zipfile.ZipFile(p) as z:
    self.assertIsNone(z.testzip());metadata=json.loads(z.read(next(n for n in z.namelist() if n.endswith('/PACKAGE.json'))));sets.append(metadata['download_set'])
    prefix=next(n.split('/')[0] for n in z.namelist())+'/'
    for name,sha in metadata['file_sha256'].items():self.assertEqual(hashlib.sha256(z.read(prefix+name)).hexdigest(),sha)
    if package['kind']=='firmware':
     for name in ['main.c','.project','.cproject','.ccsproject','MSP430_FR5969_Port.c']:self.assertIn(prefix+name,z.namelist())
     self.assertFalse(any('/tools/' in n for n in z.namelist()))
  self.assertEqual(set(sets),{manifest['download_set']});self.assertEqual(len(sets),6)
 def test_extracted_python_package_loads_shared_engine(self):
  with tempfile.TemporaryDirectory() as d:
   with zipfile.ZipFile(ROOT/'downloads/Python_Dashboard.zip') as z:z.extractall(d)
   folder=Path(d)/'Python_Dashboard'
   self.assertTrue((folder/'requirements.txt').exists());self.assertTrue((folder/'dashboard_assets/chevron-down.svg').exists())
   command="import potentiostat_dashboard as launcher; engine=launcher.load_dashboard(); assert engine.build_start('CA',{'base_mV':0,'step_mV':100,'duration_s':20,'hold_ms':20000})==b'S,0,100,20000,20000\\n'"
   result=subprocess.run([sys.executable,'-c',command],cwd=folder,capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr)
if __name__=='__main__':unittest.main()
