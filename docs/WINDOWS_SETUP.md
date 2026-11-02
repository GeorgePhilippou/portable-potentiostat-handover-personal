# Windows setup: MSP-FET and CCS

Use this checklist for a new Windows computer or a **“No USB FET was found”** error. Work in this order: **USB detection → project import/build → programming/target connection → dashboard UART**. A USB-debugger detection failure occurs before the application firmware or sensor measurement can be assessed.

If CCS or Python is not yet installed, start with **[Install CCS and Python](INSTALL_SOFTWARE.md)**, then return to the checks below.

## Software and drivers

| Component | Purpose | Where to obtain it |
|---|---|---|
| Code Composer Studio with MSP430 device/debug support | Build and program the MSP430FR5994 using the MSP-FET | [TI Code Composer Studio](https://www.ti.com/tool/CCSTUDIO) |
| TI MSP-FET low-level USB drivers | Let Windows communicate with the debugger and its backchannel UART | Normally installed with CCS. If missing or installation failed, use the driver installer supplied with CCS or the official **MSPDS-USB-DRIVERS** fallback below. |
| TI MSP430 compiler 21.6.1.LTS | Toolchain specified by the supplied projects | Install/select through CCS. This is a compiler, not a USB driver. |
| Python 3.11 and the supplied `requirements.txt` | Run the computer dashboard | Follow [Start here](START_HERE.md). `pyserial` is a Python library, not the Windows device driver. |

**Standalone driver download:** open [TI MSP Debug Stack (MSPDS)](https://www.ti.com/tool/MSPDS), find **MSPDS-USB-DRIVERS — Stand-alone Driver Installer for USB Low-Level Drivers**, and choose **Windows Installer for MSP430_FET_Drivers**. The driver entry is distinct from the MSPDS developer/source packages. TI may require a sign-in/export approval for the download.

Start with the drivers supplied by the installed CCS release. The standalone package listed by TI is older; it is a fallback for missing USB drivers, not evidence that every current Windows configuration has been tested. If installation is blocked on a managed computer, ask IT to install the signed TI driver; do not bypass Windows driver-signing protections.

The project/build baseline is **classic CCS 12.5, MSP430FR5994 and TI MSP430 compiler 21.6.1.LTS**. Newer Theia-based CCS supports existing CCS projects, but menus/workspaces differ and this handover has not been exercised end-to-end on that interface or on a fresh Windows computer. A different interface alone does not require downgrading.

## 1. Check Windows can see the MSP-FET

1. Close active CCS debug sessions, serial terminals and Python dashboards.
2. Connect the MSP-FET directly to the computer with a known working **USB data cable**. Avoid a dock/hub for the first check. A power LED alone does not prove USB data communication.
3. Open **Device Manager** and watch what changes when the MSP-FET is unplugged/reconnected. Expand **Ports (COM & LPT)** and inspect any **Other devices** or unknown USB entries.
4. The normal TI interfaces are typically named **MSP Debug Interface (COMx)** and **MSP Application UART1 (COMy)**. Names can vary with driver/firmware versions. Record the names, COM numbers and any warning icons; do not assume a particular COM number.

| Observation | Next check |
|---|---|
| Nothing appears or disappears | Try another known data cable and USB port. Test the same probe/cable on a known working computer. This is a USB connection/enumeration issue; changing the firmware source will not fix it. |
| Unknown device or yellow warning icon | Open Properties and record the device-status error and Hardware IDs. Check/repair the TI driver installation. |
| TI interfaces appear normally, but CCS still says no FET | Check selected connection/probe instance, installed MSP430 support and whether another CCS/IAR/programming process owns the debug interface. Restart CCS after reconnecting the probe. |
| CCS detects the FET but cannot identify/connect to the MCU | Move to target power, ground, TEST/RST/SBW wiring and selected MCU checks. This is a different stage from USB detection. |

The **Debug Interface** is used by CCS. The **Application UART** is used by the Python dashboard after programming. Do not substitute one for the other.

## 2. Import the complete firmware project

1. Download and **extract** the firmware ZIP. Keep the original ZIP as your baseline.
2. Select the extracted `Handover_*` folder containing `main.c`, `.project`, `.cproject` and `.ccsproject`. Opening `main.c` alone does not import a buildable project. Do not work inside the ZIP viewer.
3. In **classic CCS 12.5**, use **Project → Import CCS Projects**, browse to the extracted project, select it in the discovered-project list and finish the import.
4. In **Theia-based CCS**, TI documents **File → Import Project(s)**. Browse to the extracted project and select it from the discovered list. Import the project, not a workspace copied from another computer. Retain an untouched copy before allowing project migration.
5. Confirm MSP430 support is installed, the device is **MSP430FR5994**, and compiler **21.6.1.LTS** is available. Build the selected project and read the first build error, if any. Building does not require the probe to be detected.

The target configuration supplied under `targetConfigs/MSP430FR5994.ccxml` uses **TI MSP430 USB1** and **MSP430FR5994**. Here “USB1” selects a probe instance; it is not the Windows COM1 port. With one probe, use the supplied first/default connection rather than an arbitrary numbered FET.

A disabled **Test Connection** button is not by itself a fault: TI documents that this test is unavailable for some non-XDS connections. Do not use that button's availability as the only MSP-FET detection check.

## 3. Program and confirm execution

Once the project builds and the FET is detected, use the CCS programming/debug workflow for the selected project. Check the full console message: distinguish “No USB FET” from “Unknown device” or target-connection errors.

For target-side failures, use the [board connection and 3.3 V power tables](HARDWARE.md), including common ground, TEST/SBWTCK and RST/SBWTDIO. Disconnect power before changing wiring; the MSP-FET is also the board's supply in this assembly.

If CCS successfully loads the program but leaves an active debug session paused at `main` or a breakpoint, continue execution using the version's Run/Resume control. An independent Flash operation may already start execution; do not assume an extra Resume step is always required. Close any CCS serial terminal before opening Python.

## 4. Check the dashboard separately

Run the extracted `Python_Dashboard/potentiostat_dashboard.py` using the environment where `requirements.txt` was installed. Select **MSP Application UART1's COM port**, then **Detect and open**. Use **Show connection details** if metadata is not received.

- **Port missing:** return to Device Manager/driver checks.
- **Port busy/access denied:** close other serial readers; confirm the chosen port is Application UART.
- **Port opens, no bytes:** confirm the MCU is running, the correct firmware is loaded and the UART wiring matches [the board table](HARDWARE.md).
- **Bytes arrive, metadata unsupported/damaged:** check matched firmware/dashboard downloads, the selected port and baud/schema details. Keep the raw error text.

Only after connection succeeds should you check the load, experiment controls and plotted measurements. Start with a known dummy load or the OCP internal-reference diagnostic. EIS remains in development.

## Information to capture if it still fails

Keep the exact CCS version, Windows version, full error/console text, Device Manager screenshot and device-status details, imported project name and compiler version. Note which cable/USB port was tested. If the dashboard opens, also capture **Show connection details**. These distinguish installation, USB, target and UART problems without guessing.

## TI references

- [MSP Debug Stack and official Windows USB-driver installer](https://www.ti.com/tool/MSPDS)
- [MSP Debuggers User's Guide — hardware/driver installation](https://www.ti.com/lit/ug/slau647o/slau647o.pdf)
- [CCS connection troubleshooting — installed device/probe support and target configuration](https://software-dl.ti.com/ccs/esd/documents/ccs_debugging_jtag_connectivity_issues.html)
- [Theia-based CCS project import procedure](https://software-dl.ti.com/ccs/esd/documents/users_guide_ccs_20.4.0/ccs_getting-started.html)
- [CCS Test Connection limitations](https://software-dl.ti.com/ccs/esd/documents/users_guide_10.1.0/ccs_debug-main.html)
