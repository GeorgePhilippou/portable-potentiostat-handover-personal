# Downloads

Download **one firmware ZIP** for the board and **the Python dashboard ZIP** for the computer. No GitHub account or invitation is required. Each link below downloads a ZIP directly.

## 1. Firmware for the MSP430

| Technique | Download | Editable source |
|---|---|---|
| OCP | [Download firmware ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_OCP.zip) | [Open project](../CCS_Import_Projects/Handover_OCP) |
| Chronoamperometry | [Download firmware ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_Chronoamperometric.zip) | [Open project](../CCS_Import_Projects/Handover_Chronoamperometric) |
| Amperometry | [Download firmware ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_Amperometric.zip) | [Open project](../CCS_Import_Projects/Handover_Amperometric) |
| CV / LSV | [Download firmware ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_CyclicVoltammetry.zip) | [Open project](../CCS_Import_Projects/Handover_CyclicVoltammetry) |
| EIS — in development | [Download firmware ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_EIS.zip) | [Open project](../CCS_Import_Projects/Handover_EIS) |

## 2. Dashboard for the computer

**[Download Python dashboard ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/Python_Dashboard.zip)**

One dashboard serves all techniques. The ZIP includes the launcher, plotting and data-handling code, graphical assets, dependency list and instructions. Extract it, install its `requirements.txt`, then run **`potentiostat_dashboard.py` inside the extracted Python_Dashboard folder**.

The firmware ZIP contains a complete editable CCS project. Import its extracted `Handover_*` folder into CCS, build and flash. CV and LSV use the same firmware. EIS remains experimental.

Download both ZIPs from this page so they belong to the same source set. `PACKAGE.json` inside each ZIP identifies that set; [checksums](MANIFEST.json) are available. [Full setup guide](../docs/START_HERE.md).
