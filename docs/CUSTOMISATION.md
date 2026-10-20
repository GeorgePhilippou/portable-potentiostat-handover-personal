# Adapt this baseline to your experiment

This repository is an **editable starting point**. You can change the firmware, replace or redesign the dashboard, add controls and techniques, and change how data are processed, plotted and saved. The supplied settings demonstrate the original bench setup; they are not fixed requirements for every sensor or researcher. Source code is included for both the MSP430 and the computer.

Configuration still has physical limits: input range, reference/common-mode voltage, amplifier headroom, ADC/filter timing, available memory and serial bandwidth. Software changes do not expand the hardware's capabilities. Vendor components retain their original license terms; see [third-party notices](../THIRD_PARTY_NOTICES.md).

## Choose the level of change

| What you want to change | Where to change it | Reflash the MSP430? |
|---|---|---|
| Potential, duration, reporting frequency or sweep parameters already offered | Matching dashboard's experiment fields | No; Start sends them to the existing firmware |
| Zoom, limits, colours/styles offered by plot settings, displayed averaging or visible runs | Dashboard's plot toolbar, Plot settings and Runs controls | No |
| Default port, automatic detection, initial CV/LSV mode or plotting backend | `CCS_Import_Projects/potentiostat_dashboard.py`, top EDIT HERE block | No; restart Python |
| Default experiment values and control labels | `CCS_Import_Projects/ec_dashboard.py`, `FIELDS` | No, if the existing firmware already accepts those values |
| Dashboard layout, colours, widgets, plots or saved output | Shared `ec_dashboard.py`; see the map below | No, unless the change also needs a different wire protocol |
| ADC input, gain/reference, TIA, filters, switches or hardware routing | The technique's `main.c` configuration function | Yes; rebuild and flash |
| Acquisition sequence, buffering, sample timing or conversion | Technique implementation `.c`/`.h` and relevant `main.c` control functions | Yes; check conversion, timing and calibration together |
| New commands, new returned quantities, units or field order | Firmware command/record functions **and** Python command builder/parser | Usually both firmware and Python must change |
| Python libraries used by a custom dashboard | `requirements.txt`, plus imports in your Python code | No; install dependencies in the environment that runs it |

In the separate dashboard ZIP, edit `potentiostat_dashboard.py`, `ec_dashboard.py` and `requirements.txt` inside `Python_Dashboard/`. In the firmware ZIP, edit files inside the extracted `Handover_*` folder. The tables below also give paths from the full repository root for browsing the source on GitHub. In GitHub, use the file search to find a named function; in CCS/Python editors use Find. Symbol names are given instead of line numbers so the guide stays useful as code grows.

## Hardware references

Use the [supplied schematic, PCB layout and AN-1563 index](hardware/README.md) to understand the circuit and AD5940 measurement loops. The PDFs are reference exports; native KiCad project files are needed to edit the hardware design. Manufacturer examples are background, not a substitute for this board's wiring and software instructions.

## Firmware: where to edit each technique

All normal hardware configuration stays in the experiment's **own `main.c`**. You do not need a separate experiment configuration header.

| Technique | Project below `CCS_Import_Projects/` | Configuration entry point | Deeper acquisition implementation |
|---|---|---|---|
| OCP | `Handover_OCP` | `OCPUserConfig()` | `main.c`: calibration, control and filtered-voltage loop |
| Amperometry | `Handover_Amperometric` | `AD5940AMPStructInit()` and top-level `AMP_*` settings | `Amperometric/Amperometric.c` and `.h` |
| Chronoamperometry | `Handover_Chronoamperometric` | `AD5940AMPStructInit()` | `Chrono/ChronoAmperometric.c` and `.h`; buffering/control in `main.c` |
| CV and LSV | `Handover_CyclicVoltammetry` | `AD5940RampStructInit()` | `Voltammetry/` and `main.c` start/record functions |
| EIS — experimental | `Handover_EIS` | `AD5940ImpedanceStructInit()` and `EISBeginPoint()` | `Impedance/Impedance.c` and `.h` |

The configuration functions contain commented input/gain alternatives. Keep one active assignment for each ADC input. OCP currently selects internal reference inputs for a bench diagnostic; it must be reconfigured for electrode OCP. Its `voltage_output_sign` controls how ADC differential polarity is reported. [The wiring guide](HARDWARE.md) explains the corresponding physical header pins.

For current techniques, the ADC is reading the TIA path. Selecting an internal voltage instead does not still produce a meaningful current just because the dashboard labels the result in µA. If you change the measurement path, update conversion, calibration, metadata and axis units accordingly.

Reference units differ between applications: for example amperometry uses `ADCRefVolt` in volts, while the ramp application uses millivolts. Read the field's comment and its conversion function before changing it. Increased ADC/TIA gain trades input range for sensitivity; filter changes alter acquisition timing. Chrono's buffered rate is separate from the UART transfer rate.

Hardware support lives in each project's `MSP430_FR5969_Port.c` (legacy filename), `support/`, `ad5940_lib/` and `targetConfigs/`. Pin remapping or a board revision may require changes there as well as in `main.c` and the physical wiring.

