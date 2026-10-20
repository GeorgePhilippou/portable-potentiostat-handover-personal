import unittest
from types import SimpleNamespace
from unittest.mock import patch
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import potentiostat_dashboard as launcher
import ec_dashboard as ec

class Probe:
    def __init__(self,port,baud,reply,**kw):
        self.port=port;self.baud=baud;self.reply=reply;self.writes=[];self.closed=False
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def reset_input_buffer(self):pass
    def flush(self):pass
    @property
    def in_waiting(self):return len(self.reply)
    def write(self,b):self.writes.append(b)
    def read(self,n):result=self.reply[:n];self.reply=self.reply[n:];return result

class Detection(unittest.TestCase):
    def test_each_schema_and_baud_without_start(self):
        for schema,tech in launcher.SCHEMA_TECHNIQUES.items():
            links=[]
            def factory(port,baud,**kw):
                reply=(f'# {tech}_META_SCHEMA,{schema}\n\r# {tech}_STATE,IDLE\n').encode() if baud==115200 else b'garbage\n'
                link=Probe(port,baud,reply,**kw);links.append(link);return link
            result=launcher.identify_firmware('COM5',serial_factory=factory,timeout_s=.01)
            self.assertEqual(result['technique'],tech);self.assertEqual(result['baud'],115200)
            self.assertTrue(all(x.closed and x.writes==[b'M\n'] for x in links))
    def test_unknown_schema_and_busy_port_fail_closed(self):
        link=Probe('COM5',9600,b'# OCP_META_SCHEMA,WRONG\n# OCP_STATE,IDLE\n')
        with self.assertRaises(ValueError):launcher.identify_firmware('COM5',serial_factory=lambda *a,**k:link,baud_rates=[9600],timeout_s=.01)
        self.assertTrue(link.closed)
        with self.assertRaisesRegex(ValueError,'busy'):
            launcher.identify_firmware('COM5',serial_factory=lambda *a,**k:(_ for _ in ()).throw(OSError('busy')),baud_rates=[9600])
    def test_automatic_search_excludes_unrelated_devices(self):
        ports=[SimpleNamespace(device='MSP',description='MSP Tools Driver'),SimpleNamespace(device='BT',description='Bluetooth')]
        self.assertEqual(launcher.msp_ports(ports),['MSP'])
    def test_fragmented_metadata(self):
        link=Probe('COM5',9600,b'# OCP_META_SCHEMA,MSP430_OCP_UART_V1\n# OCP_META_BUILD_ID,R6\n# OCP_STATE,IDLE\n')
        read=link.read;link.read=lambda n:read(min(n,3))
        result=launcher.identify_firmware('COM5',serial_factory=lambda *a,**k:link,baud_rates=[9600],timeout_s=.1)
        self.assertEqual(result['metadata']['BUILD_ID'],'R6')

