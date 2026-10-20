# Cyclic voltammetry — CV and LSV modes

One firmware supports both modes. Select CV for a forward-and-return scan or LSV for a one-way scan in the Python launcher. Changing between them does not require reflashing.

**[Download this firmware ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_CyclicVoltammetry.zip)** · **[Download Python dashboard ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/Python_Dashboard.zip)**

Extract this firmware ZIP, import its `Handover_CyclicVoltammetry` folder into CCS, build and flash. Extract the dashboard ZIP separately and run its `potentiostat_dashboard.py` on the computer.

Hardware configuration is in `main.c`. If you are working from a clone of the whole repository rather than these ZIPs, the shared launcher is in the parent `CCS_Import_Projects` folder.

See the [operating guide](../../docs/START_HERE.md) and [validation status](../../docs/VALIDATION.md).

To adapt this experiment, see the [customisation guide](../../docs/CUSTOMISATION.md) for configuration functions, acquisition code and dashboard changes.
