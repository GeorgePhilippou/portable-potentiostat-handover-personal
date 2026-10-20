#!/usr/bin/env python3
"""Individual electrochemical experiments with a shared UART/session engine.

Run cv_experiment.py, lsv_experiment.py, amp_experiment.py,
chrono_experiment.py or ocp_experiment.py. No IMU acquisition is included.
All saved samples are retained, including startup/transient samples.
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path

SCHEMAS = {'CV':'MSP430_CV_UART_V1','LSV':'MSP430_CV_UART_V1',
           'AMP':'MSP430_AMP_UART_V4','CA':'MSP430_CA_UART_V1','OCP':'MSP430_OCP_UART_V1','EIS':'MSP430_EIS_UART_V2'}
BAUD = {'CV':9600,'LSV':9600,'AMP':9600,'OCP':9600,'CA':9600,'EIS':9600}
FIELDS = {
 'EIS':[('start_hz','Start frequency (Hz)',1000),('stop_hz','End frequency (Hz)',20000),
        ('points','Frequency points',16),('amplitude_mVpp','Excitation (mV p-p)',20),
        ('period_ms','Minimum point interval (ms)',1000),('repeats','Sweeps',1),('log_grid','Log grid (1=yes)',1)],
 'CV':[('start_mV','Start (mV)',-200),('peak_mV','Vertex (mV)',800),('steps','Total steps',100),
       ('scan_rate','Scan rate (mV/s)',50),('delay_ms','Sample delay (ms)',10),('cycles','Cycles',1),('hold_ms','Initial hold (ms)',500)],
 'LSV':[('start_mV','Start (mV)',-200),('peak_mV','End (mV)',800),('steps','Steps',100),
        ('scan_rate','Scan rate (mV/s)',50),('delay_ms','Sample delay (ms)',10),('cycles','Sweeps',1),('hold_ms','Initial hold (ms)',500)],
 'AMP':[('potential_mV','Potential (mV)',400),('duration_s','Duration (s; 0=continuous)',60),('rate_hz','Sampling frequency (Hz)',10)],
 'OCP':[('duration_s','Duration (s; 0=continuous)',60),('rate_hz','Sampling frequency (Hz)',10)],
 'CA':[('base_mV','Initial potential (mV)',0),('step_mV','Step potential (mV)',400),
       ('duration_s','Step duration (s)',1),('hold_ms','Initial hold (ms)',500)],
}
COLUMNS = ['technique','run','sweep','index','time_ms','potential_mV','current_uA','voltage_V','arrival_time_s','adc_code',
           'frequency_hz','zreal_ohm','zimag_ohm','magnitude_ohm','phase_deg']


def ocp_voltage_label(meta):
    """Keep internal ADC bench tests distinct from electrode OCP."""
    convention = meta.get('POTENTIAL_CONVENTION', 'E_WE_MINUS_E_RE')
    if convention == 'INTERNAL_REFERENCE_VOLTAGE':
        return 'Internal 1.1 V reference (V)'
    if convention == 'ADC_POSITIVE_MINUS_NEGATIVE':
        return 'ADC differential voltage (V)'
    if convention == 'ADC_NEGATIVE_MINUS_POSITIVE':
        return 'ADC negative − positive voltage (V)'
    return 'WE − RE potential (V)'


def finite(value):
    number=float(value)
    if not math.isfinite(number): raise ValueError('Parameters and samples must be finite numbers')
    return number


def integer(value):
    number=finite(value)
    if number != int(number): raise ValueError('This parameter requires a whole number')
    return int(number)


def bounded(value, low, high, label):
    number=integer(value)
    if not low <= number <= high: raise ValueError(f'{label} must be between {low} and {high}')
    return number


def duration_ms(value, low=0, high=2147483647):
    number=finite(value)*1000
    result=round(number)
    if not low <= result <= high or abs(number-result)>0.00001:
        raise ValueError('Duration must be within range and use whole milliseconds')
    return result


def build_start(technique, values):
    if technique in {'CV','LSV'}:
        start=bounded(values['start_mV'],-800,800,'Start potential')
        peak=bounded(values['peak_mV'],-800,800,'End/vertex potential')
        steps=bounded(values['steps'],4,2000,'Steps')
        rate=bounded(values['scan_rate'],1,1000,'Scan rate')
        delay=bounded(values['delay_ms'],1,10000,'Sample delay')
        cycles=bounded(values['cycles'],1,100,'Cycles')
        hold=bounded(values['hold_ms'],0,60000,'Initial hold')
        one_dir=int(technique=='LSV')
        if start==peak: raise ValueError('Start and end/vertex potentials must differ')
        if technique=='CV' and steps%2: raise ValueError('CV requires an even total step count')
        period=abs(peak-start)*(1 if one_dir else 2)*1000/rate/steps
        if not 100 <= period <= 10000 or delay > period-2:
            raise ValueError('Choose a step period of 100–10000 ms, with sample delay at least 2 ms shorter')
        args=[start,peak,steps,rate,delay,cycles,one_dir,hold]
    elif technique in {'AMP','OCP'}:
        duration=duration_ms(values['duration_s'])
        rate=finite(values['rate_hz'])
        if not 0.1 <= rate <= 20: raise ValueError('Sampling frequency must be 0.1–20 Hz')
        period=round(1000/rate)
        args=[duration,period]
        if technique=='AMP': args.append(bounded(values['potential_mV'],-800,800,'Potential'))
    elif technique=='CA':
        base=bounded(values['base_mV'],-800,800,'Initial potential')
        step=bounded(values['step_mV'],-800,800,'Step potential')
        if base==step: raise ValueError('Chronoamperometry requires a potential step')
        args=[base,step,duration_ms(values['duration_s'],10,60000),bounded(values['hold_ms'],0,60000,'Initial hold')]
    elif technique=='EIS':
        start=int(round(finite(values['start_hz'])*1000))
        if not 100<=start<=200000000:raise ValueError('Start frequency must be 0.1–200000 Hz')
        stop=int(round(finite(values['stop_hz'])*1000))
        if not 100<=stop<=200000000:raise ValueError('End frequency must be 0.1–200000 Hz')
        if stop<=start:raise ValueError('End frequency must exceed start frequency')
        args=[start,stop,bounded(values['points'],2,100,'Frequency points'),
              bounded(values['amplitude_mVpp'],2,40,'Excitation mV p-p'),
              bounded(values['period_ms'],500,10000,'Point interval ms'),
              bounded(values['repeats'],1,10,'Sweeps'),bounded(values['log_grid'],0,1,'Log grid')]
    else: raise ValueError('Unknown technique')
    return ('S,'+','.join(map(str,args))+'\n').encode('ascii')


class Session:
    def __init__(self, technique):
        self.technique=technique
        self.meta={}
        self.records=[]
        self.events=[]
        self.notes=[]
        self.warnings=[]
        self.run=0
        self.state='UNKNOWN'
        self.last={}
        self.pending_parameters={}
        self.plot_settings={}
        self.started_at=None

    @property
    def compatible(self):
        return self.meta.get('SCHEMA') == SCHEMAS[self.technique]

    def feed(self,line,arrival=None):
        arrival=time.monotonic() if arrival is None else arrival
        prefix='CV' if self.technique in {'CV','LSV'} else self.technique
        if line.startswith('# '+prefix+'_META_'):
            key,value=line[len('# '+prefix+'_META_'):].split(',',1)
            self.meta[key]=value
            self.events.append({"arrival_time_s":arrival,"line":line})
            return
        if line.startswith('#'):
            self.events.append({'arrival_time_s':arrival,'line':line})
            if line.startswith('# '+prefix+'_STATE,'):
                self.state=line.split(',',1)[1]
            elif line.startswith('# '+prefix+'_RUN_BEGIN'):
                self.run+=1
                self.last={}
                self.started_at=arrival
            elif line.startswith('# '+prefix+'_SWEEP_END,'):
                fields=line.split(',')
                if len(fields)==3:
                    sweep=int(fields[1]);expected=int(fields[2])
                    received=sum(r['run']==self.run and r['sweep']==sweep for r in self.records)
                    if received!=expected: self.warnings.append(f'Sweep {sweep+1}: device sent {expected}, PC retained {received}')
            elif line.startswith('# '+prefix+'_RUN_END,'):
                self.state='IDLE'
                fields=line.split(',');reason=fields[1]
                if prefix!='CV' and len(fields)>=3:
                    expected=int(fields[2]);received=sum(r['run']==self.run for r in self.records)
                    if received!=expected: self.warnings.append(f'Run: device sent {expected}, PC retained {received}')
                if reason != 'COMPLETED' and reason != 'USER_STOP': self.warnings.append(reason)
            elif line.startswith('# '+prefix+'_ERROR,'): self.warnings.append(line)
            return
        if not line.startswith(prefix+','): return
        try:
            parts=line.split(',')
            adc_code=''
            impedance={}
            if prefix=='EIS':
                if len(parts)!=9:raise ValueError('Wrong EIS record length')
                sweep=integer(parts[1]);index=integer(parts[2]);stamp=finite(parts[3])
                impedance=dict(zip(['frequency_hz','zreal_ohm','zimag_ohm','magnitude_ohm','phase_deg'],map(finite,parts[4:])))
                if impedance['frequency_hz']<=0 or impedance['magnitude_ohm']<=0 or abs(impedance['phase_deg'])>180.001:
                    raise ValueError('Invalid frequency, magnitude or phase')
                if abs(math.hypot(impedance['zreal_ohm'],impedance['zimag_ohm'])-impedance['magnitude_ohm'])>max(.1,impedance['magnitude_ohm']*1e-5):
                    raise ValueError('Complex impedance and magnitude disagree')
                potential=current=voltage=''
            elif prefix=='CV':
                if len(parts)!=6: raise ValueError('Wrong CV record length')
                sweep=integer(parts[1]);index=integer(parts[2]);stamp=finite(parts[3])
                potential=finite(parts[4]);current=finite(parts[5]);voltage=''
            else:
                if len(parts) not in ({4,5} if prefix=='OCP' else {4}): raise ValueError('Wrong record length')
                sweep=0;index=integer(parts[1]);stamp=finite(parts[2]);number=finite(parts[3])
                potential='';current='';voltage=''
                if prefix=='OCP':
                    voltage=number
                    if len(parts)==5:
                        adc_code=bounded(parts[4],0,65535,'ADC code')
                else:
                    current=number
                    potential=finite(self.meta.get('STEP_MV' if prefix=='CA' else 'POTENTIAL_MV',0))
            if index<0 or stamp<0 or sweep<0: raise ValueError('Negative sample index/time')
        except (ValueError,IndexError,OverflowError) as exc:
            self.warnings.append(f'Malformed record: {line}: {exc}')
            return
        key=(self.run,sweep)
        previous=self.last.get(key)
        if previous is None and index!=0: self.warnings.append(f'Capture began at sample {index}')
        if previous is not None:
            if index != previous[0]+1: self.warnings.append(f'Sample sequence {previous[0]} → {index} in sweep {sweep+1}')
            if stamp <= previous[1]: self.warnings.append('Non-increasing device sample time')
        self.last[key]=(index,stamp)
        record={key:'' for key in COLUMNS}
        record.update(dict(zip(COLUMNS,[self.technique,self.run,sweep,index,stamp,potential,current,voltage,arrival,adc_code])))
        record.update(impedance)
        self.records.append(record)


def filename(directory,technique,purpose='experiment',date=None):
    date=date or datetime.now().strftime('%Y-%m-%d')
    purpose=re.sub(r'[^\w-]+','_',purpose.strip()).strip('_')[:56] or 'experiment'
    directory=Path(directory)
    for run in range(1,100000):
        path=directory/f'{date}_{technique}_{purpose}_Run{run:02d}.csv'
        if not path.exists() and not path.with_suffix('.png').exists(): return path
    raise ValueError('No unused session filename available')


def save_session(path, session):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    payload={'format':'EC_HANDOVER_SESSION_V1','technique':session.technique,'meta':session.meta,
             'notes':session.notes,'events':session.events,'warnings':session.warnings,
             'parameters':session.pending_parameters,'plot_settings':session.plot_settings,'saved_at':datetime.now().astimezone().isoformat()}
    # Exclusive creation protects an existing recording, even after a dialog selects it.
    with path.open('x',newline='',encoding='utf-8') as stream:
        stream.write('# '+json.dumps(payload,ensure_ascii=False)+'\n')
        writer=csv.DictWriter(stream,fieldnames=COLUMNS)
        writer.writeheader();writer.writerows(session.records)
    return path


def load_session(path, expected=None):
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:
        first=stream.readline()
        if not first.startswith('# '): raise ValueError('This is not a handover session CSV')
        payload=json.loads(first[2:])
        if payload.get('format')!='EC_HANDOVER_SESSION_V1': raise ValueError('Unsupported session format')
        technique=payload['technique']
        if expected and technique!=expected: raise ValueError(f'This recording is {technique}, not {expected}')
        result=Session(technique)
        result.meta=payload['meta'];result.notes=payload['notes'];result.events=payload['events']
        result.warnings=payload['warnings'];result.pending_parameters=payload.get('parameters',{})
        result.plot_settings=payload.get('plot_settings',{})
        for row in csv.DictReader(stream):
            if None in row or any(value is None for value in row.values()):
                raise ValueError('Session CSV row does not match its header; the recording may be incomplete')
            for key in COLUMNS[1:]:
                row.setdefault(key, '')
                if row[key]!='': row[key]=integer(row[key]) if key in {'run','sweep','index','adc_code'} else finite(row[key])
            result.records.append(row)
        result.run=max((row['run'] for row in result.records),default=0)
        result.state='REVIEW'
        return result


def load_reference(path, technique):
    """Load a saved session or CSV with explicit units; never infer ambiguous units."""
    if technique=='EIS':return load_session(path,'EIS').records
    text=Path(path).read_text(encoding='utf-8-sig')
    if text.startswith('# '):
        try:
            other=load_session(path,technique)
            return other.records
        except (ValueError,KeyError,json.JSONDecodeError): pass
    lines=[s for s in text.splitlines() if s.strip() and not s.startswith('#')]
    reader=csv.reader(lines,delimiter=';' if any(';' in s for s in lines[:5]) else ',')
    rows=list(reader)
    aliases={
        'potential_mV':{'potential_mv','e / mv','potential (mv)','ewe/mv'},
        'potential_V':{'potential_v','e / v','potential (v)','ewe/v'},
        'current_uA':{'current_ua','i / ua','current (ua)','i/ua'},
        'current_A':{'current_a','i / a','current (a)','i/a'},
        'time_s':{'time_s','time (s)','time / s','t/s'},
        'time_ms':{'time_ms','time (ms)','time / ms','t/ms'},
        'voltage_V':{'voltage_v','voltage (v)','ocp_v'},
    }
    for h, row in enumerate(rows):
        normalized=[cell.lower().strip().replace('µ','u').replace('μ','u') for cell in row]
        mapping={key:normalized.index(value) for key,values in aliases.items() for value in values if value in normalized}
        xkeys={'potential_mV','potential_V'} if technique in {'CV','LSV'} else {'time_ms','time_s'}
        ykeys={'voltage_V','potential_V'} if technique=='OCP' else {'current_uA','current_A'}
        if not (xkeys & mapping.keys() and ykeys & mapping.keys()): continue
        result=[]
        for data in rows[h+1:]:
            if not data or all(not c.strip() for c in data): continue
            record={key:'' for key in COLUMNS}
            try:
                for key,index in mapping.items(): record[key]=finite(data[index])
                if 'potential_V' in mapping: record['potential_mV']=record['potential_V']*1000
                if 'current_A' in mapping: record['current_uA']=record['current_A']*1e6
                if 'time_s' in mapping: record['time_ms']=record['time_s']*1000
                if technique=='OCP' and 'voltage_V' not in mapping: record['voltage_V']=record['potential_V']
            except (ValueError,IndexError) as exc: raise ValueError('Reference CSV contains an invalid data row') from exc
            result.append(record)
        if not result: raise ValueError('Reference CSV has no data')
        return result
    raise ValueError('Reference CSV needs labelled columns with explicit potential/current/time units')


def parameter_defaults(technique, overrides=None):
    """Apply script settings without changing defaults for another experiment."""
    values = {key: default for key, _, default in FIELDS[technique]}
    if overrides:
        unknown = set(overrides) - set(values)
        if unknown:
            raise ValueError('Unknown experiment setting(s): ' + ', '.join(sorted(unknown)))
        values.update(overrides)
    build_start(technique, values)  # Validate before opening the device/window.
    return values


PALETTE = ['#00897b', '#e67e22', '#7356bf', '#2474bd', '#c04680']
ACCENTS = dict(zip(['OCP','CA','AMP','CV','LSV'], PALETTE));ACCENTS['EIS']='#067f91'
NAMES = {'OCP':'Open-circuit potential', 'CA':'Chronoamperometry',
         'AMP':'Amperometry', 'CV':'Cyclic voltammetry', 'LSV':'Linear sweep voltammetry','EIS':'Electrochemical impedance spectroscopy'}


def display_values(rows, key, window=1):
    """Trailing average for display only, restarting at each run/sweep boundary."""
    from collections import deque
    window=bounded(window,1,1000,'Display average samples')
    queue=deque();total=0.0;previous=None;result=[]
    for row in rows:
        group=(row.get('run'),row.get('sweep'))
        if group!=previous:queue.clear();total=0.0;previous=group
        value=finite(row[key]);queue.append(value);total+=value
        if len(queue)>window:total-=queue.popleft()
        result.append(total/len(queue))
    return result


def zero_limits(values, minimum_span):
    """A broad, zero-inclusive range rounded outward to readable boundaries."""
    values=[finite(v) for v in values]
    low=min([0.0]+values);high=max([0.0]+values)
    span=max(high-low,minimum_span)
    step=10**math.floor(math.log10(span))/2
    if low<0 and high>0:
        return math.floor((low-span*.05)/step)*step,math.ceil((high+span*.05)/step)*step
    if low<0:return -math.ceil(max(-low*1.1,minimum_span)/step)*step,0.0
    return 0.0,math.ceil(max(high*1.1,minimum_span)/step)*step


def validate_plot_settings(values, technique):
    result=dict(values)
    for axis in ('x','y'):
        if result[axis+'_mode'] not in ('Auto','Fixed','Latest window','From zero','Include zero'):
            raise ValueError('Unknown axis mode')
        if axis == 'y' and result[axis+'_mode'] == 'Latest window':
            raise ValueError('Latest window applies only to time')
        if result[axis+'_mode']=='Fixed':
            low=finite(result[axis+'_min']);high=finite(result[axis+'_max'])
            if low>=high: raise ValueError(f'{axis.upper()} minimum must be smaller than maximum')
            result[axis+'_min']=low;result[axis+'_max']=high
    if result['x_mode']=='Include zero' or result['y_mode']=='From zero':raise ValueError('Use From zero for time and Include zero for Y')
    if result['x_mode']=='From zero' and technique in {'CV','LSV'}:raise ValueError('From zero is for time plots; potential can be negative')
    result['hidden_runs']=[bounded(v,0,2147483647,'Run number') for v in result.get('hidden_runs',[])]
    result['hidden_overlays']=[bounded(v,0,2147483647,'Overlay number') for v in result.get('hidden_overlays',[])]
    result['average_samples']=bounded(result.get('average_samples',1),1,1000,'Display average samples')
    if technique=='EIS' and result['average_samples']!=1:raise ValueError('EIS plots retain individual frequency points; averaging across frequencies is disabled')
    if result['x_mode']=='Latest window':
        if technique in {'CV','LSV','EIS'}: raise ValueError('Latest window is for time plots; use Fixed for potential')
        result['window_s']=finite(result['window_s'])
        if result['window_s']<=0: raise ValueError('Time window must be positive')
    if result['style'] not in ('Line','Line + points','Points'): raise ValueError('Unknown trace style')
    return result


def apply_plot_limits(ax, settings, latest_time=None, *, duration_s=0, minimum_y_span=.5):
    if settings['x_mode']=='Fixed': ax.set_xlim(settings['x_min'], settings['x_max'])
    elif settings['x_mode']=='Latest window' and latest_time is not None:
        end=max(settings['window_s'],latest_time)
        ax.set_xlim(end-settings['window_s'],end)
    elif settings['x_mode']=='From zero':ax.set_xlim(0,max(1.0,finite(duration_s),latest_time or 0))
    if settings['y_mode']=='Fixed': ax.set_ylim(settings['y_min'], settings['y_max'])
    elif settings['y_mode']=='Include zero':
        values=[float(v) for line in ax.lines for v in line.get_ydata()]
        ax.set_ylim(*zero_limits(values,minimum_y_span))


def recorded_frequency(meta, technique):
    """Rate of retained measurements, distinct from the internal ADC clock."""
    try:
        if technique == 'CA':value=finite(meta['SAMPLE_RATE_HZ'])
        elif technique in {'OCP','AMP'}:value=1000/finite(meta['SAMPLE_PERIOD_MS'])
        else:value=1000*finite(meta['STEPS'])/finite(meta['DURATION_MS'])
        return value if value>0 else None
    except (KeyError,ValueError,ZeroDivisionError):return None


def run_visibility_dialog(parent, runs, overlay_names, hidden_runs, hidden_overlays):
    """Choose traces shown on screen; never remove acquired or saved samples."""
    from matplotlib.backends.qt_compat import QtWidgets
    dialog=QtWidgets.QDialog(parent);dialog.setWindowTitle('Manage plotted runs');dialog.resize(450,360)
    layout=QtWidgets.QVBoxLayout(dialog)
    hint=QtWidgets.QLabel('Uncheck a run to remove it from the plot.\nAll raw samples remain available for saving.');layout.addWidget(hint)
    scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);body=QtWidgets.QWidget();rows=QtWidgets.QVBoxLayout(body)
    choices=[]
    for run,count in runs:
        box=QtWidgets.QCheckBox(f'Run {run} · {count} samples');box.setChecked(run not in hidden_runs);rows.addWidget(box);choices.append(('run',run,box))
    for index,name in enumerate(overlay_names):
        box=QtWidgets.QCheckBox('Overlay · '+name);box.setChecked(index not in hidden_overlays);rows.addWidget(box);choices.append(('overlay',index,box))
    rows.addStretch();scroll.setWidget(body);layout.addWidget(scroll)
    tools=QtWidgets.QHBoxLayout()
    for label,checked in [('Show all',True),('Clear plot',False)]:
        button=QtWidgets.QPushButton(label);button.clicked.connect(lambda _,value=checked:[box.setChecked(value) for _,_,box in choices]);tools.addWidget(button)
    layout.addLayout(tools)
    standard=getattr(QtWidgets.QDialogButtonBox,'StandardButton',QtWidgets.QDialogButtonBox)
    buttons=QtWidgets.QDialogButtonBox(standard.Ok | standard.Cancel);layout.addWidget(buttons)
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
    accepted=getattr(QtWidgets.QDialog,'DialogCode',QtWidgets.QDialog).Accepted
    if dialog.exec()!=accepted:return None
    return ([key for kind,key,box in choices if kind=='run' and not box.isChecked()],
            [key for kind,key,box in choices if kind=='overlay' and not box.isChecked()])


def plot_settings_dialog(parent, technique, settings):
    from matplotlib.backends.qt_compat import QtWidgets
    settings={'average_samples':1,'show_raw':False,**settings}
    dialog=QtWidgets.QDialog(parent);dialog.setWindowTitle('Plot settings — '+NAMES[technique])
    form=QtWidgets.QFormLayout(dialog)
    x_mode=QtWidgets.QComboBox();x_mode.addItems(['Auto','Fixed']+(['From zero'] if technique=='EIS' else [] if technique in {'CV','LSV'} else ['From zero','Latest window']))
    x_mode.setCurrentText(settings['x_mode']);form.addRow('X axis',x_mode)
    y_mode=QtWidgets.QComboBox();y_mode.addItems(['Include zero','Auto','Fixed']);y_mode.setCurrentText(settings['y_mode']);form.addRow('Y axis',y_mode)
    boxes={}
    xunit='Ω' if technique=='EIS' else 'V' if technique in {'CV','LSV'} else 's';yunit='Ω' if technique=='EIS' else 'V' if technique=='OCP' else 'µA'
    for key,label in [('x_min','X minimum ('+xunit+')'),('x_max','X maximum ('+xunit+')'),
                      ('y_min','Y minimum ('+yunit+')'),('y_max','Y maximum ('+yunit+')'),
                      ('window_s','Latest time window (s)'),('average_samples','Display average (samples; 1 = raw)')]:
        box=QtWidgets.QLineEdit(str(settings[key]));boxes[key]=box;form.addRow(label,box)
    if technique=='EIS':boxes['average_samples'].setEnabled(False)
    style=QtWidgets.QComboBox();style.addItems(['Line','Line + points','Points']);style.setCurrentText(settings['style']);form.addRow('Trace',style)
    grid=QtWidgets.QCheckBox('Show grid');grid.setChecked(settings['grid']);form.addRow(grid)
    legend=QtWidgets.QCheckBox('Show legend');legend.setChecked(settings['legend']);form.addRow(legend)
    raw=QtWidgets.QCheckBox('Show raw trace behind averaged trace');raw.setChecked(settings.get('show_raw',False));form.addRow(raw)
    hint=QtWidgets.QLabel('Averaging affects the displayed curve, including transients.\nDisplay settings only. All raw samples remain in the saved CSV.\nFor CV/LSV these limits apply to the potential plot; for EIS, Nyquist.')
    form.addRow(hint)
    standard=getattr(QtWidgets.QDialogButtonBox,'StandardButton',QtWidgets.QDialogButtonBox)
    buttons=QtWidgets.QDialogButtonBox(standard.Ok | standard.Cancel)
    form.addRow(buttons);result={}
    def apply():
        values={**settings,**{k:b.text() for k,b in boxes.items()},'x_mode':x_mode.currentText(),
                'y_mode':y_mode.currentText(),'style':style.currentText(),'grid':grid.isChecked(),'legend':legend.isChecked(),'show_raw':raw.isChecked()}
        try: result.update(validate_plot_settings(values,technique))
        except ValueError as exc: QtWidgets.QMessageBox.warning(dialog,'Check plot settings',str(exc));return
        dialog.accept()
    buttons.accepted.connect(apply);buttons.rejected.connect(dialog.reject)
    execute=getattr(dialog,'exec',None) or dialog.exec_
    return result if execute() else None


def main(technique, argv=None, *, default_port=None, default_baud=None,
         default_parameters=None, offline=False, auto_connect=True, minimum_voltage_span=0.002):
    parser=argparse.ArgumentParser(description=f'{technique} portable potentiostat experiment')
    parser.add_argument('--port', default=default_port);parser.add_argument('--baud',type=int,default=default_baud or BAUD[technique])
    parser.add_argument('--offline',action='store_true',default=offline);parser.add_argument('--session')
    parser.add_argument('--list-ports',action='store_true')
    args=parser.parse_args(argv)
    import serial
    import serial.tools.list_ports
    ports=list(serial.tools.list_ports.comports())
    if args.list_ports:
        for port in ports: print(f'{port.device}: {port.description}')
        return
    print('Available serial ports:')
    for port in ports:
        print(f'  {port.device}: {port.description}')
    print(f'Configured {technique} connection: {args.port or "automatic"} at {args.baud} baud')
    settings = parameter_defaults(technique, default_parameters)
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    from matplotlib.widgets import Button,TextBox
    session=load_session(args.session,technique) if args.session else Session(technique)
    if args.session and session.pending_parameters:
        settings=parameter_defaults(technique,{**settings,**session.pending_parameters})
    connection=None;buffer=b'';overlays=[];status='Offline';changed=True;connect_time=0.0
    awaiting_start=False;last_save_directory=Path.cwd()
    received_bytes=0;raw_lines=0;metadata_timeout_reported=False
    accent=ACCENTS[technique]
    fig=plt.figure(figsize=(12.8,8), facecolor='#eef3f8')
    fig.canvas.manager.set_window_title(f'{technique} portable potentiostat')
    fig.text(.075,.945,NAMES[technique],fontsize=18,weight='bold',color='#172b4d')
    subtitle=fig.text(.075,.913,'Portable potentiostat · live acquisition · raw samples retained',fontsize=9,color='#526174')
    from matplotlib.patches import Rectangle
    fig.patches.append(Rectangle((0,.975),1,.025,transform=fig.transFigure,facecolor=accent,edgecolor='none'))
    fig.patches.append(Rectangle((.705,.085),.275,.825,transform=fig.transFigure,
                                facecolor='white',edgecolor='#cbd7e5',zorder=-1))
    phase_ax=None
    if technique=='EIS':
        ax=fig.add_axes([.095,.51,.565,.32]);time_ax=fig.add_axes([.095,.335,.565,.12]);phase_ax=fig.add_axes([.095,.17,.565,.12],sharex=time_ax)
    elif technique in {'CV','LSV'}:
        ax=fig.add_axes([.095,.43,.565,.40]);time_ax=fig.add_axes([.095,.19,.565,.16])
    else: ax=fig.add_axes([.095,.19,.565,.64]);time_ax=None
    controls={};widgets=[]
    short_labels={'start_hz':'Start frequency (Hz)','stop_hz':'End frequency (Hz)',
                  'points':'Frequency points','amplitude_mVpp':'Excitation (mV p-p)',
                  'period_ms':'Minimum point interval (ms)','repeats':'Sweeps','log_grid':'Log grid (1=yes)',
                  'start_mV':'Start (mV)','peak_mV':'Vertex (mV)' if technique=='CV' else 'End (mV)',
                  'steps':'Points','scan_rate':'Scan rate (mV/s)','delay_ms':'Sample delay (ms)',
                  'cycles':'Cycles' if technique=='CV' else 'Sweeps','hold_ms':'Initial hold (ms)',
                  'duration_s':'Duration (s)','rate_hz':'Sampling frequency (Hz)',
                  'potential_mV':'Potential (mV)','base_mV':'Initial (mV)','step_mV':'Step (mV)'}
    for i,(key,label,default) in enumerate(FIELDS[technique]):
        column=i%2;row=i//2;x=.725+column*.125;y=.72-row*.065
        fig.text(x,y+.036,short_labels.get(key,label),fontsize=8.5,color='#526174')
        box=TextBox(fig.add_axes([x,y,.11,.03]),'',initial=str(settings[key]),
                    color='#f5f7fa',hovercolor='#edf2f7')
        controls[key]=box;widgets.append(box)
    fig.text(.725,.886,'Serial port',fontsize=9,color='#526174')
    port_box=TextBox(fig.add_axes([.725,.844,.235,.032]),'',initial=args.port or '',
                     color='#f5f7fa',hovercolor='#edf2f7')
    widgets.append(port_box)
    control_shift=(4-(len(FIELDS[technique])+1)//2)*.065
    fig.text(.725,.403+control_shift,'Session purpose',fontsize=9,color='#526174')
    purpose=TextBox(fig.add_axes([.725,.363+control_shift,.235,.032]),'',initial='experiment',
                    color='#f5f7fa',hovercolor='#edf2f7');widgets.append(purpose)
    fig.text(.725,.333+control_shift,'Session note',fontsize=9,color='#526174')
    note=TextBox(fig.add_axes([.725,.293+control_shift,.235,.032]),'',
                 color='#f5f7fa',hovercolor='#edf2f7');widgets.append(note)
    status_artist=fig.text(.075,.115,status,fontsize=9,color='#334155',wrap=True)
    info=fig.text(.725,(.18 if technique=='EIS' else .16)+control_shift,'',fontsize=8,color='#526174',va='top',wrap=True)

    def set_status(text):
        nonlocal status
        text=str(text)
        if text == status:
            return
        status=text;status_artist.set_text(status[:180]);fig.canvas.draw_idle()
        print('[dashboard] ' + status)

    def dialog(save=False):
        nonlocal last_save_directory
        default=filename(last_save_directory,technique,purpose.text) if save else last_save_directory
        if 'qt' in plt.get_backend().lower():
            # Use the same GUI toolkit as the live window. Creating a Tk root
            # inside a Qt application can terminate the Python process on macOS.
            from matplotlib.backends.qt_compat import QtWidgets
            parent=getattr(fig.canvas.manager, 'window', None)
            if save:
                selected,_=QtWidgets.QFileDialog.getSaveFileName(parent, 'Save session CSV', str(default), 'CSV files (*.csv)')
                if selected and not Path(selected).suffix:
                    selected += '.csv'
            else:
                selected,_=QtWidgets.QFileDialog.getOpenFileName(parent, 'Open session CSV', str(default), 'CSV files (*.csv)')
        else:
            from tkinter import Tk,filedialog
            root=Tk();root.withdraw()
            try:
                if save:
                    selected=filedialog.asksaveasfilename(title='Save session CSV',initialdir=str(default.parent),
                        initialfile=default.name,defaultextension='.csv',filetypes=[('CSV','*.csv')])
                else: selected=filedialog.askopenfilename(filetypes=[('CSV','*.csv')])
            finally: root.destroy()
        return Path(selected) if selected else None

    def add_note(event=None):
        if note.text.strip(): session.notes.append(note.text.strip());note.set_val('');set_status('Note added')

    def connect(event=None):
        nonlocal connection,buffer,connect_time,awaiting_start
        nonlocal received_bytes,raw_lines,metadata_timeout_reported
        try:
            if connection:
                # Reconnect closes a stream without implicitly starting/stopping the device.
                connection.close();connection=None
            current=list(serial.tools.list_ports.comports())
            candidates=[p.device for p in current if 'usbmodem' in p.device or 'usbserial' in p.device or p.device.startswith('COM')]
            port=port_box.text.strip() or None if port_box is not None else args.port
            if port is None:
                if len(candidates)!=1: raise ValueError('Set SERIAL_PORT in the experiment script; available ports are printed in the console')
                port=candidates[0]
            connection=serial.Serial(port,args.baud,timeout=0,write_timeout=1)
            buffer=b'';session.meta={};session.state='UNKNOWN';connect_time=time.monotonic();awaiting_start=False
            received_bytes=0;raw_lines=0;metadata_timeout_reported=False
            connection.write(b'M\n');set_status(f'Connected to {port}; waiting for firmware metadata')
            print(f'[serial] Opened {port} at {args.baud}; requested firmware metadata')
        except (ValueError,serial.SerialException,OSError) as exc:
            if connection: connection.close();connection=None
            set_status(str(exc))

    def start(event=None):
        nonlocal session,changed,awaiting_start
        try:
            if not connection: raise ValueError('Connect the device first')
            if not session.compatible: raise ValueError('Firmware does not match this experiment script; flash the matching build')
            if session.state!='IDLE' or awaiting_start: raise ValueError('Wait for IDLE, or press Stop before starting')
            values={key:box.text for key,box in controls.items()}
            command=build_start(technique,values)
            # Preserve prior records until explicitly saved or cleared by the user.
            session.pending_parameters=values
            connection.write(command);awaiting_start=True
            session.events.append({'arrival_time_s':time.monotonic(),'line':'PC_COMMAND,'+command.decode().strip()})
            changed=True;set_status('Start requested; waiting for device')
        except (ValueError,serial.SerialException,OSError) as exc: set_status(str(exc))

    def stop(event=None):
        nonlocal awaiting_start
        try:
            if not connection: raise ValueError('Device is disconnected; Stop could not be sent')
            connection.write(b'X\n');awaiting_start=False;set_status('Stop requested; waiting for device IDLE')
        except (ValueError,serial.SerialException,OSError) as exc:set_status(str(exc))

    def save(event=None):
        nonlocal last_save_directory
        try:
            add_note()
            if not session.records: raise ValueError('No samples to save')
            path=dialog(True)
            if path:
                if path.with_suffix('.png').exists(): raise ValueError('PNG already exists; choose a new session name')
                session.plot_settings=dict(plot_state['settings'])
                save_session(path,session);fig.savefig(path.with_suffix('.png'),dpi=160)
                last_save_directory=path.parent;set_status(f'Saved {path.name} and PNG')
        except (ValueError,OSError,RuntimeError) as exc:set_status(str(exc))

    def review(event=None):
        nonlocal session,changed
        try:
            if connection: raise ValueError('Disconnect before reviewing a saved session')
            path=dialog()
            if path:
                loaded=load_session(path,technique)
                restored=validate_plot_settings({**plot_state['settings'],**loaded.plot_settings},technique)
                session=loaded;plot_state['settings']=restored;changed=True;set_status(f'Reviewing {path.name}')
        except (ValueError,KeyError,OSError) as exc:set_status(str(exc))

    def overlay(event=None):
        nonlocal changed
        try:
            path=dialog()
            if path: overlays.append((path.name,load_reference(path,technique)));changed=True;set_status(f'Loaded {path.name}')
        except (ValueError,OSError) as exc:set_status(str(exc))

    def disconnect(event=None):
        nonlocal connection
        if connection:
            connection.close();connection=None
        set_status('Disconnected; device may still be running. Reconnect to send Stop.')

    buttons=[('Connect',connect,.725,.79),('Disconnect',disconnect,.85,.79),
             ('Start',start,.725,.452+control_shift),('Stop',stop,.85,.452+control_shift),
             ('Add note',add_note,.725,.237+control_shift),('Save session',save,.85,.237+control_shift),
             ('Review',review,.725,.185+control_shift),('Overlay',overlay,.85,.185+control_shift)]
    for label,callback,x,y in buttons:
        color=accent if label in {'Start','Save session','Connect'} else '#e8eef5'
        button=Button(fig.add_axes([x,y,.11,.035]),label,color=color,hovercolor=accent if label in {'Start','Save session','Connect'} else '#dce7f5')
        button.label.set_fontsize(9)
        if label in {'Start','Save session','Connect'}: button.label.set_color('white')
        button.on_clicked(callback);widgets.append(button)

    plot_state={'held':False,'settings':{
        'x_mode':'Auto' if technique in {'CV','LSV'} else 'From zero','y_mode':'Include zero','x_min':0.0,'x_max':60.0 if technique not in {'CV','LSV'} else 1.0,
        'y_min':0.0,'y_max':2.0 if technique=='OCP' else 20.0,'window_s':10.0,
        'style':'Line + points' if technique=='EIS' else 'Line','grid':True,'legend':True,'average_samples':1,'show_raw':False,
        'hidden_runs':[],'hidden_overlays':[]},'time_view':None,'phase_view':None,'navigating':False}
    if session.plot_settings:
        plot_state['settings']=validate_plot_settings({**plot_state['settings'],**session.plot_settings},technique)
    fig._ec_plot_state=plot_state
    def edit_plot(event=None):
        if 'qt' not in plt.get_backend().lower():
            set_status('Plot settings dialog requires QtAgg; run the experiment launcher.');return
        initial=dict(plot_state['settings'])
        if initial['x_mode']=='Auto':initial['x_min'],initial['x_max']=ax.get_xlim()
        if initial['y_mode']=='Auto':initial['y_min'],initial['y_max']=ax.get_ylim()
        selected=plot_settings_dialog(getattr(fig.canvas.manager,'window',None),technique,initial)
        if selected:
            plot_state['settings']=selected;draw()
            set_status('Plot settings applied'+(' · display held' if plot_state['held'] else ''))
    def overview(event=None):
        plot_state['settings'].update(x_mode='Auto' if technique in {'CV','LSV'} else 'From zero',y_mode='Include zero')
        plot_state['time_view']=None;plot_state['phase_view']=None;plot_state['held']=False;hold_button.label.set_text('Hold plot');draw();set_status('Overview — includes zero; raw samples retained')
    def auto_axes(event=None):
        plot_state['settings'].update(x_mode='Auto',y_mode='Auto',average_samples=1);plot_state['held']=False
        plot_state['time_view']=None;plot_state['phase_view']=None;hold_button.label.set_text('Hold plot');draw();set_status('Detail view — raw samples; automatic axes')
    def hold_plot(event=None):
        plot_state['held']=not plot_state['held']
        hold_button.label.set_text('Resume plot' if plot_state['held'] else 'Hold plot')
        if not plot_state['held']:draw()
        set_status('Display held; acquisition continues' if plot_state['held'] else 'Live display resumed')
    def manage_runs(event=None):
        if 'qt' not in plt.get_backend().lower():
            set_status('Run controls require QtAgg; use the normal dashboard launcher.');return
        from collections import Counter
        counts=Counter(r['run'] for r in session.records)
        selected=run_visibility_dialog(getattr(fig.canvas.manager,'window',None),sorted(counts.items()),
            [name for name,_ in overlays],plot_state['settings']['hidden_runs'],plot_state['settings']['hidden_overlays'])
        if selected is not None:
            plot_state['settings'].update(hidden_runs=selected[0],hidden_overlays=selected[1])
            plot_state['held']=False;hold_button.label.set_text('Hold plot');draw()
            set_status('Plot selection applied; all raw samples retained')
    for label,callback,x in [('Plot settings',edit_plot,.075),('Overview',overview,.195),('Detail view',auto_axes,.315),('Hold plot',hold_plot,.435),('Runs',manage_runs,.555)]:
        button=Button(fig.add_axes([x,.86,.105,.032]),label,color='#dceaf3',hovercolor='#c3d9ec')
        button.label.set_fontsize(9);button.on_clicked(callback);widgets.append(button)
        if label=='Hold plot':hold_button=button

    def draw():
        if not plot_state['held'] and not plot_state['navigating']:
            ax.clear()
            opts=plot_state['settings']
            ax.set_facecolor('#ffffff')
            ax.set_prop_cycle(color=[accent]+[c for c in PALETTE if c!=accent])
            trace={'linewidth':1.5,'marker':'.' if opts['style']!='Line' else None,
                   'linestyle':'None' if opts['style']=='Points' else '-','markersize':4}
            if time_ax:time_ax.clear()
            if phase_ax:phase_ax.clear()
            if technique=='EIS':
                ax.set_xlabel("Z′ (Ω)");ax.set_ylabel("−Z″ (Ω)")
                time_ax.set_xlabel('');time_ax.tick_params(labelbottom=False);time_ax.set_ylabel('|Z| (Ω)');time_ax.set_xscale('log');time_ax.set_yscale('log')
                phase_ax.set_xlabel('Frequency (Hz)');phase_ax.set_ylabel('Phase (°)');phase_ax.set_xscale('log')
            elif technique in {'CV','LSV'}:
                ax.set_xlabel('WE − RE potential (V)');ax.set_ylabel('Current (µA)')
                time_ax.set_xlabel('Time within sweep (s)');time_ax.set_ylabel('Current (µA)')
            else:
                ax.set_xlabel('Time (s)');ax.set_ylabel(ocp_voltage_label(session.meta) if technique=='OCP' else 'Current (µA)')
            if technique == 'OCP':
                ax.ticklabel_format(axis='y',style='plain',useOffset=False)
            shown=[r for r in session.records if r['run'] not in opts['hidden_runs']]
            shown_overlays=[(name,rows) for i,(name,rows) in enumerate(overlays) if i not in opts['hidden_overlays']]
            groups={}
            for record in shown:groups.setdefault((record['run'],record['sweep']),[]).append(record)
            if technique=='EIS':
                traces=[(f'Run {run}, sweep {sweep+1}',rows,False) for (run,sweep),rows in groups.items()]
                traces += [(name,rows,True) for name,rows in shown_overlays]
                for number,(name,rows,is_overlay) in enumerate(traces):
                    color=([accent]+[c for c in PALETTE if c!=accent])[number%len(PALETTE)]
                    style={**trace,'color':color}
                    if is_overlay:style['linestyle']='--'
                    ax.plot([r['zreal_ohm'] for r in rows],[-r['zimag_ohm'] for r in rows],label=name,**style)
                    time_ax.plot([r['frequency_hz'] for r in rows],[r['magnitude_ohm'] for r in rows],**style)
                    phase_ax.plot([r['frequency_hz'] for r in rows],[r['phase_deg'] for r in rows],**style)
            else:
                for (run,sweep),rows in groups.items():
                    x=[r['potential_mV']/1000 if technique in {'CV','LSV'} else r['time_ms']/1000 for r in rows]
                    key='voltage_V' if technique=='OCP' else 'current_uA'
                    y=display_values(rows,key,opts['average_samples'])
                    if opts['show_raw'] and opts['average_samples']>1:
                        ax.plot(x,[r[key] for r in rows],color=accent,alpha=.25,linewidth=.7)
                    ax.plot(x,y,**trace,label=f'Run {run}, sweep {sweep+1}' if technique in {'CV','LSV'} else f'Run {run}'+(f' · {opts["average_samples"]}-sample average' if opts['average_samples']>1 else ''))
                    if time_ax:time_ax.plot([r['time_ms']/1000 for r in rows],y,**trace)
                for name,rows in shown_overlays:
                    x=[r['potential_mV']/1000 if technique in {'CV','LSV'} else r['time_ms']/1000 for r in rows]
                    y=display_values(rows,'voltage_V' if technique=='OCP' else 'current_uA',opts['average_samples'])
                    ax.plot(x,y,'--',label=name,alpha=.8)
            if technique == 'OCP' and (shown or shown_overlays):
                values=[r['voltage_V'] for r in shown] + [r['voltage_V'] for _,rows in shown_overlays for r in rows]
                low,high=min(values),max(values)
                span=max(high-low,float(minimum_voltage_span))
                center=(low+high)/2
                ax.set_ylim(center-span*.55,center+span*.55)
            if opts['legend'] and (groups or shown_overlays):ax.legend(fontsize=8,loc='best')
            ax.grid(opts['grid'],alpha=.2) if opts['grid'] else ax.grid(False)
            if time_ax:time_ax.grid(opts['grid'],alpha=.2) if opts['grid'] else time_ax.grid(False)
            latest=shown[-1]['time_ms']/1000 if shown else None
            try:duration=finite(controls['duration_s'].text) if 'duration_s' in controls else 0
            except ValueError:duration=0  # A partially edited field must not interrupt live plotting.
            limits=opts
            if technique=='EIS' and opts['x_mode']=='From zero':
                ax.set_xlim(*zero_limits([r['zreal_ohm'] for r in shown]+[r['zreal_ohm'] for _,rows in shown_overlays for r in rows],10))
                limits={**opts,'x_mode':'Auto'}
            apply_plot_limits(ax,limits,latest,duration_s=duration,minimum_y_span=.5 if technique=='OCP' else 10)
            if technique=='EIS':
                # Nyquist axes share impedance units; preserve geometric scale.
                if opts['y_mode']=='Include zero':
                    box=ax.get_position();ratio=(box.height*fig.get_figheight())/(box.width*fig.get_figwidth())
                    xmin,xmax=ax.get_xlim();ymin,ymax=ax.get_ylim();span=max(ymax-ymin,(xmax-xmin)*ratio)
                    if ymin>=0:ax.set_ylim(0,span)
                    elif ymax<=0:ax.set_ylim(-span,0)
                    else:ax.set_ylim((ymin+ymax-span)/2,(ymin+ymax+span)/2)
                ax.set_aspect('equal',adjustable='box')
            if opts['y_mode']=='Include zero':ax.axhline(0,color='#6b8097',linewidth=.8,alpha=.5)
            if phase_ax:phase_ax.grid(opts['grid'],alpha=.2)
            if technique=='EIS':
                if plot_state['time_view']:
                    time_ax.set_xlim(*plot_state['time_view'][0]);time_ax.set_ylim(*plot_state['time_view'][1])
                if plot_state['phase_view']:
                    phase_ax.set_xlim(*plot_state['phase_view'][0]);phase_ax.set_ylim(*plot_state['phase_view'][1])
            elif time_ax:
                time_ax.set_xlim(left=0)
                if plot_state['time_view']:
                    time_ax.set_xlim(*plot_state['time_view'][0]);time_ax.set_ylim(*plot_state['time_view'][1])
            subtitle.set_text('Portable potentiostat · '+('display average: '+str(opts['average_samples'])+' samples' if opts['average_samples']>1 else 'raw trace')+' · all raw samples retained')
        visible_runs={r['run'] for r in session.records if r['run'] not in plot_state['settings']['hidden_runs']}
        lines=[f'{len(session.records)} raw samples · {len(visible_runs)} runs shown',f'{len(session.warnings)} warnings',
               f'UART {args.baud} baud · {session.state}']
        frequency=recorded_frequency(session.meta,technique)
        if frequency is not None:lines.append(f'{"Impedance points" if technique=="EIS" else "Sampling frequency"} {frequency:.4g} Hz')
        if technique=='EIS' and session.records:
            r=session.records[-1];lines.append(f'Latest {r["frequency_hz"]:.5g} Hz')
            lines.append(f'|Z| {r["magnitude_ohm"]:.5g} Ω · phase {r["phase_deg"]:.4g}°')
        if technique == 'OCP' and session.records:
            values=[r['voltage_V'] for r in session.records]
            lines.append(f'Latest {values[-1]:.6f} V')
            lines.append(f'Peak-to-peak {(max(values)-min(values))*1e6:.1f} µV')
            if 'ADC_PGA_ENUM' in session.meta:
                gain=[1,1.5,2,4,9][int(session.meta['ADC_PGA_ENUM'])]
                step=float(session.meta.get('ADC_REFERENCE_V',1.82))*1.835/1.82/32768/gain*1e6
                lines.append(f'ADC code step ≈ {step:.1f} µV · gain {gain:g}')
        info.set_text('\n'.join(lines))
        fig.canvas.draw_idle()

    def navigation_press(event):
        mode=str(getattr(getattr(fig.canvas.manager,'toolbar',None),'mode','')).lower()
        if event.inaxes is not None and event.inaxes in [ax,time_ax,phase_ax] and any(word in mode for word in ('zoom','pan')):
            plot_state['navigating']=True;plot_state['navigation_axis']=event.inaxes
    def navigation_release(event):
        if not plot_state['navigating']:return
        plot_state['navigating']=False
        if plot_state.get('navigation_axis') is phase_ax and phase_ax is not None:
            plot_state['phase_view']=(phase_ax.get_xlim(),phase_ax.get_ylim())
        elif plot_state.get('navigation_axis') is time_ax and time_ax is not None:
            plot_state['time_view']=(time_ax.get_xlim(),time_ax.get_ylim())
        else:
            xlo,xhi=ax.get_xlim();ylo,yhi=ax.get_ylim()
            plot_state['settings'].update(x_mode='Fixed',x_min=xlo,x_max=xhi,y_mode='Fixed',y_min=ylo,y_max=yhi)
        set_status('Zoom/pan retained during acquisition; Overview resets the view')
    fig.canvas.mpl_connect('button_press_event',navigation_press)
    fig.canvas.mpl_connect('button_release_event',navigation_release)

    def tick(frame):
        nonlocal buffer,connection,changed,awaiting_start
        nonlocal received_bytes,raw_lines,metadata_timeout_reported
        if connection:
            try:
                incoming=connection.read(min(connection.in_waiting,65536))
                received_bytes+=len(incoming)
                buffer+=incoming
                if len(buffer)>262144: raise ValueError('UART buffer exceeded limit; malformed stream')
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1)
                    text=line.decode('ascii',errors='strict').strip()
                    if text and raw_lines < 8:
                        print('[UART] ' + text)
                        raw_lines+=1
                    try:session.feed(text)
                    except (ValueError,UnicodeError) as exc:session.warnings.append(str(exc))
                    if '_STATE,' in text or '_RUN_BEGIN' in text or '_ERROR,' in text:awaiting_start=False
                    if '_STATE,' in text:
                        set_status(f'{session.state} — {technique}; {len(session.warnings)} warnings')
                    if '_ERROR,' in text:set_status(text)
                    if text.startswith('# CA_TRANSFER_BEGIN,'):set_status('Acquisition complete — receiving buffered samples')
                    changed=True
                if time.monotonic()-connect_time>10 and not session.compatible and not metadata_timeout_reported:
                    metadata_timeout_reported=True
                    if received_bytes == 0:
                        set_status('Port open, but no UART bytes received. Check MCU is running, power, and UART wiring; then Connect again.')
                    else:
                        schema=session.meta.get('SCHEMA', 'not received')
                        set_status(f'UART received {received_bytes} bytes, but {technique} schema is {schema}. Check flashed image and baud; see [UART] lines.')
            except (serial.SerialException,OSError,ValueError,UnicodeError) as exc:
                connection.close();connection=None;session.warnings.append('Connection lost: '+str(exc));set_status('Connection lost: '+str(exc))
        if changed:draw();changed=False
        return []

    def close(event):
        if connection:
            try:connection.write(b'X\n');connection.flush()
            except (serial.SerialException,OSError):pass
            connection.close()
    fig.canvas.mpl_connect('close_event',close)
    animation=FuncAnimation(fig,tick,interval=150,cache_frame_data=False)
    fig._ec_keepalive=(animation,widgets)
    draw()
    if args.session: set_status("Reviewing "+Path(args.session).name)
    if auto_connect and not args.offline and not args.session:connect()
    plt.show()

if __name__=='__main__': main('CV')