class PlotControls(unittest.TestCase):
    def tearDown(self):plt.close('all')
    def open(self):
        with patch.object(plt,'show'),patch('serial.tools.list_ports.comports',return_value=[]):ec.main('OCP',argv=['--offline'])
        fig=plt.gcf();widgets=fig._ec_keepalive[1]
        return fig,{w.label.get_text():w for w in widgets if hasattr(w,'label')}
    def click(self,buttons,name):buttons[name]._observers.process('clicked',None)
    def test_fixed_axes_points_grid_and_auto_reset(self):
        fig,buttons=self.open();state=fig._ec_plot_state
        opts={**state['settings'],'x_mode':'Fixed','x_min':2,'x_max':12,'y_mode':'Fixed','y_min':-.2,'y_max':.2,'style':'Points','grid':False,'legend':False}
        with patch.object(plt,'get_backend',return_value='QtAgg'),patch.object(ec,'plot_settings_dialog',return_value=ec.validate_plot_settings(opts,'OCP')):
            self.click(buttons,'Plot settings')
        self.assertEqual(fig.axes[0].get_xlim(),(2,12));self.assertEqual(fig.axes[0].get_ylim(),(-.2,.2))
        self.click(buttons,'Hold plot');self.assertTrue(state['held'])
        self.click(buttons,'Detail view');self.assertFalse(state['held']);self.assertEqual(state['settings']['x_mode'],'Auto')
    def test_rolling_window_and_invalid_limits(self):
        fig,_=self.open();opts={**fig._ec_plot_state['settings'],'x_mode':'Latest window','window_s':5}
        ec.apply_plot_limits(fig.axes[0],ec.validate_plot_settings(opts,'OCP'),20)
        self.assertEqual(fig.axes[0].get_xlim(),(15,20))
        with self.assertRaises(ValueError):ec.validate_plot_settings({**opts,'x_mode':'Fixed','x_min':2,'x_max':1},'OCP')
        with self.assertRaises(ValueError):ec.validate_plot_settings(opts,'CV')

    def test_display_hold_preserves_raw_samples_and_points_style(self):
        session=ec.Session('OCP');session.feed('# OCP_RUN_BEGIN');session.feed('OCP,0,100,-.137255,35219')
        with patch.object(plt,'show'),patch('serial.tools.list_ports.comports',return_value=[]),patch.object(ec,'Session',return_value=session):
            ec.main('OCP',argv=['--offline'])
        fig=plt.gcf();buttons={w.label.get_text():w for w in fig._ec_keepalive[1] if hasattr(w,'label')}
        self.click(buttons,'Hold plot');session.feed('OCP,1,200,-.137199,35218')
        self.assertEqual(len(session.records),2);self.assertEqual(len(fig.axes[0].lines[0].get_xdata()),1)
        self.click(buttons,'Hold plot');self.assertEqual(len(fig.axes[0].lines[0].get_xdata()),2)
        opts={**fig._ec_plot_state['settings'],'style':'Points'}
        with patch.object(plt,'get_backend',return_value='QtAgg'),patch.object(ec,'plot_settings_dialog',return_value=opts):
            self.click(buttons,'Plot settings')
        self.assertEqual(fig.axes[0].lines[0].get_linestyle(),'None');self.assertEqual(session.records[1]['adc_code'],35218)

    def test_overview_default_includes_origin_and_detail_is_reversible(self):
        session=ec.Session('OCP');session.feed('OCP,0,100,-.137255,35219')
        with patch.object(plt,'show'),patch('serial.tools.list_ports.comports',return_value=[]),patch.object(ec,'Session',return_value=session):
            ec.main('OCP',argv=['--offline'])
        fig=plt.gcf();buttons={w.label.get_text():w for w in fig._ec_keepalive[1] if hasattr(w,'label')}
        self.assertEqual(fig.axes[0].get_xlim(),(0,60));self.assertEqual(fig.axes[0].get_ylim(),(-.5,0))
        self.click(buttons,'Detail view');self.assertLess(fig.axes[0].get_ylim()[1]-fig.axes[0].get_ylim()[0],.01)
        self.click(buttons,'Overview');self.assertEqual(fig.axes[0].get_ylim(),(-.5,0))
        self.assertEqual(session.records[0]['adc_code'],35219)
        for values in ([1.1,1.100056],[-.137255,-.137199],[-1,1],[0,0]):
            lo,hi=ec.zero_limits(values,.5)
            self.assertLessEqual(lo,min([0]+values));self.assertGreaterEqual(hi,max([0]+values));self.assertGreater(hi,lo)

    def test_display_average_resets_at_boundaries_and_preserves_source(self):
        rows=[{'run':1,'sweep':0,'voltage_V':v} for v in [0,2,4]]
        rows += [{'run':1,'sweep':1,'voltage_V':10},{'run':2,'sweep':0,'voltage_V':20}]
        before=[dict(r) for r in rows]
        self.assertEqual(ec.display_values(rows,'voltage_V',2),[0,1,3,10,20])
        self.assertEqual(rows,before)
        self.assertEqual(ec.display_values(rows,'voltage_V',1),[0,2,4,10,20])
        with self.assertRaises(ValueError):ec.display_values(rows,'voltage_V',0)

    def test_saved_plot_preferences_and_raw_records_roundtrip(self):
        import tempfile
        from pathlib import Path
        session=ec.Session('OCP');session.feed('OCP,0,100,-.137255,35219')
        fig,_=self.open();session.plot_settings={**fig._ec_plot_state['settings'],'average_samples':5,'show_raw':True}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'record.csv';ec.save_session(path,session);loaded=ec.load_session(path,'OCP')
            self.assertEqual(loaded.plot_settings,session.plot_settings);self.assertEqual(loaded.records,session.records)
            with patch.object(plt,'show'),patch('serial.tools.list_ports.comports',return_value=[]):
                ec.main('OCP',argv=['--session',str(path)])
            self.assertEqual(plt.gcf()._ec_plot_state['settings']['average_samples'],5)

