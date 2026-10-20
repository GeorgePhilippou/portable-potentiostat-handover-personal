"""Run this file in Spyder for every handover technique.

The launcher asks the firmware for metadata, identifies the technique and baud,
then opens the matching controls. Detection never starts an experiment.
Close other dashboards and CCS serial terminals before running this file.
CV and LSV use the same firmware; choose the desired mode in the launcher.
"""
# ============================= EDIT HERE =============================
SERIAL_PORT = None          # None searches MSP-FET ports; or '/dev/cu.usbmodem103' / 'COM5'
PLOT_BACKEND = 'QtAgg'
AUTO_DETECT = True
CV_OR_LSV = 'CV'            # Desired mode when the shared CV/LSV firmware is detected
MIN_VOLTAGE_SPAN_MV = 2.0  # Minimum span for OCP Detail view; Overview includes zero
# ====================================================================
from pathlib import Path
import importlib.util
import re
import time

SCHEMA_TECHNIQUES = {
    'MSP430_OCP_UART_V1':'OCP', 'MSP430_CA_UART_V1':'CA',
    'MSP430_AMP_UART_V4':'AMP', 'MSP430_CV_UART_V1':'CV', 'MSP430_EIS_UART_V2':'EIS',
}


def msp_ports(ports):
    """Automatic probing is limited to recognised TI/MSP interfaces."""
    candidates=[p.device for p in ports if ('msp' in str(p.description).lower()
            or 'texas instruments' in str(getattr(p,'manufacturer','')).lower())
            and 'debug' not in str(p.description).lower()]
    # Prefer the higher numbered MSP-FET UART interface; metadata remains the proof.
    return sorted(candidates,key=lambda name:int(re.search(r'(\d+)$',name).group(1)) if re.search(r'(\d+)$',name) else 0,reverse=True)


def identify_firmware(port, *, serial_factory=None, baud_rates=(9600,115200), timeout_s=3.0, progress=None, settle_s=.2):
    """Read a complete metadata reply at each supported baud, always closing probes."""
    if serial_factory is None:
        import serial
        serial_factory=serial.Serial
    errors=[]
    for baud in baud_rates:
        if progress:progress(f'Checking {port} at {baud} baud…')
        try:
            with serial_factory(port,baud,timeout=.1,write_timeout=1,exclusive=True) as link:
                time.sleep(settle_s)  # Let the MSP-FET apply its USB baud-rate command.
                link.reset_input_buffer();link.write(b'M\n');link.flush()
                deadline=time.monotonic()+timeout_s;buffer=b'';meta={};technique=None;received=0;examples=[];retry_at=time.monotonic()+.8
                while time.monotonic()<deadline:
                    if technique is None and time.monotonic()>=retry_at:
                        link.write(b'M\n');link.flush();retry_at=time.monotonic()+.8
                    chunk=link.read(min(max(getattr(link,'in_waiting',0),1),4096))
                    received+=len(chunk);buffer+=chunk
                    if len(buffer)>65536:raise ValueError('Malformed metadata: buffer limit exceeded')
                    while b'\n' in buffer:
                        raw,buffer=buffer.split(b'\n',1)
                        line=raw.decode('ascii',errors='replace').strip()
                        if line and len(examples)<3:examples.append(line[:160])
                        match=re.fullmatch(r'# (OCP|CA|AMP|CV|EIS)_META_([A-Z0-9_]+),(.+)',line)
                        if match:
                            prefix,key,value=match.groups();meta[key]=value
                            candidate=SCHEMA_TECHNIQUES.get(meta.get('SCHEMA'))
                            if candidate==prefix:technique=candidate
                        if technique and line.startswith('# '+technique+'_STATE,'):
                            return {'port':port,'baud':baud,'technique':technique,'metadata':meta}
                if technique:return {'port':port,'baud':baud,'technique':technique,'metadata':meta}
                errors.append(f'{baud} baud: '+('no UART bytes received' if received==0 else f'{received} bytes received, but metadata is damaged or unsupported: '+repr(examples)))
        except (OSError,ValueError) as exc:
            errors.append(f'{baud}: {exc}')
    raise ValueError(f'{port}: '+ '; '.join(errors))


