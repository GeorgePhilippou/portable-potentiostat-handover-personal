"""AMP live dashboard: edit the settings below, then press Run in Spyder.

The window lets you change experiment settings before pressing Start.
ADC inputs/gain remain in this project's main.c and need rebuild/reflash.
Close the previous dashboard before rerunning, so it releases the serial port.
"""

# ============================================================================
# SERIAL CONNECTION CONFIGURATION
# ============================================================================
# Current Mac MSP-FET Application UART (1101 is its debug interface).
# Check the printed port list after reconnecting USB: port names may change.
# Windows example: SERIAL_PORT = "COM5"
# Use None for automatic selection when exactly one USB serial port is present.
SERIAL_PORT = "/dev/cu.usbmodem1103"
BAUD_RATE = 9600  # Must match this technique's firmware

# ============================================================================
# EXPERIMENT SETTINGS -- these initialise the editable dashboard fields
# ============================================================================
EXPERIMENT_SETTINGS = {
    "potential_mV": 400,  # WE minus RE, millivolts
    "duration_s": 60,  # Seconds; OCP/AMP: 0 means continuous until Stop
    "rate_hz": 10,  # Reported readings per second
}

# ============================================================================
# DISPLAY AND STARTUP
# ============================================================================
PLOT_BACKEND = "QtAgg"  # Separate interactive window in Spyder; TkAgg is an alternative
OFFLINE = False        # True opens the dashboard without attempting a connection
AUTO_CONNECT = True    # Connect on opening; acquisition still waits for Start

# ============================================================================
# LAUNCH DASHBOARD -- normally leave this section unchanged
# ============================================================================
def run(argv=None):
    import importlib.util
    import sys
    from pathlib import Path
    import matplotlib

    if PLOT_BACKEND:
        # Spyder/IPython also needs its GUI event loop enabled for live plots.
        try:
            from IPython import get_ipython
            shell = get_ipython()
        except ImportError:
            shell = None
        gui = {"QtAgg": "qt", "TkAgg": "tk"}.get(PLOT_BACKEND)
        if shell is not None and gui:
            shell.run_line_magic("matplotlib", gui)
        else:
            matplotlib.use(PLOT_BACKEND, force=True)

    # Load the dashboard beside THIS file, even if another technique was run
    # previously in the same Spyder console.
    dashboard_path = Path(__file__).resolve().with_name("ec_dashboard.py")
    spec = importlib.util.spec_from_file_location("handover_amp_dashboard", dashboard_path)
    dashboard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dashboard)
    if argv is None:
        argv = [] if "ipykernel" in sys.modules else sys.argv[1:]
    return dashboard.main(
        "AMP", argv=argv, default_port=SERIAL_PORT, default_baud=BAUD_RATE,
        default_parameters=EXPERIMENT_SETTINGS, offline=OFFLINE,
        auto_connect=AUTO_CONNECT,
    )


if __name__ == "__main__":
    run()
