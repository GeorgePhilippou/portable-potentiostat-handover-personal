# Handover_EIS

**[Download this firmware ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/firmware/Handover_EIS.zip)** · **[Download Python dashboard ZIP](https://github.com/GeorgePhilippou/portable-potentiostat-handover-personal/raw/refs/heads/main/downloads/Python_Dashboard.zip)**

Extract this firmware ZIP, import its `Handover_EIS` folder into CCS, build and flash. Extract the dashboard ZIP separately and run its `potentiostat_dashboard.py` on the computer.

Hardware configuration is in `main.c`. If you are working from a clone of the whole repository rather than these ZIPs, the shared launcher is in the parent `CCS_Import_Projects` folder.

See the [operating guide](../../docs/START_HERE.md) and [validation status](../../docs/VALIDATION.md).

**Experimental:** the included EIS R2 source is unfinished. The 100 Hz–20 kHz bench results used the earlier R1 build; do not attribute them to this source. See [EIS development status](../../docs/EIS_DEVELOPMENT.md).

To adapt this experiment, see the [customisation guide](../../docs/CUSTOMISATION.md) for configuration functions, acquisition code and dashboard changes.
