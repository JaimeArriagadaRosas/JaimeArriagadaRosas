#!/usr/bin/env python3
"""Combine a real GitHub contribution total with the original animated snake SVG."""
import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


API_URL = "https://api.github.com/graphql"
QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
      }
    }
  }
}
"""
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
THEMES = {
    "light": {
        "colors": ("#078f88", "#397bd0", "#8259be"),
        "label": "#53677f",
    },
    "dark": {
        "colors": ("#68e1d3", "#67a9f8", "#b48bfa"),
        "label": "#a9bdd8",
    },
}


def fetch_contributions(login: str) -> int:
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GH_TOKEN is required to query GitHub GraphQL.")
    request = urllib.request.Request(
        API_URL,
        data=json.dumps({
            "query": QUERY,
            "variables": {"login": login},
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-profile-activity",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub GraphQL returned HTTP {exc.code}") from exc

    if payload.get("errors"):
        descriptions = "; ".join(
            str(item.get("message", "Unknown GraphQL error"))
            for item in payload["errors"]
        )
        raise RuntimeError(f"GitHub GraphQL error: {descriptions}")

    user = (payload.get("data") or {}).get("user")
    if not user:
        raise RuntimeError(f"GitHub user {login!r} not found via GraphQL.")

    count = user["contributionsCollection"]["contributionCalendar"]["totalContributions"]
    if type(count) is not int or count < 0:
        raise ValueError("GitHub returned an invalid contribution count.")
    return count


def compose(source: str, total: int, theme: str) -> str:
    """Only extend the root canvas and inject a header; preserve the snake verbatim."""
    root = ET.fromstring(source)
    if root.tag != f"{{{SVG_NAMESPACE}}}svg":
        raise ValueError("Input is not an SVG root element.")
    if "<style>" not in source or "@keyframes" not in source:
        raise ValueError("Expected an animated Platane/snk SVG.")

    match = re.search(r"<svg\b[^>]*>", source)
    if match is None:
        raise ValueError("No SVG opening tag was found.")
    opening = match.group()
    view_box = re.search(r'\bviewBox="([^"]+)"', opening)
    if not view_box:
        raise ValueError("Snake SVG has no viewBox.")
    x, y, width, height = (float(item) for item in view_box.group(1).split())
    extra_height = 66
    new_view_box = " ".join(f"{n:g}" for n in (x, y - extra_height, width, height + extra_height))
    opening = opening.replace(view_box.group(), f'viewBox="{new_view_box}"', 1)
    opening, replacements = re.subn(
        r'\bheight="[^"]+"',
        f'height="{height + extra_height:g}"',
        opening,
        count=1,
    )
    if replacements != 1:
        raise ValueError("Snake SVG has no root height attribute.")

    opening = opening[:-1] + (
        f' role="img" aria-label="{total} contributions in the last year and an animated GitHub snake">'
    )
    palette = THEMES[theme]
    colors = palette["colors"]
    gradient_id = f"activity-aurora-{theme}"
    if f'id="{gradient_id}"' in source:
        raise ValueError("Generated activity gradient ID collides with an existing SVG ID.")

    count_text = f"{total:,}"
    label_x = max(111, 26 * len(count_text) + 22)
    number_y = y - 17
    label_color = palette["label"]
    overlay = (
        f'<title>GitHub activity: {total} contributions in the last year</title>'
        f'<defs><linearGradient id="{gradient_id}" x1="0" y1="0" x2="240" y2="0" gradientUnits="userSpaceOnUse">'
        f'<stop stop-color="{colors[0]}"/>'
        f'<stop offset=".52" stop-color="{colors[1]}"/>'
        f'<stop offset="1" stop-color="{colors[2]}"/>'
        f'</linearGradient></defs>'
        f'<text x="0" y="{number_y:g}" fill="url(#{gradient_id})" '
        'font-family="Inter, Segoe UI, Arial, sans-serif" font-weight="700" '
        'font-size="43" letter-spacing="-1" '
        f'font-variant-numeric="tabular-nums">{count_text}</text>'
        f'<text x="{label_x}" y="{number_y - 11:g}" fill="{label_color}" '
        'font-family="Inter, Segoe UI, Arial, sans-serif" font-size="12" '
        'font-weight="600" letter-spacing="1.25">CONTRIBUTIONS</text>'
        f'<text x="{label_x}" y="{number_y + 9:g}" fill="{label_color}" '
        'font-family="Inter, Segoe UI, Arial, sans-serif" font-size="12" '
        'font-weight="400" letter-spacing=".55">LAST 12 MONTHS</text>'
    )
    combined = source[:match.start()] + opening + overlay + source[match.end():]
    new_root = ET.fromstring(combined)
    if new_root.tag != root.tag:
        raise ValueError("Combined output is not valid SVG.")
    if combined.count("@keyframes") != source.count("@keyframes"):
        raise ValueError("Animation keyframes changed during composition.")
    if combined.count("<rect ") != source.count("<rect "):
        raise ValueError("Snake geometry changed during composition.")
    source_css = source.split("<style>", 1)[1].split("</style>", 1)[0]
    if f"<style>{source_css}</style>" not in combined:
        raise ValueError("Snake animation CSS was modified.")
    return combined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", required=True)
    parser.add_argument("--light", type=Path, required=True)
    parser.add_argument("--dark", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    total = fetch_contributions(args.login)
    print(f"GraphQL contributionCalendar.totalContributions: {total}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for theme, snake_path, filename in (
        ("light", args.light, "github-snake.svg"),
        ("dark", args.dark, "github-snake-dark.svg"),
    ):
        combined = compose(snake_path.read_text(encoding="utf-8"), total, theme)
        output = args.output_dir / filename
        output.write_text(combined, encoding="utf-8")
        print(f"Validated {output} ({theme}; {len(combined)} characters)")


if __name__ == "__main__":
    main()
