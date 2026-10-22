# Start here

This is an editable baseline. First follow the normal workflow below; then use the [customisation guide](CUSTOMISATION.md) to adapt firmware and dashboard for your sensor or experiment.

## 1. Download and install

Open the [Downloads page](../downloads/README.md). Download the firmware ZIP for your technique and the separate shared Python dashboard ZIP, then extract both to a normal local folder.

You need the Version 2 board, TI MSP-FET with the prepared programming/UART/power cable, and the chosen sensor or dummy load.

The firmware was built using **classic Code Composer Studio 12.5**, TI MSP430 compiler **21.6.1.LTS**, and MSP430FR5994. Use the existing CCS workspace: **Project → Import CCS Projects**, select the extracted `Handover_*` firmware folder and import that project. If you cloned/downloaded the entire repository instead, select its `CCS_Import_Projects` folder. No change to your existing workspace is needed. The newer CCS Theia workflow is not covered by these checks.

The dashboard was exercised with Python 3.11. Open a terminal in the extracted **Python_Dashboard** folder and install its dependencies in the environment that runs Spyder/Python:

```sh
python -m pip install -r requirements.txt
```

From a terminal on macOS, `python3` may be the command name. For Spyder, confirm its configured interpreter is the environment where these packages were installed. Spyder itself is optional and is not installed by this requirements file.

## 2. Connect the board

With power disconnected, follow [the connection tables](HARDWARE.md). The OCP WE input uses a different pin from the current-measurement techniques. Use the prepared cable's pin numbers, not wire colour alone. Connect the MSP-FET to USB after the connections are checked.

The [hardware document index](hardware/README.md) provides the schematic and PCB layout PDFs, plus the Analog Devices application note for background reading.

## 3. Flash the experiment

Select the required handover project in CCS, build it, and use the usual **Flash** action. Hardware choices such as ADC input and gain are edited in that project's `main.c` and require rebuilding/reflashing. The cyclic-voltammetry firmware also supports LSV: choose CV for a forward-and-return scan or LSV for a one-way scan in the Python launcher. Switching modes does not require reflashing. **EIS is still in development; see its [status page](EIS_DEVELOPMENT.md) before use.**

## 4. Open the dashboard

Close other serial terminals and dashboards. In Spyder open **`potentiostat_dashboard.py` inside the extracted Python_Dashboard folder** and press Run, or from that folder run:

```sh
python potentiostat_dashboard.py
```

If using the entire repository rather than the separate dashboard ZIP, the launcher remains under `CCS_Import_Projects/`.

The launcher identifies the firmware using UART metadata. Automatic detection reads metadata; it does not start acquisition. Select CV or LSV if that firmware is detected. If necessary, select the Application UART port manually and press **Detect and open**. Use **Show connection details** if identification fails.

## 5. Acquire and save

Set the experiment parameters and press **Start**. Use **Stop** to end a run early. **Save session** stores raw CSV and a PNG. Record sensor/load identity, electrode connections and the purpose of the run. [Measured examples](../examples/) show the bench checks.

Chronoamperometry collects its buffered step first and sends the data afterwards at 9600 baud. Allow several seconds of transfer for a one-second capture; lack of immediate plotting during acquisition is expected.

## Plot controls

See the **[Dashboard guide](DASHBOARDS.md)** for screenshots, shared buttons and the settings/plots for each technique.

**Overview** gives a broad view including zero; **Detail** uses close automatic scaling. **Plot settings** offers fixed limits, line/point styles and display averaging. Keep averaging at 1 when assessing transient shape. The native toolbar supports zoom and pan, and limits persist during redraws. **Runs** hides individual runs; **Clear plot** hides all; **Show all** restores them. Those controls do not delete raw data. Saved CSVs retain all recorded samples.

## If the trace has periodic ripple

See [Reducing mains pickup](HARDWARE.md#reducing-mains-pickup). Compare the same dummy-load recording with the laptop charger connected and disconnected; keep the raw samples and record the setup.

## If connection fails

1. Close other serial readers; only one application should own the UART.
2. Refresh ports. macOS names resemble `/dev/cu.usbmodem…`; Windows uses `COM…`. Names can change after reconnecting.
3. Use the Application/backchannel UART, not the debugger interface.
4. Check the selected firmware build completed and was flashed to the board. Use your normal Flash workflow; only an active paused debug session needs execution to be continued.
5. Check the TX/RX connections against [the hardware table](HARDWARE.md), then inspect connection details. A port opening successfully alone does not prove the firmware is communicating.

[EIS development status](EIS_DEVELOPMENT.md) is separate from this normal operating path.
