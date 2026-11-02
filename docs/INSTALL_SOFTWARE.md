# Install CCS and Python

You need two programs:

- **Code Composer Studio (CCS)** puts the firmware onto the board.
- **Python** runs the dashboard on your computer.

**Already have both?** Go to [Start here](START_HERE.md). Otherwise, follow the sections you need below. You do not need Spyder to use the dashboard.

## 1. Install Code Composer Studio

1. Open the [Texas Instruments CCS download page](https://www.ti.com/tool/CCSTUDIO).
2. Download the **desktop installer** for your computer: Windows or macOS. If it comes in a ZIP, extract it first.
3. Run the installer. If it asks which devices to support, select **MSP430**. Allow it to install the TI USB drivers on Windows.
4. Open CCS. If asked to choose a **workspace**, create/select a local folder. A workspace is simply where CCS organises your projects.

**You should now be able to open CCS.** You will import the board's firmware later, using [Start here](START_HERE.md).

The projects were checked with **CCS 12.5** and **TI MSP430 compiler 21.6.1.LTS**. CCS 12.5 is available through TI's previous-version downloads where offered. You can keep a newer CCS installation; its menus may look different. If the project reports a missing compiler, or Windows cannot find the debugger, use the [Windows/CCS troubleshooting guide](WINDOWS_SETUP.md).

On a Mac, check TI's operating-system and processor requirements for the chosen CCS release. On a managed university computer, IT may need to approve the installation.

## 2. Install Python

**If Python already works on your computer, skip to section 3.** Use your existing Python environment for the remaining commands.

For a new installation, use **Miniforge**. This installs Python and lets us select Python 3.11, the version used for the dashboard checks.

### Windows

1. Open the [Miniforge downloads page](https://github.com/conda-forge/miniforge#download).
2. For an Intel/AMD 64-bit Windows 10/11 PC, download **Miniforge3-Windows-x86_64.exe**.
3. Run it and follow the installer prompts. Keep the Start-menu shortcut option selected.
4. From the Windows Start menu, open **Miniforge Prompt**. Enter the commands below in that window, not in CCS.

### macOS

1. Open the [Miniforge downloads page](https://github.com/conda-forge/miniforge#download).
2. Choose the macOS **arm64 SH installer** for an Apple Silicon Mac, or **x86_64 SH installer** for an Intel Mac.
3. Open **Terminal**. Type `bash `, drag the downloaded `.sh` file into the Terminal window, then press Return.
4. Follow the installer prompts and allow shell initialisation. Close and reopen Terminal afterwards. Enter the commands below in Terminal.

### Set up the dashboard's Python environment

Copy this command, paste it into **Miniforge Prompt** (Windows) or **Terminal** (macOS), then press Enter:

```sh
conda create -n potentiostat -c conda-forge python=3.11 pip "pyqt=5"
```

If asked to proceed, type **y** and press Enter. Wait for installation to finish. Then run:

```sh
conda activate potentiostat
```

This selects the Python setup we just created. Run this check:

```sh
python --version
```

**You should see `Python 3.11.x`.** The last number may vary. If Windows says `conda` is not recognised, check that you opened **Miniforge Prompt**.

## 3. Open the dashboard folder

1. Go to [Downloads](../downloads/README.md) and download the **Python dashboard ZIP**.
2. Extract the ZIP. On Windows, right-click it and choose **Extract All**. On macOS, double-click it.
3. Open the extracted **Python_Dashboard** folder. You should see `potentiostat_dashboard.py`, `ec_dashboard.py`, `requirements.txt` and `dashboard_assets`. Keep them together.
4. Open your command window:
   - **Windows with Miniforge:** open **Miniforge Prompt**.
   - **macOS:** open **Terminal**.
   - **Existing Python installation:** use the terminal for that installation.
5. If you followed the Miniforge steps, enter `conda activate potentiostat` again. Do this each time you open a new command window.
6. Tell the command window where the dashboard files are:
   - **Windows:** copy the folder path from File Explorer's address bar. Type `cd /d `, paste the path inside quotation marks, and press Enter. For example: `cd /d "C:\Users\YourName\Downloads\Python_Dashboard"`. Use your own path, not this example.
   - **macOS:** type `cd `, drag the **Python_Dashboard folder** into Terminal, then press Return.

The next commands must be run from this folder, because it contains `requirements.txt` and the launcher script.

## 4. Install the dashboard libraries

In the same command window, run:

```sh
python -m pip install -r requirements.txt
```

Wait for the command to finish and return to the prompt. This downloads the extra Python libraries the dashboard needs. You only need to do this when setting up this Python environment or updating the supplied requirements.

If you normally use `python3` on macOS, use `python3` consistently in this and the next command. If you see **“requirements.txt not found”**, return to section 3: the command window is in the wrong folder.

## 5. Check the dashboard opens

Run:

```sh
python potentiostat_dashboard.py
```

**You should see the “Portable potentiostat — connect” window.** To look around without a board, choose an **Offline experiment** and press **Open offline**. It shows the controls but does not create measurement data. A message saying no firmware was identified does not necessarily mean Python installation failed.

You are now ready to follow **[Start here](START_HERE.md)** to connect and program the board.

If the window does not open, keep the error text from the command window. If it opens but cannot connect to the board, use [Windows/CCS troubleshooting](WINDOWS_SETUP.md) instead of reinstalling Python.

<details>
<summary>Optional: using Spyder</summary>

Spyder is an editor, not a requirement. If you prefer it, its console must use the same Python environment where you installed the libraries. Follow [Spyder's environment instructions](https://docs.spyder-ide.org/current/faq.html). The terminal method above avoids this additional setup.

</details>

Software sources: [TI CCS](https://www.ti.com/tool/CCSTUDIO) · [Miniforge](https://github.com/conda-forge/miniforge) · [Conda environment instructions](https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html).
