# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

实时风速监控系统 (Real-time Wind Speed Monitoring System) — a Windows desktop app that reads wind speed/direction data from one or more serial-port weather devices, shows it live on a web dashboard (gauges + Chart.js charts), and stores it to daily CSV files. Backend is Flask + Flask-SocketIO (threading mode); frontend is a single-page `index.html` in `web/templates/`. All UI text, comments, and log messages are in Chinese — match that when adding code.

The app is typically run packaged (PyInstaller → `dist/WindSpeedMonitor/`) but is developed and debugged as plain Python. Source lives entirely under `web/`; `run.bat` and `打包命令.txt` at the repo root are Windows launcher/packaging helpers.

## Commands

- **Run the app**: `run.bat` (from repo root) — sets UTF-8 codepage, `cd`s to `web/`, runs `..\venv\Scripts\python realtime_wind_monitor.py`. A virtualenv named `venv/` is expected next to `web/` (not currently present). Equivalent: `cd web && python realtime_wind_monitor.py`.
- **Install dependencies**: `pip install -r web/requirements.txt` (pinned: Flask 2.3.2, Flask-SocketIO 5.3.4, pyserial 3.5, python-socketio 5.7.2, simple-websocket 1.1.0). `simple-websocket` provides the actual WebSocket transport for Socket.IO's threading mode — without it the server falls back to HTTP polling with a startup warning.
- **Package to exe** (from `web/`): `python create_executable.py` — installs PyInstaller, generates `wind_monitor_new.spec` if missing, builds to `dist/WindSpeedMonitor/`. Alternative one-shot command is in `打包命令.txt` (`python -m PyInstaller wind_monitor_new.spec --clean`). Note: the generated spec `wind_monitor_new.spec` is the current one; the checked-in `WindSpeedMonitor.spec` is older (missing `serial_configs.json` data and some hidden imports) and is superseded.
- **Tests / lint**: none exist — no test suite, no linter config. Verification is manual: run the app, connect a serial port, confirm data flows to the dashboard and CSV.

The server binds `127.0.0.1:5000` and auto-opens the browser after ~2s. If port 5000 is occupied, startup fails with an explicit log message.

## Architecture

Data flow for one serial port:

```
Weather device → SerialCommunicator.read_line() (pyserial)
  → read loop assembles "#...#"-delimited frames from a shared buffer
  → DataParser.parse_data() → WindData object
  → DataStorage (in-memory deque + daily CSV append)
  → callback → WindMonitorManager.emit_data() → Socket.IO "wind_data" event → dashboard
```

- **Entry point** [realtime_wind_monitor.py](web/realtime_wind_monitor.py) — sets up logging (daily-rotating files in `logs/`, 30-day retention + cleanup), registers SIGINT/SIGTERM handlers and an `atexit` fallback cleanup, auto-opens the browser, then constructs `WindMonitorManager` and calls `start_server()`.
- **WindMonitorManager** [monitor_manager.py](web/modules/monitor_manager.py) — the central hub. Owns the `Flask` app + `SocketIO` (`async_mode='threading'`, CORS `*`), and a `readers: Dict[port, SerialWindDataReader]`. Holds `data_lock` (an `RLock`) guarding the readers dict. Lazily imports `WebRoutes` and `ConfigRoutes` (they take the manager in their constructor and register routes on `self.app`) — do not import them at module top level in this file, it's a circular-import trap.
- **SerialWindDataReader** [serial_reader.py](web/modules/serial_reader.py) — one instance per connected port; composes the pipeline (communicator + parser + storage). Defines `HEX_COMMAND` (a fixed 10-byte command sent to the device to request data). Each reader runs **three daemon threads** per port:
  1. *reconnect loop* — connects (retrying every 5s) and (re)starts the read thread;
  2. *command timer loop* — re-sends `HEX_COMMAND` every 1s (independent of data reads);
  3. *read loop* — reads, assembles frames, parses, stores, writes CSV, and calls the data callback.
