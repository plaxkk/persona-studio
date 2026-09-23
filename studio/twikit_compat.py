"""Narrow compatibility shim for X's numeric webpack chunk manifest.

Twikit 2.3.3 expects a name -> hash mapping. X now emits chunk ID -> name
and chunk ID -> hash mappings. No browser access or authentication bypass.
Reference: https://github.com/d60/twikit/pull/412
"""

import re
from types import SimpleNamespace
from twikit.x_client_transaction.transaction import ClientTransaction


class XCompatibilityError(Exception):
    pass


def ondemand_url(html: str) -> str:
    legacy = re.search(r"""["']ondemand\.s["']\s*:\s*["']([a-fA-F0-9]+)["']""", html)
    if legacy:
        fingerprint = legacy[1]
    else:
        chunk = re.search(
            r"""(?:^|[,{])\s*["']?(\d+)["']?\s*:\s*["']ondemand\.s["']""", html
        )
        if not chunk:
            raise XCompatibilityError("chunk_manifest_missing")
        hashed = re.search(
            r"""(?:^|[,{])\s*["']?"""
            + re.escape(chunk[1])
            + r"""["']?\s*:\s*["']([a-fA-F0-9]+)["']""",
            html,
        )
        if not hashed:
            raise XCompatibilityError("chunk_hash_missing")
        fingerprint = hashed[1]
    return (
        f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{fingerprint}a.js"
    )


def key_indices(script: str) -> tuple[int, list[int]]:
    values = [
        int(v)
        for v in re.findall(
            r"""\(\s*[A-Za-z_$][\w$]*\[(\d{1,3})\]\s*,\s*16\s*\)""", script
        )
    ]
    if len(values) < 2 or any(value > 255 for value in values):
        raise XCompatibilityError("transaction_indices_missing")
    return values[0], values[1:]


class CompatibleTransaction(ClientTransaction):
    async def get_indices(self, home_page_response, session, headers):
        response = await session.get(
            ondemand_url(str(home_page_response)), headers=headers
        )
        response.raise_for_status()
        return key_indices(response.text)


def normalize_response(value):
    """Default an omitted optional withholding list, never invent identity fields."""
    if isinstance(value, dict):
        if value.get("__typename") == "User" and isinstance(value.get("legacy"), dict):
            value["legacy"].setdefault("withheld_in_countries", [])
            value["legacy"].setdefault("pinned_tweet_ids_str", [])
            value["legacy"].setdefault("entities", {}).setdefault(
                "description", {}
            ).setdefault("urls", [])
        for child in value.values():
            normalize_response(child)
    elif isinstance(value, list):
        for child in value:
            normalize_response(child)
    return value


from twikit import Client


class CompatibleClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_transaction = CompatibleTransaction()

    async def request(self, *args, **kwargs):
        data, response = await super().request(*args, **kwargs)
        return normalize_response(data), response

    async def authenticated_profile(self):
        # The old settings endpoint is intermittently unavailable. This endpoint
        # verifies the supplied session and directly returns the authenticated user.
        return await self.get(
            "https://api.x.com/1.1/account/verify_credentials.json",
            headers=self._base_headers,
            params={"skip_status": "true", "include_email": "false"},
        )

    async def user(self):
        profile, _ = await self.authenticated_profile()
        handle = profile.get("screen_name") if isinstance(profile, dict) else None
        ident = (
            str(profile.get("id_str") or profile.get("id") or "")
            if isinstance(profile, dict)
            else ""
        )
        if (
            not isinstance(handle, str)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle)
            or not ident.isdecimal()
        ):
            raise XCompatibilityError("authenticated_identity_missing")
        self._user_id = ident
        # Do not expose the endpoint's optional email/phone or other private fields.
        return SimpleNamespace(
            id=ident, screen_name=handle, name=str(profile.get("name") or handle)
        )
