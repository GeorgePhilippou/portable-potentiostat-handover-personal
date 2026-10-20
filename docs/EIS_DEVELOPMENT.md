# EIS status

**The supplied EIS firmware is experimental and is not ready for routine measurements.** Its target range is 0.1 Hz–200 kHz; that full range has not been validated.

Earlier firmware produced resistor measurements from 100 Hz to 20 kHz. The current extension has unresolved high-frequency timeouts, and its low-frequency endpoint, long-point Stop/restart behaviour and capacitive phase response still require hardware checks. See [validation evidence](VALIDATION.md).

The included firmware uses schema `MSP430_EIS_UART_V2`, which is supported by the shared dashboard. Frequency controls are entered in Hz; the UART command uses integer millihertz. At the lowest endpoint, the programmed frequency can differ from the requested value because of waveform-generator resolution.

For EIS development, the hardware configuration starts in `Handover_EIS/main.c`, with acquisition implementation in `Impedance/`. The [customisation guide](CUSTOMISATION.md) maps firmware and dashboard functions.

Technical reference: [AD5940/AD5941 datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/AD5940-5941.pdf).
