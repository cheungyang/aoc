---
name: ha_config_audit
description: Audits Home Assistant health, error logs, unavailable entities, and automation integrity.
---

## Overview
This skill performs structured operational audits of the Home Assistant instance, scanning for integration errors, broken automations, missing entities, and offline hardware. It can be invoked on demand or run as a scheduled maintenance check.

## Workflows

### 1. Configuration Validation
- **Action**: Call `{"action": "check_config"}`.
- **Interpretation**: Validates that Home Assistant's configuration files are syntactically sound and integrations can load properly. If `result != "valid"`, treat as an immediate attention item.

### 2. Error Log Inspection
- **Action**: Call `{"action": "error_log"}`.
- **Analysis**:
  - Scan for repeated exceptions, connection timeouts to external APIs, or template rendering failures.
  - Separate benign warnings from critical failures (e.g. failing integrations or database corruption).

### 3. Entity & Device Health Check
- **Unavailable Entities**:
  - Call `{"action": "live_context"}` or `{"action": "search_registry", "include_disabled": false}`.
  - Filter for entities with `state: "unavailable"` or `state: "unknown"`.
  - Check whether entire devices or integrations are offline (e.g. battery-depleted Zigbee/Z-Wave sensors or disconnected Wi-Fi plugs).

### 4. Automation Audit
- **Action**: Call `{"action": "list_automations"}`.
- **Review**:
  - Identify automations that are turned off or disabled.
  - Cross-check referenced entity IDs against known entities to catch typos or references to deleted devices.

### 5. Producing the Audit Report
Format findings in an organized summary:
- **System Health**: Version, config check status, total entity count.
- **Offline / Unavailable Devices**: Grouped by area or domain.
- **Notable Errors**: High-signal items from the error log.
- **Action Items**: Concrete steps the user or Butler can take to resolve anomalies.
- If no issues are detected, deliver a succinct one-line confirmation: *"Home Assistant audit clean: config is valid, all devices healthy, no recent error logs."*
