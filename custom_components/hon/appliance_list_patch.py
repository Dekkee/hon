"""Patch pyhon appliance enumeration onto Haier's new unified-api endpoint.

Background
----------
Around 2026-06-20 Haier migrated appliance enumeration off the legacy
``/commands/v1/appliance`` REST endpoint that pyhon-revived (0.18.3) calls in
:meth:`pyhon.connection.api.HonAPI.load_appliances`. For migrated accounts that
endpoint now answers ``200`` with an empty ``{"payload": {"appliances": []}}``
body, so every Haier device ends up ``unavailable`` in Home Assistant. The hOn
mobile app reads the list from ``{API_URL}/unified-api/v1/view/appliance-list``
instead, which returns the same appliance records.

This module monkey-patches ``load_appliances`` to query the new endpoint. It is
a stopgap until the fix lands upstream in pyhon-revived; delete this module and
its use in ``__init__.py`` once the pinned dependency targets the new endpoint.

Ref: https://github.com/mmalolepszy/hon-revived/issues/48
"""

import logging
from typing import Any

from pyhon import const
from pyhon.connection.api import HonAPI

_LOGGER = logging.getLogger(__name__)

# The legacy ``f"{const.API_URL}/commands/v1/appliance"`` now returns an empty
# list for migrated accounts; this is the endpoint the current app uses.
APPLIANCE_LIST_URL = f"{const.API_URL}/unified-api/v1/view/appliance-list"

# Keys the unified-api response might nest the appliance list under. The exact
# envelope is undocumented, so probe the plausible shapes rather than assume one
# and silently return nothing.
_LIST_KEYS = ("appliances", "applianceList", "appliancesList", "items", "data")


def _extract_appliances(result: Any) -> list[dict[str, Any]]:
    """Pull the appliance list out of the unified-api response body."""
    if not result:
        return []
    if isinstance(result, dict):
        payload: Any = result.get("payload", result)
    else:
        payload = result
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    _LOGGER.warning(
        "hon: unexpected appliance-list response shape (top-level=%s, payload=%s); "
        "no appliances parsed",
        list(result.keys()) if isinstance(result, dict) else type(result).__name__,
        list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
    )
    return []


async def _load_appliances(self: HonAPI) -> list[dict[str, Any]]:
    """Replacement for ``HonAPI.load_appliances`` hitting the new endpoint."""
    # pylint: disable=protected-access
    async with self._hon.get(APPLIANCE_LIST_URL) as response:
        result = await response.json()
    appliances = _extract_appliances(result)
    _LOGGER.debug(
        "hon: loaded %d appliance(s) from %s", len(appliances), APPLIANCE_LIST_URL
    )
    return appliances


def apply() -> None:
    """Install the monkey-patch (idempotent)."""
    HonAPI.load_appliances = _load_appliances  # type: ignore[method-assign]
    _LOGGER.info(
        "hon: patched HonAPI.load_appliances -> %s (Haier API migration workaround)",
        APPLIANCE_LIST_URL,
    )
