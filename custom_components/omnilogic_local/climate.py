"""Climate platform for OmniLogic Local integration.

Exposes the pool heater as a heat-only thermostat with Off mode for proper
HomeKit compatibility. Home Assistant's HomeKit bridge only shows "Heat" for
water_heater entities, but climate entities with hvac_modes [heat, off] display
both options correctly in Apple Home.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from pyomnilogic_local.models.telemetry import TelemetryBoW
from pyomnilogic_local.omnitypes import OmniType

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEY_COORDINATOR
from .entity import OmniLogicEntity
from .types.entity_index import EntityIndexHeater, EntityIndexHeaterEquip
from .utils import get_entities_of_hass_type

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import OmniLogicCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the climate platform for pool heaters (HomeKit-friendly)."""

    coordinator = hass.data[DOMAIN][entry.entry_id][KEY_COORDINATOR]

    all_heaters = get_entities_of_hass_type(coordinator.data, "water_heater")

    virtual_heater = {
        system_id: data
        for system_id, data in all_heaters.items()
        if data.msp_config.omni_type == OmniType.VIRT_HEATER
    }
    heater_equipment_ids = [
        system_id for system_id, data in all_heaters.items() if data.msp_config.omni_type == OmniType.HEATER_EQUIP
    ]

    entities = []
    for system_id, vheater in virtual_heater.items():
        _LOGGER.debug(
            "Configuring climate heater with ID: %s, Name: %s",
            vheater.msp_config.system_id,
            vheater.msp_config.name,
        )
        entities.append(
            OmniLogicPoolHeaterClimateEntity(
                coordinator=coordinator,
                context=system_id,
                heater_equipment_ids=heater_equipment_ids,
            )
        )

    async_add_entities(entities)


class OmniLogicPoolHeaterClimateEntity(OmniLogicEntity[EntityIndexHeater], ClimateEntity):
    """Heat-only climate entity for pool heater with Off mode (HomeKit compatible)."""

    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_name = "Pool Heater"

    def __init__(self, coordinator: OmniLogicCoordinator, context: int, heater_equipment_ids: list[int]) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator=coordinator, context=context)
        self.heater_equipment_ids = heater_equipment_ids

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity (distinct from water_heater)."""
        return f"{self.bow_id}_{self.system_id}_pool_heater_climate"

    @property
    def temperature_unit(self) -> str:
        """Heaters always return Fahrenheit. See github.com/cryptk/haomnilogic-local/issues/96."""
        return UnitOfTemperature.FAHRENHEIT

    @property
    def min_temp(self) -> float:
        return self.data.msp_config.min_temp

    @property
    def max_temp(self) -> float:
        return self.data.msp_config.max_temp

    @property
    def current_temperature(self) -> float | None:
        current_temp = cast(TelemetryBoW, self.get_telemetry_by_systemid(self.bow_id)).water_temp
        return current_temp if current_temp != -1 else None

    @property
    def target_temperature(self) -> float | None:
        return self.data.telemetry.current_set_point

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.data.telemetry.enabled else HVACMode.OFF

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode (heat or off)."""
        enabled = hvac_mode == HVACMode.HEAT
        await self.coordinator.omni_api.async_set_heater_enable(self.bow_id, self.system_id, enabled)
        self.set_telemetry({"enabled": "yes" if enabled else "no"})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self.coordinator.omni_api.async_set_heater(
            self.bow_id,
            self.system_id,
            int(temperature),
            unit=self.temperature_unit,
        )
        self.set_telemetry({"current_set_point": int(temperature)})

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        """Extra attributes for heater equipment status."""
        extra = super().extra_state_attributes | {"solar_set_point": self.data.msp_config.solar_set_point}
        for system_id in self.heater_equipment_ids:
            heater_equipment = cast(EntityIndexHeaterEquip, self.coordinator.data[system_id])
            prefix = f"omni_heater_{heater_equipment.msp_config.name.lower()}"
            extra = extra | {
                f"{prefix}_enabled": heater_equipment.msp_config.enabled,
                f"{prefix}_system_id": system_id,
                f"{prefix}_bow_id": heater_equipment.msp_config.bow_id,
                f"{prefix}_state": heater_equipment.telemetry.state.pretty(),
                f"{prefix}_sensor_temp": heater_equipment.telemetry.temp,
            }
        return extra
