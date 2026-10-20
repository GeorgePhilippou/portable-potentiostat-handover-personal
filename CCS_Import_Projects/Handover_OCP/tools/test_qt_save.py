import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import ec_dashboard as ec

try:
    from matplotlib.backends.qt_compat import QtWidgets
except ImportError:
    QtWidgets = None


@unittest.skipIf(QtWidgets is None, 'Qt bindings unavailable in this test environment')
class QtSessionSaving(unittest.TestCase):
    def tearDown(self):
        plt.close('all')

    def test_save_uses_qt_dialog_and_preserves_csv_and_plot(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            session=ec.Session('OCP')
            for line in ['# OCP_META_SCHEMA,MSP430_OCP_UART_V1', '# OCP_RUN_BEGIN', 'OCP,0,100,1.11']:
                session.feed(line, arrival=1)
            initial=root/'initial.csv';ec.save_session(initial,session)
            target=root/'saved'
            with patch.object(plt,'show'), patch('serial.tools.list_ports.comports', return_value=[]):
                ec.main('OCP',argv=['--session',str(initial)])
                figure=plt.gcf();_,widgets=figure._ec_keepalive
                button=next(w for w in widgets if getattr(getattr(w,'label',None),'get_text',lambda:'')()=='Save session')
                with patch.object(plt,'get_backend', return_value='QtAgg'), \
                     patch.object(QtWidgets.QFileDialog,'getSaveFileName', return_value=(str(target),'CSV files (*.csv)')) as dialog, \
                     patch('tkinter.Tk') as tk_root:
                    button._observers.process('clicked',None)
                    dialog.assert_called_once();tk_root.assert_not_called()
            self.assertTrue(target.with_suffix('.png').exists())
            self.assertEqual(ec.load_session(target.with_suffix('.csv'),'OCP').records[0]['voltage_V'],1.11)
