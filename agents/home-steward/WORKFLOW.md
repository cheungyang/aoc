# home-steward: how its tools work

## home_assistant

One call carries a list of instructions, run in order. Each one stands alone:
- what it produced (a result, or a request for the user's confirmation) goes in the payload;
- a refusal or a failure is listed under issues, with the reason, and the rest of the batch still runs.

The status is `ok` when every instruction was carried out, `partial` when some weren't, and `failed` when none was.

### Permissions, per instruction

Every instruction is checked against the agent's grant in agent.json: actions, per entity selector. A selector covers:
- `*`: every entity;
- `light.*`, or the bare domain `light`: every light;
- `light.porch`: that entity only.

An instruction is judged by the entity it names (`entity_id`, or `target.entity_id`), else its `domain`, else the automation, script or scene it names (`automation.<id>`), else `*`, which only a `*` selector covers. Reads (`@observe`) need only this check, and run at once.

### Writes: what applies at once, what needs confirmation, what's refused

A write passes these gates in order; the first that says no decides.

1. **Shape**, before any confirmation, so the user never approves a change that can't land. Refused: a key the action doesn't take (an automation goes under `config`); a config without its triggers and actions (a script: `sequence`; a scene: `entities`); a script without `id`; a delete without `id`; a service call naming several entities, or an area, device or label; the entity named twice; a `domain` that doesn't match the entity; and `upsert_helper`, which isn't available yet.
2. **Permission**: the action must be granted on the entity (above).
3. **Kill switch**: nothing is written unless `HA_WRITE_ENABLED=true` is set in .env.
4. **Refusals no approval can lift**:
   - a blanket grant (`{}`, `[]`, or a list holding `*`): writes need their actions listed per entity selector;
   - the deny-list: lock, alarm_control_panel, cover, garage_door, valve and water_heater, as the target or named anywhere in a config. It's in code, so no grant lifts it;
   - a climate setpoint outside 12–28 °C.
5. **Confirmation**: config writes (create, change or delete an automation, script or scene; reload) always need the user's approval, and so do scripts and automations called as services, since what they do is opaque. A service call on a reversible domain applies at once. A domain on neither list needs confirmation.

A write that needs confirmation returns a confirm_token instead of applying. Once the user approves, the agent sends the same instruction again with the token. A token works once, for 5 minutes, for the agent it was issued to, and only for that exact instruction: a changed payload invalidates it. Every config write saves the old version first and reads the result back; one that doesn't read back as written is rolled back.

Gates 4 and 5, in words (`verdict`, the same checks the tool runs once writing is on):

```python
>>> from agents.home_steward.lib.homeassistant.guards import (
...     AUTO_APPLY_DOMAINS, BLOCKED_DOMAINS, CLIMATE_SAFE_BAND_C, CONFIRMATION_TTL_SECONDS,
...     is_blanket_grant, verdict)
>>> verdict("call_service", "light.porch")
'applies at once'
>>> verdict("call_service", "climate.hall", {"service_data": {"temperature": 20}})
'applies at once'
>>> verdict("call_service", "script.goodnight")
'needs confirmation'
>>> verdict("call_service", "vacuum.roomba")
'needs confirmation'
>>> verdict("upsert_automation", "automation.porch_sunset",
...         {"config": {"triggers": [{"trigger": "sun", "event": "sunset"}],
...                     "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]}})
'needs confirmation'
>>> verdict("reload", "automation")
'needs confirmation'
```

The reversible domains, which apply at once:

```python
>>> sorted(AUTO_APPLY_DOMAINS)
['button', 'climate', 'fan', 'humidifier', 'input_boolean', 'input_datetime', 'input_number',
 'input_select', 'light', 'media_player', 'notify', 'number', 'scene', 'select', 'switch', 'timer', 'todo']
```

Refused, whatever the user says:

```python
>>> sorted(BLOCKED_DOMAINS)
['alarm_control_panel', 'cover', 'garage_door', 'lock', 'valve', 'water_heater']
>>> print(verdict("call_service", "lock.front_door"))
refused: 'lock' is on the permanent deny-list (physical safety) and cannot be actuated by an agent,
with or without confirmation. ...
>>> print(verdict("upsert_automation", "automation.dusk", {"config": {"actions": [{"action": "lock.unlock"}]}}))
refused: This payload references 'lock.unlock' at 'config.actions[0].action', which is in the blocked
'lock' domain. ...
>>> CLIMATE_SAFE_BAND_C
(12.0, 28.0)
>>> print(verdict("call_service", "climate.hall", {"service_data": {"temperature": 81}}))
refused: climate 'temperature' of 81.0degC is outside the safe band 12.0-28.0degC and was refused.
>>> print(verdict("call_service", "light.porch", blanket_grant=True))
refused: home_assistant requires explicit per-action grants for writes; a blanket '{}' grant is refused. ...
```

What counts as a blanket grant:

```python
>>> is_blanket_grant({})
True
>>> is_blanket_grant(["*"])
True
>>> is_blanket_grant({"*": ["@observe"], "light.*": ["@control"]})
False
```

A confirmation token lasts 5 minutes:

```python
>>> CONFIRMATION_TTL_SECONDS
300
```

### Reads

Reads go straight to Home Assistant and come back as it answers, except for these trims:
- `inventory` is a fixed-size summary: entities per area and domain, with totals.
- `search_registry` and `list_entities` stop at `limit` and report the total before the cut.
- `list_services` without a `domain` lists the domain names only.
- `get_config` keeps the fields worth reasoning about and counts the components; `full` returns everything.
- `error_log` folds repeated entries into one with a count, newest first, within a size cap.
- `history` and `logbook` cover the last `hours` (24 by default).

The registries are fetched at most once per call.

## device_list

Your device list is `pkm/wallet/home/DEVICES.md`, in the user's PKM, so device names never reach this repository. The tool writes the file on its first call, when there is none yet, and on a refresh (`refresh=true`, which the weekly inventory passes). Every other call reads the file back as saved, without asking Home Assistant.

### Which devices

A refresh reads Home Assistant's live states and its entity, device and area registries, then keeps an entity when:
- its domain is light, switch, climate, fan or media_player (`INDEX_DOMAINS`), the ones a spoken request actuates; covers and locks are on the deny-list;
- it has a state, and the state isn't `unavailable` (an entity with no state is disabled or not loaded);
- the registry doesn't mark it as a device setting (`entity_category`, such as a plug's LED), hidden or disabled;
- the agent may control it: its `home_assistant` grant allows `call_service` on the entity, the check `home_assistant` makes before a write. A grant with no `call_service` at all, a blanket grant (`{}`) or a list grant keeps nothing.

