#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://api.github.com"
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
CACHE_PATH = ROOT / "candle_cache.json"
TEMPLATE_PATH = ROOT / "template.svg"
OUTPUT_PATH = ROOT / "candle.svg"
README_PATH = ROOT / "README.md"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


@dataclass(frozen=True)
class Metrics:
    stars: int
    commits_30d: int
    days_since_last_commit: int
    brightness: float
    flame_height: int
    flicker_speed: str
    dormant: bool


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def github_get(endpoint: str, token: str | None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "cyber-candle"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{BASE_URL}{endpoint}", headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def load_api_data(owner: str, repo: str, token: str | None) -> dict[str, Any]:
    return {
        "repo": github_get(f"/repos/{owner}/{repo}", token),
        "commit_activity": github_get(f"/repos/{owner}/{repo}/stats/commit_activity", token),
        "commits": github_get(f"/repos/{owner}/{repo}/commits?per_page=100", token),
    }


def load_data(owner: str, repo: str, token: str | None) -> dict[str, Any]:
    cache = load_json(CACHE_PATH)
    try:
        api_data = load_api_data(owner, repo, token)
        payload = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            **api_data,
        }
        write_json(CACHE_PATH, payload)
        logging.info("Fetched GitHub data and refreshed cache.")
        return payload
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        logging.error("GitHub API failed, loading cached data: %s", exc)
        if cache:
            return cache
        raise RuntimeError("API unavailable and candle_cache.json is empty.") from exc


def parse_iso8601(date_text: str) -> datetime:
    return datetime.strptime(date_text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def compute_metrics(data: dict[str, Any]) -> Metrics:
    stars = int(data.get("repo", {}).get("stargazers_count", 0))

    activity = data.get("commit_activity", []) or []
    if isinstance(activity, dict):
        activity = []
    commits_30d = 0
    for week in activity[-5:]:
        if isinstance(week, dict):
            commits_30d += int(week.get("total", 0))

    commits = data.get("commits", []) or []
    days_since_last_commit = 365
    if commits and isinstance(commits[0], dict):
        date_text = commits[0].get("commit", {}).get("committer", {}).get("date")
        if date_text:
            delta = datetime.now(timezone.utc) - parse_iso8601(date_text)
            days_since_last_commit = max(0, delta.days)

    raw_brightness = math.log10(stars + 1) / math.log10(1000)
    brightness = max(0.1, min(1.0, raw_brightness))

    raw_height = 40 + (commits_30d / (days_since_last_commit + 1)) * 8
    flame_height = min(160, int(round(raw_height / 8) * 8))
    flame_height = max(40, flame_height)

    if days_since_last_commit < 7:
        flicker_speed = "0.5s"
    elif days_since_last_commit <= 30:
        flicker_speed = "1.5s"
    else:
        flicker_speed = "3s"

    return Metrics(
        stars=stars,
        commits_30d=commits_30d,
        days_since_last_commit=days_since_last_commit,
        brightness=brightness,
        flame_height=flame_height,
        flicker_speed=flicker_speed,
        dormant=days_since_last_commit >= 60,
    )


def phase_offsets(owner: str, repo: str) -> tuple[str, str, str]:
    seed = sum(ord(ch) for ch in f"{owner}/{repo}")
    offsets = [((seed + index * 17) % 10) / 10 for index in range(3)]
    return tuple(f"-{offset:.1f}s" for offset in offsets)


def render_svg(owner: str, repo: str, metrics: Metrics) -> str:
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    phase_a, phase_b, phase_c = phase_offsets(owner, repo)

    geometry_y = max(8, int(round((160 - metrics.flame_height) / 8) * 8))
    flame_path = (
        f"M200 {geometry_y + metrics.flame_height} "
        f"C176 {geometry_y + 80} 184 {geometry_y + 32} 200 {geometry_y} "
        f"C216 {geometry_y + 32} 224 {geometry_y + 80} 200 {geometry_y + metrics.flame_height} Z"
    )

    return template.substitute(
        owner=owner,
        repo=repo,
        stars=metrics.stars,
        commits30=metrics.commits_30d,
        days=metrics.days_since_last_commit,
        brightness=f"{metrics.brightness:.3f}",
        flicker_speed=metrics.flicker_speed,
        animation_state="paused" if metrics.dormant else "running",
        level="ember" if metrics.dormant else "active",
        phase_a=phase_a,
        phase_b=phase_b,
        phase_c=phase_c,
        flame_path=flame_path,
        ember_opacity="0.8" if metrics.dormant else "0",
        glow_opacity="0" if metrics.dormant else "1",
    )


def update_readme_snippet(owner: str, repo: str) -> None:
    snippet = (
        "## Cyber Candle\n\n"
        "![Cyber Candle](./candle.svg)\n\n"
        f"Generated from `{owner}/{repo}` using the Community Oxygen Model.\n"
    )
    marker_start = "<!-- cyber-candle:start -->"
    marker_end = "<!-- cyber-candle:end -->"
    if README_PATH.exists():
        content = README_PATH.read_text(encoding="utf-8")
        if marker_start in content and marker_end in content:
            pre, rest = content.split(marker_start, 1)
            _, post = rest.split(marker_end, 1)
            README_PATH.write_text(f"{pre}{marker_start}\n{snippet}\n{marker_end}{post}", encoding="utf-8")
            return
    README_PATH.write_text(f"{marker_start}\n{snippet}\n{marker_end}\n", encoding="utf-8")


def main() -> None:
    config = load_json(CONFIG_PATH)
    owner = os.getenv("CANDLE_OWNER", config.get("owner", "octocat"))
    repo = os.getenv("CANDLE_REPO", config.get("repo", "Hello-World"))
    token = os.getenv("GITHUB_TOKEN")

    data = load_data(owner, repo, token)
    svg = render_svg(owner, repo, compute_metrics(data))

    OUTPUT_PATH.write_text(svg, encoding="utf-8")
    update_readme_snippet(owner, repo)
    logging.info("Cyber candle rendered: %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
