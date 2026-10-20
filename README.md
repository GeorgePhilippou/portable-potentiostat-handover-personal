# Portable potentiostat — personal handover copy

This public copy is maintained by George Philippou to provide immediate access to the handover files. It is based on lab source commit `3eaae751ed7d65a48dd6f0718712464d4a3d1911` (20 October 2026). The [lab repository](https://github.com/Non-Invasive-Bioelectronics-Lab/portable-potentiostat-handover) remains the main version; access to it currently requires a lab invitation. This separate copy does not synchronise automatically.

An editable firmware and Python dashboard baseline for the MSP430FR5994/AD5941 development board, with TI MSP-FET programming and UART.

Developed by **George Philippou** · [Non-Invasive Bioelectronics Lab](https://github.com/Non-Invasive-Bioelectronics-Lab)

**[Download firmware and dashboard →](downloads/README.md)** · [Start here](docs/START_HERE.md) · [Connections and board drawings](docs/HARDWARE.md) · [Customisation](docs/CUSTOMISATION.md) · [Dashboard previews](docs/DASHBOARDS.md)

## System setup

<p align="center">
  <img src="docs/images/development-board-annotated.png" width="600" alt="MSP-FET Debugger, Portable Potentiostat and Series RC test circuit, labelled with purple arrows">
</p>
<p align="center"><em>MSP-FET, development board and RC test load.</em></p>

## Supported techniques

| Technique | Firmware download | Status |
|---|---|---|
| Open-circuit potential (OCP) | [Handover_OCP ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_OCP.zip) | Bench tested |
| Chronoamperometry | [Handover_Chronoamperometric ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_Chronoamperometric.zip) | Bench tested |
| Amperometry | [Handover_Amperometric ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_Amperometric.zip) | Bench tested |
| Cyclic voltammetry (CV) | [Handover_CyclicVoltammetry ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_CyclicVoltammetry.zip) | Bench tested |
| Linear sweep voltammetry (LSV) | [Same CV / LSV ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_CyclicVoltammetry.zip) | Bench tested |
| Electrochemical impedance spectroscopy (EIS) | [Handover_EIS ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_EIS.zip) | In development |

Bench tests used internal references and resistor/RC loads. EIS is still being completed; [test results and current limits](docs/VALIDATION.md) are documented separately.

## Get started

1. [Download the firmware ZIP for your technique](downloads/README.md) and the **[Python dashboard ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/Python_Dashboard.zip)**, then extract both.
2. In Code Composer Studio, build and flash the firmware project for the technique you want to use.
3. In the extracted **Python_Dashboard** folder, install the Python dependencies using `python -m pip install -r requirements.txt`.
4. Run **`potentiostat_dashboard.py` inside that Python_Dashboard folder**. It detects the firmware and opens the matching dashboard. Choose CV or LSV when using their shared firmware.

The [setup guide](docs/START_HERE.md) covers installation, connection and saving data. Download both ZIPs from the same downloads page. This personal copy is public; no invitation is needed to view or download it. [About this copy](docs/REPOSITORY.md).

## Adapt it to your experiment

Both firmware and dashboard are supplied as editable source. The [customisation guide](docs/CUSTOMISATION.md) shows where to change ADC inputs, gain, acquisition, controls, plotting and data export, and which changes require rebuilding/reflashing.

[Schematic, PCB layout and ADI application note](docs/hardware/README.md) · [Measured examples](examples/) · [Validation details](docs/VALIDATION.md)
