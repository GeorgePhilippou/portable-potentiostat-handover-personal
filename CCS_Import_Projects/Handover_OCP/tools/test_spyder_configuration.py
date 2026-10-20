import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import ec_dashboard as ec


class SpyderConfiguration(unittest.TestCase):
    def tearDown(self):
        plt.close('all')

    def test_script_defaults_are_validated_without_changing_other_techniques(self):
        values = ec.parameter_defaults('OCP', {'duration_s': 12, 'rate_hz': 5})
        self.assertEqual(ec.build_start('OCP', values), b'S,12000,200\n')
        self.assertEqual(ec.parameter_defaults('OCP')['duration_s'], 60)
        with self.assertRaises(ValueError):
            ec.parameter_defaults('OCP', {'rate_hz': 21})

    def test_window_uses_script_settings_and_connect_uses_the_edited_port(self):
        with patch('serial.tools.list_ports.comports', return_value=[]), \
             patch('serial.Serial') as serial_open, patch.object(plt, 'show'):
            ec.main('OCP', argv=['--offline'], default_port='/dev/cu.usbmodem1103',
                    default_baud=9600, default_parameters={'duration_s': 12, 'rate_hz': 5})
            figure = plt.gcf()
            _, widgets = figure._ec_keepalive
            self.assertEqual(widgets[0].text, '12')
            self.assertEqual(widgets[1].text, '5')
            self.assertEqual(widgets[2].text, '/dev/cu.usbmodem1103')
            serial_open.assert_not_called()
            widgets[2].set_val('/dev/cu.usbmodem9993')
            connect = next(w for w in widgets if getattr(getattr(w, 'label', None), 'get_text', lambda: '')() == 'Connect')
            connect._observers.process('clicked', None)
            self.assertEqual(serial_open.call_args.args[:2], ('/dev/cu.usbmodem9993', 9600))
            serial_open.return_value.write.assert_called_once_with(b'M\n')

    def test_silent_uart_timeout_is_reported_once(self):
        with patch('serial.tools.list_ports.comports', return_value=[]), \
             patch('serial.Serial') as serial_open, patch.object(plt, 'show'), \
             patch.object(ec.time, 'monotonic', return_value=10), patch('builtins.print') as output:
            serial_open.return_value.in_waiting = 0
            serial_open.return_value.read.return_value = b''
            ec.main('OCP', argv=[], default_port='/dev/cu.usbmodem103')
            figure = plt.gcf()
            animation, _ = figure._ec_keepalive
            with patch.object(ec.time, 'monotonic', return_value=21):
                animation._func(0)
                animation._func(1)
            reports = [call for call in output.call_args_list if 'no UART bytes' in str(call)]
            self.assertEqual(len(reports), 1)
