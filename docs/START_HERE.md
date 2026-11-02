# Start here

Follow these steps in order to put the firmware onto the board and record data with the Python dashboard.

**Before starting:** you need the board, MSP-FET debugger, prepared cables, and the chosen sensor or test load. CCS and Python should already be installed. If they are not, follow **[Install CCS and Python](INSTALL_SOFTWARE.md)** first.

## 1. Download and extract the files

Open **[Downloads](../downloads/README.md)** and download:

- **One firmware ZIP** for the experiment you want to run. This is the code for the board.
- **The Python dashboard ZIP**. This is the software for your computer.

Extract both ZIPs. On Windows, right-click each ZIP and choose **Extract All**. On macOS, double-click each ZIP.

**You should have two folders:** a `Handover_*` firmware folder, such as `Handover_OCP`, and a `Python_Dashboard` folder. Keep all files in their folders. Do not open `main.c` by itself and expect CCS to recognise the whole project.

## 2. Connect the hardware

1. With power disconnected, check the prepared cables against the **[connection guide](HARDWARE.md)**.
2. Check the load/electrode connections for your experiment. OCP electrode mode uses a different WE pin from the current-measurement experiments.
3. Connect the MSP-FET to the computer using a USB **data** cable.

On Windows, check the debugger appears in Device Manager as described in **[Windows setup](WINDOWS_SETUP.md#1-check-windows-can-see-the-msp-fet)**. If it does not appear, fix that before trying to program the board.

## 3. Open the firmware in CCS

Open CCS and import the extracted **Handover_* folder**:

| CCS interface | Menu to use |
|---|---|
| Classic CCS, including 12.5 | **Project → Import CCS Projects** |
| Newer Theia-based CCS | **File → Import Project(s)** |

Browse to the firmware folder, select the project from the discovered list, and finish the import. You should then see the project in CCS's project list. Do not import the Python folder as firmware.

The board's device is **MSP430FR5994**. The supplied projects use **TI MSP430 compiler 21.6.1.LTS**. See [Windows/CCS setup](WINDOWS_SETUP.md#2-import-the-complete-firmware-project) if CCS cannot find the project, compiler or debugger.

## 4. Build and program the board

1. Select the imported project in CCS and choose **Build**. Wait until it finishes without build errors.
2. Use CCS's programming/debug action for that project. The **Debug** action (usually a bug icon; **Debug Project** in newer CCS) builds and loads the firmware onto the board.
3. Wait for programming to finish. If an active debug session stops at `main` or a breakpoint, press **Resume/Continue** (the play control) so the firmware runs. If you use a separate **Flash** action that already starts the program, no extra Resume step is needed.

**You should now have the selected firmware running on the board.** If you see **“No USB FET was found”**, follow [Windows troubleshooting](WINDOWS_SETUP.md); changing Python settings will not fix that error.

CV and LSV use the same firmware. Choose the scan mode later in the Python launcher. The supplied OCP firmware uses internal-reference inputs for a diagnostic; change `main.c` and rebuild/reflash before electrode OCP. **EIS remains [in development](EIS_DEVELOPMENT.md).**

## 5. Open the Python dashboard

1. Close other serial terminals and dashboards, including any CCS serial terminal.
2. Open a command window in the extracted **Python_Dashboard** folder. See [how to open that folder in a command window](INSTALL_SOFTWARE.md#3-open-the-dashboard-folder).
3. If you used the Miniforge installation instructions, run `conda activate potentiostat`.
4. The first time you use this Python setup, install the libraries:

   ```sh
   python -m pip install -r requirements.txt
   ```

5. Run the launcher:

   ```sh
   python potentiostat_dashboard.py
   ```

On macOS, use `python3` instead if that is the command for your Python installation. Use the same Python setup for installing the libraries and running the launcher.

**You should see the connection window.** Select **Automatic** or the MSP-FET's **Application UART** port, then press **Detect and open**. For the shared cyclic-voltammetry firmware, choose **CV** or **LSV** mode.

**You should now see the matching experiment dashboard.** If not, press **Show connection details** and keep the error text. [Connection troubleshooting](WINDOWS_SETUP.md#4-check-the-dashboard-separately) explains what to check next.

## 6. Record and save

1. Enter the experiment settings in the dashboard.
2. Press **Start**. Use **Stop** if you want to end the run early.
3. When the recording and transfer finish, press **Save session** and choose a new filename.

This saves a **CSV containing the raw measurements** and a **PNG of the plot**. Save before closing the dashboard. Chronoamperometry sends its buffered samples after acquisition, so its plot is delayed; allow the transfer to finish.

For screenshots and explanations of all buttons/settings, open the **[Dashboard guide](DASHBOARDS.md)**.

## If something goes wrong

| What happens | Where to look |
|---|---|
| CCS or Python is not installed | [Software installation](INSTALL_SOFTWARE.md) |
| CCS reports “No USB FET”, or the project will not import/build | [Windows and CCS troubleshooting](WINDOWS_SETUP.md) |
| Python reports a missing library/file | [Python library and folder setup](INSTALL_SOFTWARE.md#3-open-the-dashboard-folder) |
| Dashboard opens but cannot connect | **Show connection details**, then [UART checks](WINDOWS_SETUP.md#4-check-the-dashboard-separately) |
| Unsure what a dashboard control does | [Dashboard guide](DASHBOARDS.md) |
| Periodic ripple in measurements | [Mains-pickup checks](HARDWARE.md#reducing-mains-pickup) |

The [customisation guide](CUSTOMISATION.md) shows where to edit firmware and dashboard settings once the normal setup works.
