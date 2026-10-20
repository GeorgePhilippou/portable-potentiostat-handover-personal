# Version 2 board connections

The handover uses the MSP430FR5994/AD5941 **Version 2** development board and TI **MSP-FET**. These pin assignments were checked against the saved Version 2 design and used for the bench work. Verify physical pin 1/orientation on your assembled board; a photograph is not a substitute for the numbered connections.

## Design reference documents

[View the schematic, PCB layout and Analog Devices AN-1563](hardware/README.md). The PDF exports include their original Version_2 / Board V1.0 naming; use the document index and physical header orientation to interpret them. They do not replace the assembled-harness photo or the wiring tables below.

## Assembly photo

<p align="center">
  <img src="images/development-board-annotated.png" width="600" alt="MSP-FET, connected potentiostat board and breadboard RC test load">
</p>

The photograph shows the actual bench assembly: MSP-FET at left, the board with its prepared jumper harness in the centre, and a breadboard RC dummy load at right. It illustrates the test arrangement rather than a connected electrochemical sensor. Use the numbered tables below for wiring and select the header/input path for the intended technique.

## Programming and UART — J11

| Board header | Function | MSP-FET 14-pin connector |
|---|---|---|
| J11 pin 1 | TEST / SBWTCK | Pin 7 |
| J11 pin 2 | MCU TXD, P2.0 | Pin 12, UART input to FET |
| J11 pin 3 | MCU RXD, P2.1 | Pin 14, UART output from FET |
| J11 pin 4 | RST / SBWTDIO | Pin 1 |
| J11 pin 5 | Ground | Pin 9 |

The TX/RX descriptions above specify direction. Use this table rather than interpreting a generic UART cable's labels.

## Power — demonstrated direct-rail arrangement

J11 carries no power pin. MSP-FET **pin 2, VCC_TOOL**, configured to 3.3 V, supplies **J6 pin 2 (D3V3)** and **J7 pin 1 (A3V3)** using a split jumper lead. The two rails share this source. **This bypasses the separate analogue regulator; it does not provide analogue-regulator filtering.** The digital regulator was not populated on the tested board.

Isolate populated regulator outputs by removing their output jumpers before direct-rail injection. Close J10 to connect the digital rail to the MCU. Keep unused IMU/SD/OLED loads disconnected for the basic setup. Do not connect a second power source simultaneously. MSP-FET pin 4 is external-target voltage sensing, not another output; do not bridge it to VCC_TOOL. Check the assembled-board current against the FET's target-power limit and measure both rails when commissioning a new board.

## Sensor / dummy-load header — J3

| Pin | AD5941 signal | Handover use |
|---|---|---|
| 1 | RE0 | Reference electrode |
| 2 | CE0 | Counter electrode |
| 3 | SE0 | WE for amperometry, chrono, CV/LSV and EIS |
| 4 | AIN0 | WE for OCP electrode mode |
| 5–7 | AIN1–AIN3 | Additional analogue inputs |

**Resistor test:** join RE and CE (pins 1 and 2); place the 6.8 kΩ resistor between that node and WE (pin 3). **Chrono RC test:** place the 6.8 kΩ resistor and 10 µF capacitor in series between those same nodes. Keep the capacitor type, polarity and applied voltage appropriate to the setup.

**OCP electrode mode:** WE goes to pin 4 and RE to pin 1; leave CE and SE0 disconnected. The demonstrated OCP internal-reference tests instead select internal ADC inputs in `main.c`; those tests do not measure an attached sensor. Check the active ADC configuration before interpreting a plotted voltage as electrode potential.

The Version 2 board has no Version 5 TMUX electrode switch. P2.3 writes do not reroute or physically disconnect its WE. Idle/Stop should not be represented as galvanic electrode isolation.

The female header plus labelled jumper leads terminating in crocodile clips is suitable for the bench handover. Secure the harness and label WE, RE, CE and ground; avoid relying on loose, unlabelled leads.

Reference: [TI MSP Debuggers User's Guide](https://www.ti.com/lit/ug/slau647o/slau647o.pdf). Schematic and PCB layout PDF exports are included in the document index. Editable native KiCad project files and PCB fabrication files are not included.