def load_dashboard():
    path=Path(__file__).resolve().with_name('ec_dashboard.py')
    spec=importlib.util.spec_from_file_location('unified_potentiostat_engine',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def run():
    import matplotlib
    try:
        from IPython import get_ipython
        shell=get_ipython()
    except ImportError:shell=None
    if shell is not None:shell.run_line_magic('matplotlib','qt')
    else:matplotlib.use(PLOT_BACKEND,force=True)
    from matplotlib.backends.qt_compat import QtWidgets,QtCore
    import serial.tools.list_ports
    dashboard=load_dashboard()
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog=QtWidgets.QDialog();dialog.setWindowTitle('Portable potentiostat — connect')
    dialog.resize(680,420);dialog.setMinimumWidth(640)
    # Style only this dialog: avoid altering Spyder's application-wide style.
    dialog.setStyleSheet("""
        QDialog {background:#f3f6fa;}
        QLabel {color:#213850; font-size:13px;}
        QComboBox {background:white; color:#213850; border:1px solid #bdcbd9;
                   border-radius:5px; padding:6px 10px; min-height:22px;}
        QComboBox::drop-down {width:24px; border-left:1px solid #bdcbd9;}
        QPushButton {padding:9px 14px; color:#213850; background:#e0eaf4;
                     border:1px solid #b7cadb; border-radius:5px;}
        QPushButton:hover {background:#d0e2f1;}
        QPushButton:disabled {color:#7b8a99; background:#e9eef3; border-color:#d2dce5;}
        QPlainTextEdit {background:white; color:#364a60; border:1px solid #ccd8e3; padding:8px;}
    """)
    arrow=Path(__file__).resolve().parent/'dashboard_assets/chevron-down.svg'
    dialog.setStyleSheet(dialog.styleSheet()+f'QComboBox::down-arrow {{image:url("{arrow.as_posix()}");width:12px;height:8px;}}')
    layout=QtWidgets.QVBoxLayout(dialog);layout.setContentsMargins(24,24,24,24);layout.setSpacing(16)
    title=QtWidgets.QLabel('Portable potentiostat');title.setStyleSheet('font-size:23px;font-weight:600;color:#00897b;');layout.addWidget(title)
    description=QtWidgets.QLabel('Connect to the flashed firmware. The matching experiment opens automatically.')
    description.setWordWrap(True);layout.addWidget(description)
    form=QtWidgets.QFormLayout();form.setHorizontalSpacing(20);form.setVerticalSpacing(12)
    field_policy=getattr(QtWidgets.QFormLayout,'FieldGrowthPolicy',QtWidgets.QFormLayout)
    form.setFieldGrowthPolicy(field_policy.AllNonFixedFieldsGrow)
    align=getattr(QtCore.Qt,'AlignmentFlag',QtCore.Qt);form.setLabelAlignment(align.AlignLeft)
    layout.addLayout(form)
    port_box=QtWidgets.QComboBox();port_box.setEditable(True);form.addRow('Serial port',port_box)
    mode_box=QtWidgets.QComboBox();mode_box.addItems(['CV','LSV']);mode_box.setCurrentText(CV_OR_LSV);form.addRow('Voltammetry mode',mode_box)
    offline_box=QtWidgets.QComboBox();offline_box.addItems(['OCP','CA','AMP','CV','LSV','EIS']);form.addRow('Offline experiment',offline_box)
    for box in [port_box,mode_box,offline_box]:
        box.setMinimumWidth(300);box.setMinimumHeight(34)
        box.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding if hasattr(QtWidgets.QSizePolicy,'Policy') else QtWidgets.QSizePolicy.Expanding,
                          QtWidgets.QSizePolicy.Policy.Fixed if hasattr(QtWidgets.QSizePolicy,'Policy') else QtWidgets.QSizePolicy.Fixed)
    status=QtWidgets.QLabel('Detection reads metadata only. Press Start in the experiment window to acquire.')
    status.setWordWrap(True);status.setMinimumHeight(48);layout.addWidget(status)
    details=QtWidgets.QPlainTextEdit();details.setReadOnly(True);details.setFixedHeight(130);details.hide();layout.addWidget(details)
    detail_button=QtWidgets.QPushButton('Show connection details');detail_button.setVisible(False);layout.addWidget(detail_button)
    def toggle_details():
        details.setVisible(not details.isVisible());detail_button.setText('Hide connection details' if details.isVisible() else 'Show connection details')
        dialog.adjustSize()
    detail_button.clicked.connect(toggle_details)
    row=QtWidgets.QHBoxLayout();row.setSpacing(12);layout.addLayout(row)
    detect_button=QtWidgets.QPushButton('Detect and open');refresh_button=QtWidgets.QPushButton('Refresh ports');offline_button=QtWidgets.QPushButton('Open offline')
    for button in [detect_button,refresh_button,offline_button]:button.setMinimumHeight(38);row.addWidget(button)
    selection={}
    def refresh():
        current=port_box.currentText();ports=list(serial.tools.list_ports.comports())
        port_box.clear();port_box.addItem('Automatic (MSP-FET ports)')
        for p in ports:port_box.addItem(p.device)
        preferred=SERIAL_PORT or (current if current and not current.startswith('Automatic') else None)
        if preferred:port_box.setCurrentText(preferred)
    def progress(text):
        status.setText(text);app.processEvents()
    def detect():
        for b in [detect_button,refresh_button,offline_button]:b.setEnabled(False)
        try:
            details.clear();details.hide();detail_button.hide()
            selected=port_box.currentText().strip()
            candidates=msp_ports(serial.tools.list_ports.comports()) if selected.startswith('Automatic') else [selected]
            if not candidates:raise ValueError('No MSP-FET ports found. Refresh after connecting it, or enter the UART port manually.')
            failures=[]
            for port in candidates:
                try:
                    found=identify_firmware(port,progress=progress)
                    selection.update(found)
                    if found['technique']=='CV':
                        mode,accepted=QtWidgets.QInputDialog.getItem(dialog,'Voltammetry mode',
                            'CV and LSV share firmware. Which experiment would you like to run?',
                            ['CV','LSV'],mode_box.currentIndex(),False)
                        if not accepted:
                            selection.clear();status.setText('CV/LSV firmware detected. Choose a mode when opening the dashboard.');return
                        selection['technique']=mode
                    selection['offline']=False;dialog.accept();return
                except ValueError as exc:failures.append(str(exc))
            raise ValueError('\n'.join(failures))
        except ValueError as exc:
            status.setText('Connection could not identify the firmware. Check the selected UART port and close other serial readers. See details below.')
            details.setPlainText(str(exc));detail_button.show();print('[detection] '+str(exc))
        finally:
            for b in [detect_button,refresh_button,offline_button]:b.setEnabled(True)
    def offline():
        selection.update(technique=offline_box.currentText(),port=SERIAL_PORT,offline=True);dialog.accept()
    detect_button.clicked.connect(detect);refresh_button.clicked.connect(refresh);offline_button.clicked.connect(offline)
    refresh()
    if AUTO_DETECT:QtCore.QTimer.singleShot(0,detect)
    execute=getattr(dialog,'exec',None) or dialog.exec_
    if not execute():return
    technique=selection['technique'];print('[detection] '+str(selection))
    # A conservative step for the user's next 6.8 kohm / 10 uF RC test.
    parameters={'base_mV':0,'step_mV':100,'duration_s':1,'hold_ms':500} if technique=='CA' else None
    return dashboard.main(technique,argv=[],default_port=selection['port'],default_baud=selection.get('baud'),
                          default_parameters=parameters,offline=selection['offline'],
                          minimum_voltage_span=MIN_VOLTAGE_SPAN_MV/1000)


if __name__=='__main__':run()
