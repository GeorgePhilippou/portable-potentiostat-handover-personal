#!/usr/bin/env python3
"""Build deterministic, matched firmware/dashboard downloads. Run --check in CI."""
from pathlib import Path
import hashlib,io,json,sys,zipfile
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'CCS_Import_Projects'
REPO='https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal'
PROJECTS=['Handover_OCP','Handover_Chronoamperometric','Handover_Amperometric','Handover_CyclicVoltammetry','Handover_EIS']
EXCLUDE={'.git','__pycache__','Debug','Release','.launches','.settings','tools'}

def source_files(project):
    folder=SOURCE/project
    return {str(p.relative_to(folder)):p.read_bytes() for p in sorted(folder.rglob('*'))
            if p.is_file() and not any(x in EXCLUDE for x in p.relative_to(folder).parts)
            and p.name not in {'.DS_Store','README.md'} and p.suffix not in {'.pyc','.out','.obj','.map','.log'}}

def zip_bytes(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,10,19,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            z.writestr(info,data)
    return out.getvalue()

def build():
    firmware={p:source_files(p) for p in PROJECTS}
    python={'potentiostat_dashboard.py':(SOURCE/'potentiostat_dashboard.py').read_bytes(),
            'ec_dashboard.py':(SOURCE/'ec_dashboard.py').read_bytes(),'requirements.txt':(ROOT/'requirements.txt').read_bytes()}
    for p in sorted((SOURCE/'dashboard_assets').rglob('*')):
        if p.is_file():python[str(p.relative_to(SOURCE))]=p.read_bytes()
    h=hashlib.sha256()
    for name,data in sorted({**{f'{p}/{n}':v for p,files in firmware.items() for n,v in files.items()},**{f'Python/{n}':v for n,v in python.items()}}.items()):
        h.update(name.encode()+b'\0'+data+b'\0')
    bundle=h.hexdigest()[:16]
    packages={};metadata=[]
    for project,files in firmware.items():
        readme=f'''# {project} — firmware source

This ZIP is the complete CCS firmware source project, not a pre-flashed binary.

1. Extract the ZIP to a local folder.
2. In classic Code Composer Studio 12.5, use Project → Import CCS Projects and select this {project} folder.
3. Select the project, Build, then use your usual Flash action with the TI MSP-FET and MSP430FR5994 board.
4. On the computer, extract the separate Python_Dashboard.zip and run its potentiostat_dashboard.py. Install its requirements.txt first.

Hardware configuration is in main.c. Toolchain used: TI MSP430 compiler 21.6.1.LTS. CV and LSV share Handover_CyclicVoltammetry; choose the mode in Python.

Connections: {REPO}/blob/main/docs/HARDWARE.md
Customisation: {REPO}/blob/main/docs/CUSTOMISATION.md

Matched download set: {bundle}. Download firmware and Python from the same downloads page. PACKAGE.json identifies the set and included source hashes.
'''
        if project=='Handover_EIS':readme+='\nEIS IS IN DEVELOPMENT. This source is experimental; see the repository validation/EIS notes before use.\n'
        files={**files,'README.md':readme.encode(),'THIRD_PARTY_NOTICES.md':(ROOT/'THIRD_PARTY_NOTICES.md').read_bytes()}
        package={'download_set':bundle,'kind':'firmware','project':project,'file_sha256':{n:hashlib.sha256(v).hexdigest() for n,v in files.items()}}
        files['PACKAGE.json']=(json.dumps(package,indent=2)+'\n').encode()
        rel=f'firmware/{project}.zip';data=zip_bytes({f'{project}/{n}':v for n,v in files.items()});packages[rel]=data
        metadata.append({'path':rel,'kind':'firmware','project':project,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
    readme=f'''# Python dashboard — run on the computer

This is the shared dashboard for OCP, chronoamperometry, amperometry, CV/LSV and experimental EIS. It is editable Python source.

1. Install Python (tested with 3.11); Spyder is optional.
2. Extract this ZIP and open a terminal in the extracted Python_Dashboard folder.
3. Install dependencies with: python -m pip install -r requirements.txt
4. Run: python potentiostat_dashboard.py
   Alternatively open that file in Spyder using the same Python environment.
5. Close other serial readers, then Detect and open. The launcher identifies the flashed firmware; CV and LSV share a firmware image and you choose their mode in the launcher.

The matching firmware ZIP must be built/flashed in Code Composer Studio first. Keep ec_dashboard.py and dashboard_assets alongside the launcher; do not move the launcher by itself.

Matched download set: {bundle}. PACKAGE.json identifies the set and exact source hashes. No firmware code is contained in this dashboard ZIP.
Setup and wiring: {REPO}/blob/main/docs/START_HERE.md
Customisation: {REPO}/blob/main/docs/CUSTOMISATION.md
EIS remains in development.
'''
    python['README.md']=readme.encode();python['THIRD_PARTY_NOTICES.md']=(ROOT/'THIRD_PARTY_NOTICES.md').read_bytes()
    package={'download_set':bundle,'kind':'python-dashboard','file_sha256':{n:hashlib.sha256(v).hexdigest() for n,v in python.items()}}
    python['PACKAGE.json']=(json.dumps(package,indent=2)+'\n').encode();data=zip_bytes({f'Python_Dashboard/{n}':v for n,v in python.items()});packages['Python_Dashboard.zip']=data
    metadata.append({'path':'Python_Dashboard.zip','kind':'python-dashboard','sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
    manifest={'download_set':bundle,'description':'Firmware source and Python dashboard from one matched source set','packages':metadata}
    packages['MANIFEST.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    return packages

if __name__=='__main__':
    packages=build();check='--check' in sys.argv
    for name,data in packages.items():
        path=ROOT/'downloads'/name
        if check:
            if not path.exists() or path.read_bytes()!=data:raise SystemExit(f'Download needs rebuilding: {name}; run python scripts/build_downloads.py')
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    print(('Verified' if check else 'Built')+' 5 firmware ZIPs and the shared Python dashboard ZIP; source set '+json.loads(packages['MANIFEST.json'])['download_set'])
