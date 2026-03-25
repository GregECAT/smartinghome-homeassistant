---
description: How to persist data permanently in the Smarting HOME integration (settings.json)
---

# Data Persistence — Smarting HOME

All persistent user/system data in the Smarting HOME integration is stored in a single JSON file:

```
/config/www/smartinghome/settings.json
```

## Architecture

A centralized I/O module handles all reads and writes:

```
custom_components/smartinghome/settings_io.py
```

It provides **thread-safe**, **atomic** file operations using:
- `threading.Lock()` — serializes all concurrent writers
- Atomic `write → .tmp → rename` — prevents partial/corrupt reads

## API Reference

```python
from .settings_io import read_sync, write_sync, write_async, get_path
```

| Function | Context | Description |
|----------|---------|-------------|
| `read_sync(hass)` | sync (executor) | Returns full `dict` from settings.json |
| `write_sync(hass, updates)` | sync (executor) | Merges `updates` dict into settings.json |
| `write_async(hass, updates)` | async (event loop) | Async wrapper around `write_sync` |
| `get_path(hass)` | any | Returns `Path` to settings.json |

## Usage Patterns

### Pattern 1: Writing from an async service handler

```python
from .settings_io import write_sync

async def handle_my_service(call):
    updates = {"my_key": call.data["value"]}
    await hass.async_add_executor_job(write_sync, hass, updates)
```

### Pattern 2: Writing from a sync method (executor context)

```python
from .settings_io import write_sync

def _my_sync_writer(self):
    write_sync(self.hass, {"my_key": "my_value"})
```

### Pattern 3: Using write_async directly

```python
from .settings_io import write_async

async def _save_state(self):
    await write_async(self.hass, {"state_key": self._state})
```

### Pattern 4: Reading settings on startup / restore

```python
from .settings_io import read_sync

async def restore_state(self):
    settings = await self.hass.async_add_executor_job(read_sync, self.hass)
    saved_value = settings.get("my_key", "default")
```

### Pattern 5: Fire-and-forget from a sync method

```python
# Use hass.async_create_task to persist without blocking
def toggle_something(self):
    self._state = not self._state
    self.hass.async_create_task(self._persist())

async def _persist(self):
    from .settings_io import write_async
    await write_async(self.hass, {"my_state": self._state})
```

## Rules

1. **NEVER** write directly to settings.json with `Path.write_text()` or `json.dump()`
2. **ALWAYS** import from `settings_io` — it guarantees thread safety and atomicity
3. **Use `write_sync`** from executor/sync contexts, **`write_async`** from async contexts
4. **Merge, don't overwrite** — `write_sync` does a read→merge→write internally
5. **Use lazy imports** (`from .settings_io import ...` inside methods) to avoid circular imports

## Frontend Persistence (panel.js)

The frontend reads settings.json via HTTP fetch and writes via HA services:

- **Load**: `_loadSettings()` fetches `/local/smartinghome/settings.json`
- **Save**: `_savePanelSettings()` calls `smartinghome.save_panel_settings` service
- **Key status**: After successful API key test, call `this._savePanelSettings()` to persist

## What Gets Persisted

| Key | Writer | Description |
|-----|--------|-------------|
| `autopilot_active_strategy` | strategy_controller | Active strategy enum value |
| `autopilot_enabled` | strategy_controller | Whether autopilot is on |
| `autopilot_active_action_ids` | strategy_controller | List of enabled action IDs |
| `autopilot_disabled_action_ids` | strategy_controller | List of disabled action IDs |
| `autopilot_live` | coordinator | Live autopilot tick data |
| `gemini_key_status` | config_flow / panel.js | API key verification status |
| `anthropic_key_status` | config_flow / panel.js | API key verification status |
| `ecowitt_*` | services | Ecowitt integration settings |
| `sub_meters` | services | Sub-meter configurations |
| `winter_*` | services | Winter tab data |
| `ai_strategic_plan` | strategy_controller | AI 24h strategic plan |
| `ai_cron_*` | cron_scheduler | AI cron job results |
| `inverter_capabilities` | inverter_agent | Detected inverter features |
