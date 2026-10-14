"""Event log, report list, and Contact ID tests using synthetic panel HTML."""

from datetime import datetime
import importlib
from pathlib import Path
import sys
import types
import unittest

PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "trueguard_mz"
# Load parser.py and events.py without importing Home Assistant.
package = types.ModuleType("trueguard_pkg")
package.__path__ = [str(PACKAGE_PATH)]
sys.modules["trueguard_pkg"] = package
parser = importlib.import_module("trueguard_pkg.parser")
events = importlib.import_module("trueguard_pkg.events")

HISTORY = """
<h1>Hændelser</h1>
<table class=fm>
<tr><td>Tid</td><td>Enhed</td><td>Bruger</td><td>Hændelse</td></tr>
<tr><td>2026-01-02 10:00:05</td><td></td><td></td><td>Panel sabotage ok</td></tr>
<tr><td>2026-01-02 10:00:00</td><td></td><td></td><td>Panel sabotage</td></tr>
<tr><td>2026-01-02 09:00:00</td><td>Zone1 (Hoveddør)</td><td></td><td>Sabotage</td></tr>
<tr><td>2026-01-02 08:00:00</td><td>Zone2 (Bagdør)</td><td></td><td>Lavt batteri</td></tr>
<tr><td>2026-01-02 07:00:00</td><td></td><td>Bruger 1</td><td>Frakoblet</td></tr>
</table>
"""

REPORTS = """
<h1>Rapport hændelser</h1>
<table class=fm>
<tr><td>Tid</td><td>Hændelse</td><td>CID hændelse</td><td>Gruppe</td>
<td>Zone / Bruger</td><td>Status</td></tr>
<tr><td>2026-01-02 10:00:05</td><td>Panel sabotage ok</td><td>3137</td><td>0</td><td>0</td><td>Færdig</td></tr>
<tr><td>2026-01-02 10:00:00</td><td>Panel sabotage</td><td>1137</td><td>0</td><td>0</td><td>Færdig</td></tr>
<tr><td>2026-01-02 09:00:00</td><td>Sabotage</td><td>1383</td><td>1</td><td>1</td><td>Færdig</td></tr>
<tr><td>2026-01-02 08:00:00</td><td>Batteri lav</td><td>1384</td><td>1</td><td>2</td><td>Færdig</td></tr>
</table>
"""

DEVICES = {
    1: parser.PanelDevice(zone=1, device_type="Dørkontakt", name="Hoveddør"),
    2: parser.PanelDevice(zone=2, device_type="Dørkontakt", name="Bagdør"),
}


def report(time, cid, zone, event="x"):
    return parser.ReportEvent(time=time, event=event, cid=cid, group=1, zone=zone)


class ParseTests(unittest.TestCase):
    def test_history(self):
        rows = parser.parse_history_page(HISTORY)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[2].zone, 1)
        self.assertEqual(rows[2].event, "Sabotage")
        self.assertIsNone(rows[0].zone)
        self.assertEqual(rows[4].user, "Bruger 1")

    def test_reports(self):
        rows = parser.parse_report_page(REPORTS)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[3].cid, "1384")
        self.assertEqual(rows[3].qualifier, "1")
        self.assertEqual(rows[3].code, "384")
        self.assertEqual(rows[3].zone, 2)

    def test_empty_report_page(self):
        html = """<table class=fm><tr><td>Tid</td><td>Hændelse</td>
        <td>CID hændelse</td><td>Gruppe</td><td>Zone / Bruger</td><td>Status</td></tr>
        <tr><td colspan=6>Ingen enheder fundet</td></tr></table>"""
        self.assertEqual(parser.parse_report_page(html), ())

    def test_info_and_gsm(self):
        info = parser.parse_info_page(
            "<table><tr><td>Firmware revision:</td><td>MZ-8_F 1.0</td></tr>"
            "<tr><td>ZigBee revision:</td><td>3.4</td></tr></table>"
        )
        self.assertEqual(info.firmware, "MZ-8_F 1.0")
        self.assertEqual(info.zigbee, "3.4")
        self.assertEqual(
            parser.parse_gsm_page(
                "<table><tr><td>Status:</td><td>SIM ikke monteret </td></tr></table>"
            ),
            "SIM ikke monteret",
        )


