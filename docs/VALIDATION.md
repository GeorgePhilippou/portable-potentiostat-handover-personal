# Bench validation and limits

These results were recorded on 12–13 October 2026 on the Version 2 board. Nominal loads are not traceable calibration standards.

| Technique / build | Recorded check | Result | Practical limit |
|---|---|---|---|
| OCP R6 | Internal voltage-reference ADC routing | Stable reference checks, six-decimal voltage/raw-code reporting | Electrode OCP and independent voltage accuracy not validated |
| Chrono R3 | 6.8 kΩ + 10 µF series, 100 mV step | 149 samples; fitted τ 69.94 ms vs nominal 68 ms | Approximately 150.04 samples/s; buffered transfer after acquisition |
| Amperometry R4 | Three 10 s runs at 100, 200 and 0 mV, 6.8 kΩ | 300 samples; settled means 14.8776, 29.8389 and 0.00163 µA | Functional dummy-load check; retain settling samples |
| CV/LSV R3 | −200 to +200 mV, 100 mV/s, 6.8 kΩ | 160 CV and 80 LSV points; inferred R 6.716 and 6.709 kΩ | Tested modest sweep settings; not every allowed setting |
| EIS R1, historical | 100 Hz–20 kHz, nominal 6.8 kΩ | 48 points; magnitude 6709.45–6732.83 Ω, phase −0.3799° to +0.0360° | Earlier R1 build only; no physical capacitor phase check |
| EIS R2, included source | Extension target 0.1 Hz–200 kHz | First high-frequency attempt timed out; latest timing change untested | Experimental; not demo-ready |

The supplied EIS project is **R2**, not a reconstructed copy of validated R1. The R1 data are retained as evidence, not as validation of R2. The requested 0.1 Hz–200 kHz range is not ready for routine measurements.

## Evidence

Measured CSVs, summary JSON and comparison plots are under [examples](../examples/). EIS raw JSONL logs retain exact transmitted/received bytes: `eis_uart_161852.jsonl` and `eis_uart_162046.jsonl` are the successful historical checks; `eis_uart_163811.jsonl` and `eis_uart_163902.jsonl` are incomplete/failed diagnostics. Do not combine them as successful runs.

## Noise in the supplied chrono example

The saved 149-sample chrono recording contains a near-50 Hz component in its settled tail, approximately 0.083 µA peak-to-peak after accounting for the RC decay. Its timestamp grid is approximately 150 samples/s. This is consistent with mains-related interference, but does not identify a physical source: charger state was not recorded, and higher-frequency components can alias into this band. The raw recording is retained unchanged. A paired battery-versus-charger test is needed before attributing the ripple to the laptop supply; see [noise guidance](HARDWARE.md#reducing-mains-pickup).

## Software checks

The root dashboard/protocol suite can be run from `CCS_Import_Projects` with `python -m unittest discover -p 'test_*.py'`. Some tests compile extracted C functions and require a host C compiler. They exercise conversion, wire protocols and plotting; they do not replace a CCS firmware build or hardware acquisition test.