Its area is the entity's own, else its device's; its name is the friendly name Home Assistant shows. Entities are sorted by area, those with no area last, then by entity id. States aren't kept: they change by the minute, and `get_state` reads them live.

```python
>>> from agents.home_steward.lib.homeassistant.entity_index import Entry, build
>>> build(
...     entities=[{"entity_id": "light.porch", "area_id": "porch"},
...               {"entity_id": "switch.porch_plug_led", "device_id": "plug_1", "entity_category": "config"},
...               {"entity_id": "switch.coffee_maker", "device_id": "plug_2"}],
...     devices=[{"id": "plug_1", "area_id": "porch"}, {"id": "plug_2", "area_id": "kitchen"}],
...     areas=[{"area_id": "porch", "name": "Porch"}, {"area_id": "kitchen", "name": "Kitchen"}],
...     states=[{"entity_id": "light.porch", "state": "off", "attributes": {"friendly_name": "Porch light"}},
...             {"entity_id": "switch.porch_plug_led", "state": "on"},
...             {"entity_id": "switch.coffee_maker", "state": "off", "attributes": {"friendly_name": "Coffee maker"}},
...             {"entity_id": "light.attic", "state": "unavailable"},
...             {"entity_id": "sensor.porch_temperature", "state": "18.5"}])
[Entry(area='Kitchen', entity_id='switch.coffee_maker', name='Coffee maker'),
 Entry(area='Porch', entity_id='light.porch', name='Porch light')]
```

### The file

Plain markdown, so it reads well in Obsidian: a first line saying the tool writes it and refreshes it weekly, so edits are overwritten; when it was refreshed; then a heading per area ("No area" last) and a line per device, its entity id and then its name:

```python
>>> from datetime import datetime
>>> from agents.home_steward.lib.homeassistant.entity_index import parse_file, render, render_file
>>> devices = [Entry("Kitchen", "switch.plug_3", "Coffee maker"),
...            Entry("Living Room", "light.living_room_lamp", "Living room lamp"),
...            Entry("Living Room", "media_player.living_room_tv", "Living room TV"),
...            Entry("", "switch.yard_lights", "Yard lights")]
>>> text = render_file(devices, datetime(2026, 10, 18, 20, 0))
>>> print(text, end="")
Written by home-steward's device_list and refreshed weekly, so edits are overwritten.
<BLANKLINE>
Refreshed: 2026-10-18 20:00
<BLANKLINE>
## Kitchen
- switch.plug_3: Coffee maker
<BLANKLINE>
## Living Room
- light.living_room_lamp: Living room lamp
- media_player.living_room_tv: Living room TV
<BLANKLINE>
## No area
- switch.yard_lights: Yard lights
```

### What it returns

The file read back: `refreshed`, when the list was fetched, and `devices`, one line per area, each entity id followed by its name in quotes only when the id doesn't already say it (a word of the name is missing from the id):

```python
>>> saved = parse_file(text)
>>> saved.refreshed
'2026-10-18 20:00'
>>> print(render(saved.entries))
Kitchen: switch.plug_3 "Coffee maker"
Living Room: light.living_room_lamp, media_player.living_room_tv
No area: switch.yard_lights
```

At most 200 devices are listed (`MAX_ENTRIES`); a last line counts the rest, and `search_registry` finds them.

A line the tool doesn't recognize, such as a note added by hand, comes back raw under issues, and the status is partial; the rest is still listed. A file with no Refreshed line is partial too.

```python
>>> parse_file(text + "Patio plug: broken\n").unrecognized
['Patio plug: broken']
```

### Failures

- The write is checked against the agent's grant before Home Assistant is asked; a refused write changes nothing.
- A refresh that can't reach Home Assistant leaves the file as it was and fails, with Home Assistant's error as the issue.
- No file yet and no Home Assistant: it fails, saying to look devices up with `search_registry`.