class StateTests(unittest.TestCase):
    def setUp(self):
        self.data = parser.PanelData(
            alarm_state="Frakoblet",
            devices=DEVICES,
            history=parser.parse_history_page(HISTORY),
            reports=parser.parse_report_page(REPORTS),
        )

    def test_zone_flags(self):
        self.assertTrue(events.zone_tamper(self.data, 1))
        self.assertFalse(events.zone_tamper(self.data, 2))
        self.assertTrue(events.zone_battery_low(self.data, 2))
        self.assertFalse(events.zone_battery_low(self.data, 1))
        self.assertFalse(events.zone_supervision_lost(self.data, 1))

    def test_panel_tamper_restored(self):
        self.assertFalse(events.panel_tamper(self.data))

    def test_column_overrides_reports(self):
        devices = {
            2: parser.PanelDevice(
                zone=2, device_type="Dørkontakt", name="Bagdør", battery="OK"
            )
        }
        data = parser.PanelData("Frakoblet", devices=devices, reports=self.data.reports)
        self.assertFalse(events.zone_battery_low(data, 2))

    def test_log_fallback_without_reports(self):
        data = parser.PanelData("Frakoblet", devices=DEVICES, history=self.data.history)
        self.assertTrue(events.zone_battery_low(data, 2))
        self.assertTrue(events.zone_tamper(data, 1))

    def test_newer_log_restore_beats_older_report(self):
        history = (
            parser.LogEntry("2026-01-02 09:30:00", "Zone1 (Hoveddør)", "", "Sabotage ok", 1),
        ) + self.data.history
        data = parser.PanelData(
            "Frakoblet", devices=DEVICES, history=history, reports=self.data.reports
        )
        self.assertFalse(events.zone_tamper(data, 1))

    def test_panel_restart_clears_old_flags(self):
        restart = parser.LogEntry("2026-01-02 11:00:00", "", "", "Enhed tændt", None)
        tamper = parser.LogEntry("2026-01-02 10:59:00", "", "", "Panel sabotage", None)
        history = (restart, tamper) + self.data.history
        data = parser.PanelData(
            "Frakoblet", devices=DEVICES, history=history, reports=self.data.reports
        )
        self.assertFalse(events.zone_battery_low(data, 2))
        self.assertFalse(events.zone_tamper(data, 1))
        self.assertFalse(events.panel_tamper(data))
        self.assertEqual(events.last_power_up(data), "2026-01-02 11:00:00")

    def test_active_fault_names_zone(self):
        data = parser.PanelData(
            "Frakoblet",
            active_faults=("Bagdør Lavt batteri",),
            devices=DEVICES,
        )
        self.assertTrue(events.zone_battery_low(data, 2))
        self.assertFalse(events.zone_battery_low(data, 1))

    def test_changed_by_and_last_event(self):
        self.assertEqual(events.last_changed_by(self.data), "Bruger 1")
        self.assertEqual(events.zone_last_event(self.data, 2).event, "Lavt batteri")
        self.assertEqual(
            events.describe(self.data.history[2]), "Zone1 (Hoveddør): Sabotage"
        )


class TriggeredTests(unittest.TestCase):
    now = datetime(2026, 1, 2, 12, 0, 0)

    def data(self, *reports):
        return parser.PanelData("Fuldsikring", devices=DEVICES, reports=reports)

    def test_burglary_while_armed(self):
        data = self.data(report("2026-01-02 11:59:00", "1130", 1, "Indbrud"))
        self.assertTrue(events.alarm_is_triggered(data, self.now, armed=True))
        self.assertFalse(events.alarm_is_triggered(data, self.now, armed=False))
        self.assertEqual(events.last_alarm(data).event, "Indbrud")

    def test_disarm_report_ends_alarm(self):
        data = self.data(
            report("2026-01-02 11:59:30", "1401", 1),
            report("2026-01-02 11:59:00", "1130", 1),
        )
        self.assertFalse(events.alarm_is_triggered(data, self.now, armed=True))

    def test_alarm_expires(self):
        data = self.data(report("2026-01-02 10:00:00", "1130", 1))
        self.assertFalse(events.alarm_is_triggered(data, self.now, armed=True))

    def test_fire_alarm_while_disarmed_until_restore(self):
        fire = report("2026-01-02 11:59:00", "1111", 2, "Brand")
        self.assertTrue(events.alarm_is_triggered(self.data(fire), self.now, armed=False))
        restored = self.data(report("2026-01-02 11:59:40", "3111", 2), fire)
        self.assertFalse(events.alarm_is_triggered(restored, self.now, armed=False))

    def test_alarm_before_panel_restart_is_over(self):
        data = parser.PanelData(
            "Fuldsikring",
            devices=DEVICES,
            history=(parser.LogEntry("2026-01-02 11:59:30", "", "", "Enhed tændt", None),),
            reports=(report("2026-01-02 11:59:00", "1130", 1),),
        )
        self.assertFalse(events.alarm_is_triggered(data, self.now, armed=True))

    def test_tamper_is_not_an_alarm(self):
        data = self.data(report("2026-01-02 11:59:00", "1383", 1))
        self.assertFalse(events.alarm_is_triggered(data, self.now, armed=True))
        self.assertIsNone(events.last_alarm(data))


if __name__ == "__main__":
    unittest.main()
