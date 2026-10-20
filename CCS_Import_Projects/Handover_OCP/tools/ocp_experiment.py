"""OCP dashboard: edit these settings, then press Run in Spyder.

ADC input selection is in Handover_OCP/main.c and requires rebuild/reflash.
Close any previous dashboard before rerunning to release the serial port.
"""

# ============================================================================
# SERIAL CONNECTION -- EDIT HERE
# ============================================================================
SERIAL_PORT = "/dev/cu.usbmodem103"  # MSP-FET Application UART; recheck after USB reconnect
BAUD_RATE = 9600                     # Matches the handover OCP firmware
# SERIAL_PORT = "COM5"               # Windows example
# SERIAL_PORT = None                 # Automatic only when one USB serial port exists

# ============================================================================
# EXPERIMENT SETTINGS -- ALSO EDITABLE IN THE DASHBOARD WINDOW
# ============================================================================
DURATION_SECONDS = 60                # Set 0 to record continuously until Stop
SAMPLE_RATE_HZ = 10                   # Recorded sampling frequency: 0.1 to 20 Hz

# ============================================================================
# DISPLAY AND STARTUP
# ============================================================================
PLOT_BACKEND = "QtAgg"                # Interactive window in Spyder; TkAgg is an alternative
OFFLINE = False                      # True opens the window without connecting
MIN_VOLTAGE_SPAN_MV = 2.0             # Minimum plotted span; raw samples are unchanged
AUTO_CONNECT = True                  # Connect on opening; press Start to acquire

# ============================================================================
# LAUNCH -- NORMALLY LEAVE THE CODE BELOW UNCHANGED
# ============================================================================
def run(argv=None):
    import importlib.util
    import sys
    from pathlib import Path
    import matplotlib

    if PLOT_BACKEND:
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

    # Load the dashboard beside this file, avoiding another copy cached in Spyder.
    dashboard_path = Path(__file__).resolve().with_name("ec_dashboard.py")
    spec = importlib.util.spec_from_file_location("handover_ocp_dashboard", dashboard_path)
    dashboard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dashboard)
    if argv is None:
        argv = [] if "ipykernel" in sys.modules else sys.argv[1:]
    return dashboard.main(
        "OCP", argv=argv, default_port=SERIAL_PORT, default_baud=BAUD_RATE,
        default_parameters={"duration_s": DURATION_SECONDS, "rate_hz": SAMPLE_RATE_HZ},
        offline=OFFLINE, auto_connect=AUTO_CONNECT,
        minimum_voltage_span=MIN_VOLTAGE_SPAN_MV / 1000.0,
    )


if __name__ == "__main__":
    run()