- **SerialCommunicator** [serial_communicator.py](web/modules/serial_communicator.py) — thin wrapper over pyserial (connect/disconnect/send/read). `is_connected(quick_check=...)`: quick check only tests `is_open`; `quick_check=False` (deep) does a real write test — used by `add_reader` to detect "zombie" readers whose device vanished.
- **DataParser** [data_parser.py](web/modules/data_parser.py) — auto-detects the wire format and parses it into a `WindData`: hex-encoded (detected by length/char-set, converted to ASCII), JSON, CSV (comma), space-delimited (e.g. `01.1 112 +29.2 0993.9 +29.4 60 319 0000.0 CE*3B`), or bare numbers. Wind speed is range-checked to 0–100 m/s.
- **WindData** [data_model.py](web/modules/data_model.py) — plain data class: `timestamp, port, wind_speed, wind_direction, temperature, pressure, humidity`, with `to_dict()`/`from_dict()`.
- **DataStorage** [data_storage.py](web/modules/data_storage.py) — per-port storage. Keeps a `deque(maxlen=10000)` buffer (source for latest data / status), and appends every sample to a daily CSV `wind_data/<port>_YYYYMMDD.csv` with Chinese headers and UTF-8 BOM (so Excel opens it correctly). Handles date rollover (switches file at midnight), manual save (copies the daily file), merge helpers (`merge_daily_files`, `merge_all_port_data`), and 30-day cleanup.
- **WebRoutes** [web_routes.py](web/modules/web_routes.py) — the REST/control API: `/api/status`, `/api/add_port`, `/api/remove_port`, `/api/send_command`, `/api/save`, `/api/exit`, plus log/data list/merge/cleanup endpoints. `/api/exit` calls `os._exit()` (sys.exit is swallowed inside Flask routes). Note `/api/data/latest` reads `manager.readers` under `manager.data_lock`.
- **ConfigRoutes** [config_routes.py](web/modules/config_routes.py) — serial-config and diagnostics API: available ports, `diagnose_port`, config CRUD/validate/recommend, set port custom name.
- **serial_config_manager.py** — `SerialConfig` dataclass, `SerialConfigValidator`, and `SerialConfigManager` persisting to `web/serial_configs.json` (atomic write: temp file + `os.replace`; corrupt file gets backed up as `.corrupt` and defaults re-created). Exposes module-level singletons `config_manager` and `param_helper`. When frozen, the JSON lives under `sys._MEIPASS` — editing it post-packaging has no effect (bundled into the exe).
- **serial_diagnostics.py** — `SerialDiagnostics` (singleton `serial_diagnostics`): opens a port, tests for virtual-serial loopback, and watches for real data flow for 5s to produce a health report.
- **Frontend** [index.html](web/templates/index.html) — single page: Socket.IO client + Chart.js. Listens for `wind_data` (updates gauges + chart), `program_stopped`, `program_exit`; polls `/api/status` every 3s; all other actions use `fetch` to `/api/*`. `templates/full_test.html` is a standalone diagnostic/test page.

## Concurrency model

Multiple threads touch shared state; the locking rules are deliberate and commented:

- `manager.data_lock` (RLock) guards the readers dict. **Long-running operations — disconnect, thread joins — are performed OUTSIDE the lock** (see the comment in `add_reader`): the old reader is pulled from the dict under the lock, then disconnected without it, to avoid blocking other ports for seconds.
- Each reader has its own RLock (shared with its communicator) and a dedicated `_buffer_lock` guarding frame assembly.
- Buffer safety rails exist on purpose: the frame buffer is capped at 4096 chars with three degradation paths (see `P0-3`), and single `read()` calls cap `in_waiting` at 4096 to prevent OOM.
- A reader is considered connected via the **deep** `is_connected(quick_check=False)` check before `add_reader` decides a port is already connected.

## Conventions and gotchas

- **Working directory matters.** `logs/`, `wind_data/`, and writes to `serial_configs.json` are all relative to CWD, so always run from `web/`. Data/log file paths in the frontend API assume these exist next to the process.
- **`P0-x` / `P1-x` comment markers** throughout the code annotate prior bug fixes (e.g. `P0-2` sanitizes `custom_name` against path traversal, `P0-3` buffer overflow protection, `P1-8` malformed-frame detection, `P1-11` packaging `serial_configs.json`). Keep these markers and the behaviors they describe when editing.
- Methods uniformly wrap logic in try/except that logs `traceback.format_exc()` and returns a safe default — new methods should follow the same pattern. Logging is through `logging.getLogger(__name__)`.
- PyInstaller path handling: code checks `getattr(sys, 'frozen', False)` and uses `sys._MEIPASS` as the base path for `templates/` and `serial_configs.json` (see `WindMonitorManager.__init__` and `SerialConfigManager.__init__`). Any new bundled-resource path must do the same.
- The serial wire protocol is frame-delimited with `#...#`; parser field order is `wind_speed, wind_direction, temperature, pressure, <extra>, humidity` (index 5 = humidity, index 4 is skipped — the device sends two temperature-ish readings).