class ChronoDashboard(unittest.TestCase):
    def test_chrono_measurements_and_controls_without_demo_fitting(self):
        import tempfile
        from pathlib import Path
        session=ec.Session('CA')
        session.feed('# CA_RUN_BEGIN')
        session.feed('CA,0,100,14.7')
        with patch.object(plt,'show'),patch('serial.tools.list_ports.comports',return_value=[]),patch.object(ec,'Session',return_value=session):
            ec.main('CA',argv=['--offline'])
        fig=plt.gcf()
        labels={w.label.get_text() for w in fig._ec_keepalive[1] if hasattr(w,'label')}
        self.assertTrue({'Start','Stop','Save session','Runs','Overlay','Plot settings'}.issubset(labels))
        self.assertNotIn('RC fit',labels)
        traces=[line for line in fig.axes[0].lines if line.get_label()=='Run 1']
        self.assertEqual(len(traces),1)
        self.assertEqual(list(traces[0].get_ydata()),[14.7])
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'chrono.csv'
            ec.save_session(path,session)
            self.assertEqual(ec.load_session(path,'CA').records,session.records)
        plt.close(fig)


class RunManagement(unittest.TestCase):
    def tearDown(self):plt.close('all')
    def test_hide_one_clear_all_and_restore_preserves_raw_records(self):
        import copy
        session=ec.Session('OCP')
        session.feed('# OCP_RUN_BEGIN');session.feed('OCP,0,100,1.1,35000')
        session.feed('# OCP_RUN_BEGIN');session.feed('OCP,0,100,1.2,35001')
        before=copy.deepcopy(session.records)
        with patch.object(plt,'show'),patch('serial.tools.list_ports.comports',return_value=[]),patch.object(ec,'Session',return_value=session):ec.main('OCP',argv=['--offline'])
        fig=plt.gcf();buttons={w.label.get_text():w for w in fig._ec_keepalive[1] if hasattr(w,'label')}
        for hidden,expected in [([1],['Run 2']),([1,2],[]),([],['Run 1','Run 2'])]:
            with patch.object(plt,'get_backend',return_value='QtAgg'),patch.object(ec,'run_visibility_dialog',return_value=(hidden,[])):
                buttons['Runs']._observers.process('clicked',None)
            self.assertEqual([line.get_label() for line in fig.axes[0].lines if line.get_label().startswith('Run')],expected)
            self.assertEqual(session.records,before)
    def test_live_redraw_preserves_zoom_and_overview_resets_it(self):
        from matplotlib.backend_bases import MouseEvent
        session=ec.Session('OCP');session.feed('OCP,0,100,1.1,35000')
        with patch.object(plt,'show'),patch('serial.tools.list_ports.comports',return_value=[]),patch.object(ec,'Session',return_value=session):ec.main('OCP',argv=['--offline'])
        fig=plt.gcf();ax=fig.axes[0];toolbar=SimpleNamespace(mode='zoom rect');fig.canvas.manager.toolbar=toolbar
        x,y=ax.transData.transform((1,1.1));event=MouseEvent('button_press_event',fig.canvas,x,y,button=1)
        fig.canvas.callbacks.process('button_press_event',event)
        self.assertTrue(fig._ec_plot_state['navigating'])
        ax.set_xlim(1,4);ax.set_ylim(1.09,1.11)
        release=MouseEvent('button_release_event',fig.canvas,x,y,button=1)
        with patch.object(fig.canvas,'draw_idle'):
            fig.canvas.callbacks.process('button_release_event',release)
            session.feed('OCP,1,200,1.1001,35001');fig._ec_keepalive[0]._func(0)
        self.assertFalse(fig._ec_plot_state['navigating']);self.assertEqual(ax.get_xlim(),(1,4));self.assertEqual(ax.get_ylim(),(1.09,1.11))
        fig.canvas.manager.toolbar=None
        buttons={w.label.get_text():w for w in fig._ec_keepalive[1] if hasattr(w,'label')};buttons['Overview']._observers.process('clicked',None)
        self.assertEqual(ax.get_xlim(),(0,60));self.assertGreater(ax.get_ylim()[1]-ax.get_ylim()[0],.1)

class RecordedFrequency(unittest.TestCase):
    def test_rates_come_from_firmware_measurement_schedule(self):
        self.assertEqual(ec.recorded_frequency({'SAMPLE_PERIOD_MS':'100'},'OCP'),10)
        self.assertEqual(ec.recorded_frequency({'SAMPLE_PERIOD_MS':'50'},'AMP'),20)
        self.assertAlmostEqual(ec.recorded_frequency({'SAMPLE_RATE_HZ':'150.0375'},'CA'),150.0375)
        for mode in ['CV','LSV']:
            self.assertEqual(ec.recorded_frequency({'STEPS':'80','DURATION_MS':'8000'},mode),10)
            for meta in [{},{'STEPS':'80','DURATION_MS':'0'},{'STEPS':'NaN','DURATION_MS':'8000'}]:
                self.assertIsNone(ec.recorded_frequency(meta,mode))
