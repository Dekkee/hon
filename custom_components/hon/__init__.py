import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol  # type: ignore[import-untyped]
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import config_validation as cv, aiohttp_client
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pyhon import Hon

from .const import DOMAIN, PLATFORMS, MOBILE_ID, CONF_REFRESH_TOKEN

_LOGGER = logging.getLogger(__name__)

HON_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema(vol.All(cv.ensure_list, [HON_SCHEMA]))},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = aiohttp_client.async_get_clientsession(hass)
    if (config_dir := hass.config.config_dir) is None:
        raise ValueError("Missing Config Dir")
    hon = await Hon(
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        mobile_id=MOBILE_ID,
        session=session,
        test_data_path=Path(config_dir),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN, ""),
    ).create()

    # Save the new refresh token
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_REFRESH_TOKEN: hon.api.auth.refresh_token}
    )

    async def _async_poll() -> dict[str, Any]:
        """Periodic REST refresh as a fallback for MQTT push.

        pyhon delivers live state via AWS IoT MQTT push, but that
        subscription can silently stall with no auto-recovery short of a
        restart. Polling on an interval bounds staleness and keeps state
        usable even while push is dead.
        """
        for appliance in hon.appliances:
            try:
                await appliance.update()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Polling refresh failed for %s: %s",
                    getattr(appliance, "nick_name", appliance),
                    err,
                )
        return {}

    coordinator: DataUpdateCoordinator[dict[str, Any]] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=DOMAIN,
        update_interval=timedelta(seconds=60),
        update_method=_async_poll,
    )

    @callback
    def _push_update(data: Any) -> None:
        """Apply a pyhon push update on the event loop.

        pyhon-revived delivers MQTT push notifications from the awscrt
        network thread and invokes this subscriber synchronously (see
        pyhon.connection.mqtt.MQTTClient._on_publish_received ->
        Hon.notify). Calling the loop-affine ``async_set_updated_data``
        directly from that foreign thread raises a thread-safety error on
        HA Core 2026.5.x, so we marshal it onto the event loop.
        """
        hass.loop.call_soon_threadsafe(coordinator.async_set_updated_data, data)

    hon.subscribe_updates(_push_update)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.unique_id] = {"hon": hon, "coordinator": coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    refresh_token = hass.data[DOMAIN][entry.unique_id]["hon"].api.auth.refresh_token

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_REFRESH_TOKEN: refresh_token}
    )
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload:
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
    return unload