Some values in `main.c` are startup defaults that **Start replaces with dashboard values**. Follow the technique's Start/control function to see what wins. To change a normal run's potential or duration, prefer the dashboard rather than editing a firmware default and expecting it to override Start.

## Dashboard: source map

The normal entry point is always **`CCS_Import_Projects/potentiostat_dashboard.py`**. It detects the firmware schema and loads the sibling **`ec_dashboard.py`**. There is no compiled/locked dashboard layer: those Python files are editable source.

| Change | Symbol or location |
|---|---|
| Connection dialog, port selection, detection behaviour and technique selection | `potentiostat_dashboard.py`: `identify_firmware()`, `SCHEMA_TECHNIQUES`, launcher UI |
| Experiment controls, default numbers and labels | `ec_dashboard.py`: `FIELDS` |
| Validate values, convert units, build Start messages | `build_start()` |
| Parse received metadata/state/records and retain runs | `Session`, especially `Session.feed()` |
| CSV columns and save/load behaviour | `COLUMNS`, `save_session()`, `load_session()`, `load_reference()` |
| Palette, technique colours and names | `PALETTE`, `ACCENTS`, `NAMES`, and UI styles inside `main()` |
| Main layout, widget positions, control callbacks and plots | `main()`; nested `draw()`, `start()`, `stop()`, `tick()` and other callbacks |
| Plot limits, smoothing and settings controls | `display_values()`, `validate_plot_settings()`, `apply_plot_limits()`, `plot_settings_dialog()` |
| Run visibility and overlays | `run_visibility_dialog()` and `manage_runs()` inside `main()` |

For example, to make amperometry open with **100 mV instead of 400 mV**, change the `potential_mV` default in `FIELDS['AMP']`, save, and restart Python. Start sends the chosen value to the existing AMP firmware; a firmware rebuild is not required for this supported runtime parameter. If you need a value outside the present accepted range, inspect both `build_start()` and the firmware's Start validation rather than changing only the displayed default.

You may replace the entire graphical interface with your own Python application. Preserve the documented command/record contract, or update both ends as described below. Plot changes should preserve the original samples separately from display smoothing if those samples are needed for analysis.

### Shared engine versus per-project tools

The `Handover_*/tools/` folders also contain earlier per-technique entry points and copies of the engine. They are not the normal researcher launch path. Editing the parent shared `ec_dashboard.py` changes the **unified launcher**; it does not automatically update those copies. If you choose to support the per-project entry points too, update their engines deliberately and run their tests. Avoid editing a tools copy and then expecting the unified launcher to use it.

## When firmware and Python need to change together

The dashboard communicates with the MSP430 over UART. The current protocol uses newline-terminated ASCII commands: `M` requests metadata, `X` requests Stop, and `S,...` starts a run with technique-specific numeric arguments. `build_start()` defines the Python side of that ordering and scaling. Each technique's Start function in `main.c` defines the firmware side; `ec_protocol.h` and `ec_control.inc` provide parsing/mailbox support.

For a new parameter or returned measurement:

1. Define its meaning, units, valid range and whether it changes hardware or only presentation.
2. Add a dashboard control/default if needed, then update `build_start()` argument ordering/scaling.
3. Update firmware argument validation and the acquisition configuration that actually uses it.
4. For a new returned value, update the firmware's record output, `Session.feed()`, `COLUMNS`, save/load handling, plot labels and metadata together.
5. For an incompatible contract, change the firmware's schema metadata and Python's accepted schema maps (`SCHEMAS` and `SCHEMA_TECHNIQUES`). This prevents an old dashboard silently interpreting a new record format.
6. Add the dependency to `requirements.txt` if your new Python code imports another library, and install it in the interpreter used for the dashboard.

Changing the UART baud requires matching the MSP430 UART register setup, metadata, dashboard baud and detection candidates. Merely changing a label or `BAUD` does not reconfigure the MCU clock or serial hardware.

## Keep the baseline and check your changes

Keep an untouched copy of both extracted ZIPs as your baseline. Make changes in a separate local working copy. Each ZIP’s `PACKAGE.json` identifies the download set. Record that identifier, your changes, sensor configuration and wiring with your measurements. You do not need Git or GitHub write access to customise your downloaded files.

For a Python-only layout/default change, restart the correct script and check the affected UI and saving. For firmware changes, rebuild and flash the selected project, confirm its metadata, then check a known reference or dummy load before drawing conclusions from sensor data. For protocol changes, check both ends together, including Stop, completion, repeated Start and saved units/timestamps.

Run the software suite from `CCS_Import_Projects`:

```sh
python -m unittest discover -p 'test_*.py'
```

The suite includes extracted C-function checks and needs a host C compiler. It does not compile the entire MSP430 firmware or prove physical accuracy. The original bench results apply to their recorded versions and settings; a customised build needs checks appropriate to what changed. [Validation status](VALIDATION.md) identifies what the baseline actually demonstrated. The included wide-range EIS R2 work remains unfinished.
