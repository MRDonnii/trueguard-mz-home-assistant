"""Camera platform for TrueGuard MZ PIR cameras ('PIR m. kamera')."""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import TrueGuardEntity
from .events import is_camera
from .parser import Capture


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one camera per PIR camera zone."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        TrueGuardCamera(coordinator, entry, zone)
        for zone, device in coordinator.data.devices.items()
        if is_camera(device.device_type)
    )


class TrueGuardCamera(TrueGuardEntity, Camera):
    """The newest picture a PIR camera stored on the panel (Billeder/Video)."""

    _attr_name = "Billede"

    def __init__(self, coordinator, entry: ConfigEntry, zone: int) -> None:
        TrueGuardEntity.__init__(self, coordinator, entry, zone)
        Camera.__init__(self)
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_camera"
        self._cached: tuple[Capture, bytes] | None = None

    def _latest(self) -> Capture | None:
        return next(
            (
                capture
                for capture in self.coordinator.data.captures
                if capture.zone == self.zone and capture.links
            ),
            None,
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        if (capture := self._latest()) is None:
            return None
        if self._cached and self._cached[0] == capture:
            return self._cached[1]
        if (image := await self.coordinator.async_capture_image(capture)) is None:
            return None
        self._cached = (capture, image[0])
        return image[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        capture = self._latest()
        return {
            "zone": self.zone,
            "time": capture.time if capture else None,
            "kind": capture.kind if capture else None,
            "pictures": len(capture.links) if capture else 0,
        }
