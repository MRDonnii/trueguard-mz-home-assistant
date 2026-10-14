"""Set up the integration against synthetic panel pages."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.trueguard_mz.const import DOMAIN, EVENT_TRUEGUARD
from custom_components.trueguard_mz.diagnostics import (
    async_get_config_entry_diagnostics,
)

HOST = "http://192.0.2.10"

CONTROL = """<h1>Panel styring</h1><form>Panel tilstand:Fuldsikring<br/>
<table class=fm><tr><td>Aktive fejl</td><td></td></tr>
<tr><td colspan=2 class=ct>Ingen enheder fundet</td></tr></table></form>"""

DEVICE = """<table class=fm>
<tr><td>Index</td><td>Type</td><td>Navn</td><td>Attribute</td><td>Tilstand</td>
<td>Batteri</td><td>Sabotage</td><td>Deaktiver enhed</td><td>Signal</td><td>Status</td><td></td></tr>
<tr><td>1</td><td>Dørkontakt</td><td>Hoveddør</td><td>Indgang1</td><td></td><td></td>
<td></td><td></td><td></td><td></td><td>Ændre</td></tr>
<tr><td>2</td><td>Røgalarm</td><td>Gang</td><td>Brand</td><td></td><td></td>
<td></td><td></td><td>7</td><td></td><td>Ændre</td></tr>
</table>"""

HISTORY = """<table class=fm><tr><td>Tid</td><td>Enhed</td><td>Bruger</td><td>Hændelse</td></tr>
<tr><td>2026-01-02 09:00:00</td><td>Zone1 (Hoveddør)</td><td></td><td>Sabotage</td></tr>
<tr><td>2026-01-02 08:00:00</td><td></td><td>Bruger 1</td><td>Fuldsikring</td></tr>
</table>"""

HISTORY_NEW = HISTORY.replace(
    "<tr><td>2026-01-02 09:00:00",
    "<tr><td>2026-01-02 09:30:00</td><td>Zone1 (Hoveddør)</td><td></td>"
    "<td>Sabotage ok</td></tr><tr><td>2026-01-02 09:00:00",
)

REPORTS = """<table class=fm><tr><td>Tid</td><td>Hændelse</td><td>CID hændelse</td>
<td>Gruppe</td><td>Zone / Bruger</td><td>Status</td></tr>
<tr><td>2026-01-02 09:00:00</td><td>Sabotage</td><td>1383</td><td>1</td><td>1</td><td>Færdig</td></tr>
<tr><td>2026-01-02 07:00:00</td><td>Indbrud</td><td>1130</td><td>1</td><td>1</td><td>Færdig</td></tr>
</table>"""

INDEX = """<table><tr><td>Firmware revision:</td><td>MZ-TEST 1.0</td></tr>
<tr><td>MAC Adresse:</td><td>00:1D:94:00:00:01</td></tr></table>"""

GSM = "<table><tr><td>Status:</td><td>SIM ikke monteret</td></tr></table>"


def _mock_pages(aioclient_mock, history=HISTORY):
    aioclient_mock.clear_requests()
    for page, body in (
        ("control.htm", CONTROL),
        ("device.htm", DEVICE),
        ("history.htm", history),
        ("rptevent.htm", REPORTS),
        ("index.htm", INDEX),
        ("gsm.htm", GSM),
    ):
        aioclient_mock.get(f"{HOST}/{page}", text=body)


async def test_setup_entities_and_events(hass, aioclient_mock):
    _mock_pages(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="TrueGuard MZ",
        unique_id="00:1d:94:00:00:01",
        data={"host": HOST, "username": "u", "password": "p", "scan_interval": 10},
        options={"event_interval": 2},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    alarm = hass.states.get("alarm_control_panel.trueguard_mz")
    assert alarm.state == "armed_away"  # alarm at 07:00 is older than one hour
    assert alarm.attributes["last_alarm"] == "Indbrud"
    assert alarm.attributes["last_alarm_zone_name"] == "Hoveddør"
    assert alarm.attributes["changed_by"] == "Bruger 1"

    assert hass.states.get("binary_sensor.hoveddor").state == "off"
    assert hass.states.get("binary_sensor.gang").attributes["device_class"] == "smoke"
    assert hass.states.get("binary_sensor.hoveddor_sabotage").state == "on"
    assert hass.states.get("binary_sensor.hoveddor_batteri").state == "off"
    assert hass.states.get("binary_sensor.hoveddor_forbindelse").state == "on"
    assert hass.states.get("binary_sensor.trueguard_mz_sabotage").state == "off"
    assert hass.states.get("sensor.hoveddor_sidste_haendelse").state == "Sabotage"
    assert hass.states.get("sensor.hoveddor_signal").state == "unknown"
    assert hass.states.get("sensor.gang_signal").state == "7"
    assert (
        hass.states.get("sensor.trueguard_mz_sidste_haendelse").state
        == "Zone1 (Hoveddør): Sabotage"
    )
    assert hass.states.get("sensor.trueguard_mz_seneste_alarm").state == "Indbrud – Hoveddør"
    assert hass.states.get("sensor.trueguard_mz_gsm").state == "SIM ikke monteret"

    from homeassistant.helpers import device_registry as dr

    panel = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert panel.sw_version == "MZ-TEST 1.0"

    fired = []
    hass.bus.async_listen(EVENT_TRUEGUARD, fired.append)
    _mock_pages(aioclient_mock, HISTORY_NEW)
    coordinator = entry.runtime_data.coordinator
    coordinator._events_read_at = None  # force the event log to be read now
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert [event.data["event"] for event in fired] == ["Sabotage ok"]
    assert fired[0].data["zone_name"] == "Hoveddør"
    assert hass.states.get("binary_sensor.hoveddor_sabotage").state == "off"

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"]["password"] == "**REDACTED**"
    assert diagnostics["info"]["mac"] == "**REDACTED**"
    assert len(diagnostics["history"]) == 3


async def test_follows_panel_names_and_zones(hass, aioclient_mock):
    _mock_pages(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="TrueGuard MZ",
        data={"host": HOST, "username": "u", "password": "p", "scan_interval": 10},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    ident = {(DOMAIN, f"{entry.entry_id}_zone_1")}
    assert registry.async_get_device(identifiers=ident).name == "Hoveddør"

    # Renamed on the panel: the device and friendly names follow without a reload.
    aioclient_mock.clear_requests()
    renamed = DEVICE.replace("Hoveddør", "Fordør")
    for page, body in (
        ("control.htm", CONTROL), ("device.htm", renamed), ("history.htm", HISTORY),
        ("rptevent.htm", REPORTS), ("index.htm", INDEX), ("gsm.htm", GSM),
    ):
        aioclient_mock.get(f"{HOST}/{page}", text=body)
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert registry.async_get_device(identifiers=ident).name == "Fordør"
    assert hass.states.get("binary_sensor.hoveddor_batteri").attributes[
        "friendly_name"
    ] == "Fordør Batteri"

    # A new zone on the panel: the entry reloads and creates its entities.
    aioclient_mock.clear_requests()
    added = renamed.replace(
        "</table>",
        "<tr><td>3</td><td>PIR</td><td>Stue</td><td>Indbrud</td><td></td><td></td>"
        "<td></td><td></td><td>5</td><td></td><td>Ændre</td></tr></table>",
    )
    for page, body in (
        ("control.htm", CONTROL), ("device.htm", added), ("history.htm", HISTORY),
        ("rptevent.htm", REPORTS), ("index.htm", INDEX), ("gsm.htm", GSM),
    ):
        aioclient_mock.get(f"{HOST}/{page}", text=body)
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.stue").attributes["device_class"] == "motion"
