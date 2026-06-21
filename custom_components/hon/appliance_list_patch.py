"""Patch pyhon appliance enumeration onto Haier's new unified-api endpoint.

Background
----------
Around 2026-06-20 Haier migrated appliance enumeration off the legacy
``/commands/v1/appliance`` REST endpoint that pyhon-revived (0.18.3) calls in
:meth:`pyhon.connection.api.HonAPI.load_appliances`. For migrated accounts that
endpoint now answers ``200`` with an empty ``{"payload": {"appliances": []}}``
body, so every Haier device ends up ``unavailable`` in Home Assistant.

The hOn app now reads the list from a ``POST`` to
``{API_URL}/unified-api/v1/view/appliance-list`` with a ``{"deviceId": ...}``
body, and returns the appliances nested under
``modules.applianceList.payload.appliances`` (shape cross-checked against
gvigroux/hon's ``async_authorize``).

This module monkey-patches ``load_appliances`` to query the new endpoint. It is
a stopgap until the fix lands upstream in pyhon-revived; delete this module and
its use in ``__init__.py`` once the pinned dependency targets the new endpoint.

Ref: https://github.com/mmalolepszy/hon-revived/issues/48
"""

import json
import logging
from typing import Any

from pyhon import const
from pyhon.connection.api import HonAPI

_LOGGER = logging.getLogger(__name__)

# The legacy ``f"{const.API_URL}/commands/v1/appliance"`` now returns an empty
# list for migrated accounts; this is the endpoint the current app uses.
APPLIANCE_LIST_URL = f"{const.API_URL}/unified-api/v1/view/appliance-list"

# Fallback keys the appliance list might be nested under, in case the envelope
# differs from the primary ``modules.applianceList.payload.appliances`` path.
_LIST_KEYS = ("appliances", "applianceList", "appliancesList", "items", "data")


def _extract_appliances(result: Any) -> list[dict[str, Any]]:
    """Pull the appliance list out of the unified-api response body."""
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    # Primary shape used by the current app (see module docstring).
    try:
        nested = result["modules"]["applianceList"]["payload"]["appliances"]
        if isinstance(nested, list):
            return nested
    except (KeyError, TypeError):
        pass
    # Fallbacks: legacy ``payload.appliances`` or any known list-bearing key.
    payload = result.get("payload", result)
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


async def _load_appliances(self: HonAPI) -> list[dict[str, Any]]:
    """Replacement for ``HonAPI.load_appliances`` hitting the new endpoint."""
    # pylint: disable=protected-access
    async with self._hon.post(
        APPLIANCE_LIST_URL, json={"deviceId": "homeassistant"}
    ) as response:
        status = response.status
        raw = await response.text()
    try:
        result: Any = json.loads(raw) if raw else None
    except ValueError:
        result = None
    appliances = _extract_appliances(result)
    if appliances:
        _LOGGER.debug(
            "hon: loaded %d appliance(s) from %s", len(appliances), APPLIANCE_LIST_URL
        )
    else:
        # Surface the raw response so a shape/auth mismatch is debuggable from
        # the HA log instead of silently yielding zero devices.
        _LOGGER.warning(
            "hon: appliance-list returned no appliances (HTTP %s) from %s; "
            "body (truncated): %s",
            status,
            APPLIANCE_LIST_URL,
            raw[:600],
        )
    return appliances


def apply() -> None:
    """Install the monkey-patch (idempotent)."""
    HonAPI.load_appliances = _load_appliances  # type: ignore[method-assign]
    _LOGGER.info(
        "hon: patched HonAPI.load_appliances -> %s (Haier API migration workaround)",
        APPLIANCE_LIST_URL,
    )
