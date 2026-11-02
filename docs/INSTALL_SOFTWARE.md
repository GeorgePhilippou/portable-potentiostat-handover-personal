# Install Code Composer Studio and Python

Use this page only if these programs are not already available on your computer. The [README quick start](../README.md#get-started) assumes **Code Composer Studio with MSP430 support** and a working **Python environment** are installed. If both are already set up, go straight to [Start here](START_HERE.md).

| Software | Used for | Handover baseline |
|---|---|---|
| Code Composer Studio (CCS), with TI MSP430 compiler and debugger support | Import/build firmware and program the MSP430 through the MSP-FET | Classic CCS 12.5; TI MSP430 compiler 21.6.1.LTS; MSP430FR5994 |
| Python and the dashboard libraries | Run the dashboard on the computer | Python 3.11; packages listed in `requirements.txt` |

Installing Python does not install CCS, and installing CCS does not install the Python dashboard libraries. Spyder is optional.

## 1. Install Code Composer Studio

1. Open the official [TI CCS download page](https://www.ti.com/tool/CCSTUDIO). Choose the **desktop installer** for your operating system and processor architecture. Check TI's requirements for that release, particularly on Apple Silicon Macs. If the download is a ZIP, extract it before running the installer.
2. To reproduce the original interface, use **CCS 12.5** from TI's previous-version downloads where available. A newer supported CCS release can import existing CCS projects, but its menus differ. Do not replace a working installation solely to make it look like the screenshots.
3. Run the installer. If it offers device-family/component choices, include **MSP430 device support, MSP430 debug support and the TI MSP430 compiler**. MSPM0/Arm support alone is not the toolchain for this board. Install into a normal local folder and allow the signed TI USB-driver installation on Windows. Managed computers may need IT assistance.
4. Open CCS. Create/select a workspace or working folder when prompted; this is where CCS organises projects. You do not need a workspace from another person's computer.
5. Check that **MSP430FR5994** and a **TI MSP430 USB** connection are available in project/target settings, and that compiler **21.6.1.LTS** is available. If the imported project reports that compiler missing, install the matching TI MSP430 compiler through the release's compiler/component installer and select it for the project. An Arm compiler is not a replacement.
6. On Windows, follow [MSP-FET drivers and CCS setup](WINDOWS_SETUP.md) to verify the debugger in Device Manager before programming.

**Check before continuing:** CCS opens, MSP430 support is installed, and you can select the MSP430FR5994 target and required compiler. This confirms software setup, not yet a successful board connection or measurement.

The newer CCS interface is based on Theia. Follow [TI's import procedure](https://software-dl.ti.com/ccs/esd/documents/users_guide_ccs_20.4.0/ccs_getting-started.html) if its menus differ from the classic CCS instructions. The handover's own build/workflow checks used classic CCS; other releases need checking on the actual computer.

## 2. Install Python if needed

If you already have a suitable Python environment, keep it and skip to section 3. Check it with `python --version` (or `python3 --version` on macOS). Python **3.11** is the tested baseline; do not assume that having a different version installed automatically means it cannot work.

For a new installation, one route is **Miniforge**, which installs Python and the Conda environment manager. It lets you create a separate Python 3.11 environment without changing other projects. You do not need both Miniforge and a separate python.org installation.

1. Open the official [Miniforge download and installation instructions](https://github.com/conda-forge/miniforge). Choose the installer for your computer:

   | Computer | Installer |
   |---|---|
   | Windows 10/11, Intel/AMD 64-bit | `Miniforge3-Windows-x86_64.exe` |
   | macOS, Apple Silicon | `Miniforge3-MacOSX-arm64.sh` |
   | macOS, Intel | `Miniforge3-MacOSX-x86_64.sh` |

2. **Windows:** run the EXE, follow the installer prompts and keep the Start-menu shortcut. Open **Miniforge Prompt** from Start afterwards. You do not need to add Miniforge globally to PATH.
3. **macOS:** open Terminal and run the downloaded SH installer. For an Apple Silicon download in Downloads, use `bash ~/Downloads/Miniforge3-MacOSX-arm64.sh`; for Intel replace `arm64` with `x86_64`. Follow its prompts, allow shell initialisation, then close and reopen Terminal. Use the actual downloaded filename if it differs.
4. In Miniforge Prompt (Windows) or the initialised Terminal (macOS), create and activate the environment:

   ```sh
   conda create -n potentiostat -c conda-forge python=3.11 pip "pyqt=5"
   conda activate potentiostat
   python --version
   ```

   Confirm the proposed installation when prompted. The last command should report Python 3.11.x. Qt 5 supplies the interactive window toolkit required by this dashboard. The installer may use a newer Python in its base environment; the named `potentiostat` environment is the one that matters here.

If `conda` is not recognised on Windows, open **Miniforge Prompt**, rather than an unrelated terminal. On macOS, follow Miniforge's shell-initialisation instructions and reopen Terminal. Check the distributor's requirements for other OS/CPU combinations.

## 3. Install the dashboard libraries

1. [Download the Python dashboard ZIP](../downloads/README.md#2-dashboard-for-the-computer) and extract it. Keep `potentiostat_dashboard.py`, `ec_dashboard.py`, `dashboard_assets` and `requirements.txt` together inside **Python_Dashboard**.
2. Keep using the same terminal/environment. If following the Miniforge route, run `conda activate potentiostat` each time you open a new terminal.
3. Change directory to the extracted **Python_Dashboard** folder. On Windows, copy the folder's full path from File Explorer and use `cd /d "PASTE THE FOLDER PATH HERE"` in Miniforge Prompt. On macOS, type `cd `, drag the folder from Finder into Terminal, then press Return. Do not type the placeholder literally.
4. Install the libraries using that environment's Python:

   ```sh
   python -m pip install -r requirements.txt
   python -m pip check
   ```

5. Check Python can import the required libraries:

   ```sh
   python -c "import sys, numpy, matplotlib, serial; from PyQt5 import QtWidgets; print(sys.executable); print(sys.version); print('Dashboard libraries available')"
   ```

The import check should finish without an error. `sys.executable` shows which Python will run the dashboard. If you are using an existing non-Conda installation, use that installation's Python command consistently, including `python3` where appropriate. Installation needs internet access to retrieve packages.

## 4. Check the dashboard opens

From the same folder and environment, run:

```sh
python potentiostat_dashboard.py
```

The connection window should open. You can choose an **Offline experiment** and press **Open offline** to inspect its dashboard without a board. Offline mode generates no measurement data. A failure to identify the board is a separate USB/firmware issue; use [Windows troubleshooting](WINDOWS_SETUP.md) where applicable.

**Optional Spyder:** you can edit/run the same file in Spyder, but its console must use the environment containing these libraries. Follow [Spyder's environment instructions](https://docs.spyder-ide.org/current/faq.html) and install the matching `spyder-kernels` version if requested. Running from the terminal above does not require Spyder.

## Continue with normal setup

Return to **[Start here](START_HERE.md)** to connect the hardware, import/build/flash the selected firmware and record a known load. Consult the **[Dashboard guide](DASHBOARDS.md)** for the controls. Installing software or opening an offline window does not validate a physical measurement; EIS remains in development.

References: [TI CCS](https://www.ti.com/tool/CCSTUDIO) · [TI USB drivers](https://www.ti.com/tool/MSPDS) · [Miniforge](https://github.com/conda-forge/miniforge) · [Conda environments](https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html).
