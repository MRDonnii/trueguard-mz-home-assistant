"""Data update coordinator for TrueGuard MZ."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TrueGuardAuthError, TrueGuardClient, TrueGuardConnectionError
from .const import (
    AUTO_BACKUP_EVERY,
    BACKUPS_TO_KEEP,
    CONF_AUTO_BACKUP,
    CONF_SAVE_IMAGES,
    EVENT_TRUEGUARD_IMAGE,
    CONF_EVENT_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_EVENT_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    EVENT_TRUEGUARD,
    EVENT_TRUEGUARD_REPORT,
    GSM_INTERVAL_SECONDS,
    SETTINGS_INTERVAL_SECONDS,
)
from .events import (
    CODE_TEXT,
    is_camera,
    arm_mode,
    entry_delay,
    exit_delay,
    parse_panel_time,
    zone_is_open,
    zone_name,
)
from .parser import Capture, LogEntry, PanelData, PanelInfo, ReportEvent
from .receiver import PushReport

# Push reports are kept until the panel's own report list shows them.
PUSH_KEEP = timedelta(minutes=10)
PUSH_MATCH = timedelta(minutes=2)

_LOGGER = logging.getLogger(__name__)


class TrueGuardCoordinator(DataUpdateCoordinator[PanelData]):
    """Poll the panel pages once for all entities.

    control.htm and device.htm are read on every poll. The event log and the
    report list are read every event interval, and at once when the arm
    state or a zone changes, so the cause of a change shows up quickly.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TrueGuardClient
    ) -> None:
        self.client = client
        self.info = PanelInfo()
        interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        self._event_interval = entry.options.get(
            CONF_EVENT_INTERVAL, DEFAULT_EVENT_INTERVAL
        )
        self._events_read_at: float | None = None
        self._gsm_read_at: float | None = None
        self._settings_read_at: float | None = None
        # Countdowns derived from state changes and the panel's area times.
        self.arming_until: datetime | None = None
        self.entry_until: datetime | None = None
        # Reports pushed by the panel (receiver.py).
        self.last_push: PushReport | None = None
        self.last_push_at: datetime | None = None
        self._push_reports: list[ReportEvent] = []
        self._seen_log: Counter[LogEntry] | None = None
        self._programming_seen: str | None = None
        self.last_backup: datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="TrueGuard MZ",
            update_interval=timedelta(seconds=interval),
            always_update=False,
        )

    async def _async_setup(self) -> None:
        """Read static panel information once."""
        try:
            self.info = await self.client.async_get_info()
        except (TrueGuardConnectionError, ValueError) as err:
            _LOGGER.debug("Could not read TrueGuard panel info: %s", err)

    async def _async_update_data(self) -> PanelData:
        try:
            data = await self.client.async_get_data()
        except TrueGuardAuthError as err:
            raise ConfigEntryAuthFailed("TrueGuard login was rejected") from err
        except (TrueGuardConnectionError, ValueError) as err:
            raise UpdateFailed(f"Error communicating with TrueGuard: {err}") from err

        previous = self.data
        now = time.monotonic()
        history = previous.history if previous else ()
        reports = previous.reports if previous else ()
        gsm_status = previous.gsm_status if previous else None
        gsm_details = previous.gsm_details if previous else ()
        captures = previous.captures if previous else ()

        changed = previous is None or (
            data.alarm_state,
            data.active_faults,
            data.devices,
        ) != (previous.alarm_state, previous.active_faults, previous.devices)
        if (
            changed
            or self._events_read_at is None
            or now - self._events_read_at >= self._event_interval
        ):
            try:
                history, reports = await self.client.async_get_events()
            except TrueGuardAuthError as err:
                raise ConfigEntryAuthFailed("TrueGuard login was rejected") from err
            except (TrueGuardConnectionError, ValueError) as err:
                _LOGGER.debug("Could not read the TrueGuard event log: %s", err)
            else:
                self._events_read_at = now
                if self.has_cameras(data):
                    try:
                        captures = await self.client.async_get_captures()
                    except (TrueGuardConnectionError, ValueError) as err:
                        _LOGGER.debug("Could not read TrueGuard pictures: %s", err)
                if self._programming_finished(reports):
                    self._settings_read_at = None  # settings may have changed

        if self._gsm_read_at is None or now - self._gsm_read_at >= GSM_INTERVAL_SECONDS:
            try:
                gsm_status, details = await self.client.async_get_gsm()
                gsm_details = tuple(
                    (key, value)
                    for key, value in details
                    if key not in ("imei", "imsi") and "apn" not in key
                )
            except (TrueGuardConnectionError, ValueError) as err:
                _LOGGER.debug("Could not read the TrueGuard GSM status: %s", err)
            else:
                self._gsm_read_at = now

        settings = previous.settings if previous else None
        panel_clock = previous.panel_clock if previous else None
        clock_offset = previous.clock_offset if previous else None
        panel_settings = previous.panel_settings if previous else None
        if (
            self._settings_read_at is None
            or now - self._settings_read_at >= SETTINGS_INTERVAL_SECONDS
        ):
            try:
                (
                    settings,
                    panel_clock,
                    panel_settings,
                ) = await self.client.async_get_settings()
            except (TrueGuardConnectionError, ValueError) as err:
                _LOGGER.debug("Could not read TrueGuard settings: %s", err)
            else:
                self._settings_read_at = now
                clock_offset = _clock_offset(panel_clock)

        data = replace(
            data,
            history=history,
            reports=self._merge_push(reports),
            gsm_status=gsm_status,
            settings=settings,
            panel_clock=panel_clock,
            clock_offset=clock_offset,
            panel_settings=panel_settings,
            gsm_details=gsm_details,
            captures=captures,
        )
        self._track_countdowns(previous, data)
        self._fire_new_log_entries(data)
        self._handle_new_captures(previous, data)
        await self._async_auto_backup()
        return data

    def request_events_soon(self) -> None:
        """Read the event pages (and pictures) on the next polls for a while."""
        self._events_read_at = None
        for delay in (5, 15, 30):
            async_call_later(self.hass, delay, self._async_force_events)

    async def _async_force_events(self, _now) -> None:
        self._events_read_at = None
        await self.async_request_refresh()

    @staticmethod
    def has_cameras(data: PanelData) -> bool:
        """Return True when a zone is a PIR camera."""
        return any(is_camera(device.device_type) for device in data.devices.values())

    def _programming_finished(self, reports: tuple[ReportEvent, ...]) -> bool:
        """Return True once per new 'programming finished' report (CID 628)."""
        newest = next((r.time for r in reports if r.code == "628"), None)
        if newest is None or newest == self._programming_seen:
            return False
        first = self._programming_seen is None
        self._programming_seen = newest
        return not first

    def _handle_new_captures(self, previous: PanelData | None, data: PanelData) -> None:
        """Fire an event (and optionally save the files) for new camera pictures."""
        if previous is None or not data.captures:
            return
        known = set(previous.captures)
        for capture in reversed(data.captures):
            if capture in known:
                continue
            self.hass.bus.async_fire(
                EVENT_TRUEGUARD_IMAGE,
                {
                    "time": capture.time,
                    "device": capture.device,
                    "zone": capture.zone,
                    "zone_name": zone_name(data, capture.zone)
                    if capture.zone is not None
                    else None,
                    "kind": capture.kind,
                    "links": list(capture.links),
                    "config_entry_id": self.config_entry.entry_id,
                },
            )
            if self.config_entry.options.get(CONF_SAVE_IMAGES):
                self.hass.async_create_task(self._async_save_capture(capture))

    async def async_capture_image(self, capture: Capture) -> tuple[bytes, str] | None:
        """Download the first picture of a capture row."""
        for link in sorted(capture.links, key=lambda link: not _looks_like_image(link)):
            try:
                content, content_type = await self.client.async_fetch(link)
            except (TrueGuardConnectionError, ValueError) as err:
                _LOGGER.debug("Could not fetch TrueGuard picture %s: %s", link, err)
                continue
            if content_type.startswith("image/") or _looks_like_image(link):
                return content, content_type or "image/jpeg"
            # A viewer page: follow its first picture.
            text = content.decode("utf-8", errors="replace")
            if match := re.search(r'<img[^>]+src="([^"]+)"', text, re.I):
                try:
                    picture, kind = await self.client.async_fetch(match.group(1))
                except (TrueGuardConnectionError, ValueError):
                    continue
                return picture, kind or "image/jpeg"
        return None

    async def _async_save_capture(self, capture: Capture) -> None:
        if (image := await self.async_capture_image(capture)) is None:
            return
        name = re.sub(r"[^0-9]", "", capture.time) or "unknown"
        folder = Path(self.hass.config.path("media", "trueguard_mz", f"zone_{capture.zone}"))
        await self.hass.async_add_executor_job(_write_file, folder, f"{name}.jpg", image[0])

    async def async_backup(self) -> Path:
        """Save the panel's configuration backup under /config/trueguard_mz/backups."""
        content = await self.client.async_get_backup()
        stamp = dt_util.now().strftime("%Y%m%d-%H%M%S")
        folder = Path(self.hass.config.path("trueguard_mz", "backups"))
        path = await self.hass.async_add_executor_job(
            _write_backup, folder, f"cfg-{stamp}.bin", content
        )
        self.last_backup = dt_util.utcnow()
        self.async_update_listeners()
        return path

    async def async_load_last_backup(self) -> None:
        folder = Path(self.hass.config.path("trueguard_mz", "backups"))
        newest = await self.hass.async_add_executor_job(_newest_backup, folder)
        if newest is not None:
            self.last_backup = dt_util.utc_from_timestamp(newest)

    async def _async_auto_backup(self) -> None:
        """Take a monthly backup when the option is on."""
        if not self.config_entry.options.get(CONF_AUTO_BACKUP):
            return
        if self.last_backup and dt_util.utcnow() - self.last_backup < AUTO_BACKUP_EVERY:
            return
        try:
            await self.async_backup()
        except (TrueGuardConnectionError, OSError) as err:
            _LOGGER.warning("Automatic TrueGuard backup failed: %s", err)
            self.last_backup = dt_util.utcnow() - AUTO_BACKUP_EVERY + timedelta(hours=1)

    def _merge_push(self, reports: tuple[ReportEvent, ...]) -> tuple[ReportEvent, ...]:
        """Put pushed reports the panel has not listed yet in front of its list."""
        reports = tuple(report for report in reports if report.status != "Push")
        now = dt_util.now().replace(tzinfo=None)
        kept: list[ReportEvent] = []
        for pushed in self._push_reports:
            when = parse_panel_time(pushed.time)
            if when is None or now - when > PUSH_KEEP:
                continue
            listed = any(
                report.cid == pushed.cid
                and report.zone == pushed.zone
                and (listed_at := parse_panel_time(report.time)) is not None
                and abs(listed_at - when) <= PUSH_MATCH
                for report in reports
            )
            if not listed:
                kept.append(pushed)
        self._push_reports = kept
        merged = sorted((*kept, *reports), key=lambda report: report.time, reverse=True)
        return tuple(merged)

    def async_handle_push(self, report: PushReport) -> None:
        """Apply a report pushed by the panel and read its pages at once."""
        self.last_push = report
        self.last_push_at = dt_util.utcnow()
        self.hass.bus.async_fire(
            EVENT_TRUEGUARD_REPORT,
            {
                "format": report.format,
                "account": report.account,
                "cid": report.cid,
                "qualifier": report.qualifier,
                "code": report.code,
                "sia_code": report.sia_code,
                "group": report.group,
                "zone": report.zone,
                "zone_name": zone_name(self.data, report.zone)
                if self.data and report.zone is not None
                else None,
                "config_entry_id": self.config_entry.entry_id,
            },
        )
        if report.code == "628":
            self._settings_read_at = None
        if not report.cid or report.code == "602":
            # Heartbeat or test report: only the "last contact" sensor changes.
            self.async_update_listeners()
            return
        stamp = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
        self._push_reports.append(
            ReportEvent(
                time=stamp,
                event=CODE_TEXT.get(report.code, f"CID {report.cid}"),
                cid=report.cid,
                group=report.group,
                zone=report.zone,
                status="Push",
            )
        )
        if self.data is not None:
            self.async_set_updated_data(
                replace(self.data, reports=self._merge_push(self.data.reports))
            )
        self._events_read_at = None  # read the log and report list on the next poll
        self.hass.async_create_task(self.async_request_refresh())

    def _track_countdowns(self, previous: PanelData | None, data: PanelData) -> None:
        """Start exit and entry countdowns from the panel's area times."""
        now = dt_util.utcnow()
        mode = arm_mode(data.alarm_state)
        if mode is None:
            self.arming_until = self.entry_until = None
            return
        if previous is None:
            return
        previous_mode = arm_mode(previous.alarm_state)
        if previous_mode is None and (delay := exit_delay(data, mode)):
            self.arming_until = now + timedelta(seconds=delay)
            self.entry_until = None
            return
        if self.arming_until and now < self.arming_until:
            return
        if self.entry_until and now < self.entry_until:
            return
        for zone in data.devices:
            if zone_is_open(data, zone) and not zone_is_open(previous, zone):
                if delay := entry_delay(data, zone, mode):
                    self.entry_until = now + timedelta(seconds=delay)
                    return

    def _fire_new_log_entries(self, data: PanelData) -> None:
        """Fire one bus event per log row that was not in the previous read."""
        if self._events_read_at is None:
            return
        current = Counter(data.history)
        if self._seen_log is None:
            self._seen_log = current
            return
        new = current - self._seen_log
        self._seen_log = current
        # The log is newest first; fire the oldest new row first.
        for entry in reversed(data.history):
            if new[entry] <= 0:
                continue
            new[entry] -= 1
            self.hass.bus.async_fire(
                EVENT_TRUEGUARD,
                {
                    **asdict(entry),
                    "zone_name": zone_name(data, entry.zone)
                    if entry.zone is not None
                    else None,
                    "config_entry_id": self.config_entry.entry_id,
                },
            )


def _clock_offset(panel_clock: str | None) -> int | None:
    """Return panel clock minus Home Assistant time, in whole minutes."""
    if not panel_clock:
        return None
    try:
        panel = datetime.strptime(panel_clock, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    local = dt_util.now().replace(tzinfo=None, second=0, microsecond=0)
    return round((panel - local).total_seconds() / 60)


def _looks_like_image(link: str) -> bool:
    return bool(re.search(r"\.(jpe?g|png|bmp)(\?|$)", link, re.I))


def _write_file(folder: Path, name: str, content: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    return path


def _write_backup(folder: Path, name: str, content: bytes) -> Path:
    """Write a backup and keep only the newest BACKUPS_TO_KEEP files."""
    path = _write_file(folder, name, content)
    backups = sorted(folder.glob("cfg-*.bin"))
    for old in backups[:-BACKUPS_TO_KEEP]:
        old.unlink(missing_ok=True)
    return path


def _newest_backup(folder: Path) -> float | None:
    backups = sorted(folder.glob("cfg-*.bin"))
    return backups[-1].stat().st_mtime if backups else None
