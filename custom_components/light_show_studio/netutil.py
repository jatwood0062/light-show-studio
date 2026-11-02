"""Safe handling of HTTP responses from lights: no redirects, capped size. Needs only aiohttp."""
from __future__ import annotations

import json
from typing import Any

import aiohttp

MAX_RESPONSE = 256 * 1024  # a light's JSON is a few KB; anything bigger is not a light


def check(resp: aiohttp.ClientResponse) -> None:
    """Raise unless the response is a plain success. Redirects are refused, not followed: a light
    that answers 3xx could be sending Home Assistant somewhere else."""
    if 300 <= resp.status < 400:
        raise aiohttp.ClientError(f"redirect refused ({resp.status})")
    resp.raise_for_status()


async def read_json(resp: aiohttp.ClientResponse, limit: int = MAX_RESPONSE) -> Any:
    """The body as JSON (any content type), reading at most `limit` bytes. ValueError when longer."""
    body = bytearray()
    async for chunk in resp.content.iter_chunked(16384):
        body += chunk
        if len(body) > limit:
            raise ValueError(f"response is over {limit} bytes")
    return json.loads(body)
