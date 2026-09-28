"""Pure Python release lookup used by the Blender preferences UI."""

import json
import re
from urllib.request import Request, urlopen


API_URL = "https://api.github.com/repos/ssice-a/EFF-blender/releases/latest"
RELEASE_PREFIX = "https://github.com/ssice-a/EFF-blender/releases/"
VERSION = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag):
    match = VERSION.fullmatch(tag or "")
    if not match:
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    return tuple(map(int, match.groups()))


def check_release(current, ignored_tag="", force=False, opener=urlopen):
    request = Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "EFF-Blender-UpdateCheck/1.0",
    })
    with opener(request, timeout=5) as response:
        data = json.load(response)
    tag, url = data.get("tag_name"), data.get("html_url")
    latest = parse_version(tag)
    if not isinstance(url, str) or not url.startswith(RELEASE_PREFIX):
        raise ValueError("Invalid release URL")
    if latest <= tuple(current):
        return {"status": "latest", "tag": tag, "url": url}
    if not force and tag == ignored_tag:
        return {"status": "ignored", "tag": tag, "url": url}
    return {"status": "available", "tag": tag, "url": url}
