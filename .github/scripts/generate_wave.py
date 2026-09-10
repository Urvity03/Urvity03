#!/usr/bin/env python3
"""
generate_wave.py
Generates a smooth wave/area contribution graph SVG from real GitHub data.

Usage:
  python generate_wave.py --username Urvity03 --token TOKEN --output assets/github-contribution-wave.svg
"""

import argparse
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# ── Tokyo Night / Sakura palette ─────────────────────────────────────────────
BG_COLOR       = "#1a1b27"
GRID_COLOR     = "#1e2030"
AXIS_COLOR     = "#2a2e45"
TEXT_COLOR     = "#a9b1d6"
LABEL_COLOR    = "#565f89"
LINE_COLOR     = "#7aa2f7"
PEAK_COLOR     = "#bb9af7"
GRAD_TOP_OP    = "0.30"
GRAD_MID_OP    = "0.10"
GRAD_BOT_OP    = "0.00"

# ── Chart layout ─────────────────────────────────────────────────────────────
SVG_W    = 900
SVG_H    = 260
MG_L     = 45   # left  (Y-axis labels)
MG_R     = 18   # right
MG_T     = 30   # top   (title clearance)
MG_B     = 42   # bottom (X-axis labels)
CW       = SVG_W - MG_L - MG_R
CH       = SVG_H - MG_T - MG_B
CORNER_R = 10

# ─────────────────────────────────────────────────────────────────────────────
def fetch_contributions(username: str, token: str):
    """Return (days_list, total) where days_list = [{date, count}, ...]"""
    now          = datetime.now(timezone.utc)
    one_year_ago = now - timedelta(days=365)

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "login": username,
        "from":  one_year_ago.strftime("%Y-%m-%dT00:00:00Z"),
        "to":    now.strftime("%Y-%m-%dT23:59:59Z"),
    }
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "User-Agent":    "contribution-wave-generator/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())

    if "errors" in body:
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    if not body.get("data", {}).get("user"):
        raise RuntimeError("User not found or API returned no data")

    cal   = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    total = cal["totalContributions"]
    days  = sorted(
        [{"date": d["date"], "count": d["contributionCount"]}
         for w in cal["weeks"] for d in w["contributionDays"]],
        key=lambda x: x["date"],
    )
    return days, total


# ── Smoothing (centred rolling average) ──────────────────────────────────────
def smooth(values: list, window: int = 7) -> list:
    n, half = len(values), window // 2
    return [
        sum(values[max(0, i - half): min(n, i + half + 1)])
        / len(values[max(0, i - half): min(n, i + half + 1)])
        for i in range(n)
    ]


# ── Catmull-Rom → cubic Bezier SVG path ──────────────────────────────────────
def catmull_rom_path(pts: list) -> str:
    """Return SVG 'd' attribute for a smooth curve through pts [(x,y), ...]"""
    n = len(pts)
    if n < 2:
        return ""

    segs = [f"M {pts[0][0]:.3f},{pts[0][1]:.3f}"]
    for i in range(1, n):
        p0 = pts[max(0, i - 2)]
        p1 = pts[i - 1]
        p2 = pts[i]
        p3 = pts[min(n - 1, i + 1)]

        # Control point 1:  P1 + (P2 - P0) / 6
        cp1x = p1[0] + (p2[0] - p0[0]) / 6
        cp1y = p1[1] + (p2[1] - p0[1]) / 6
        # Control point 2:  P2 - (P3 - P1) / 6
        cp2x = p2[0] - (p3[0] - p1[0]) / 6
        cp2y = p2[1] - (p3[1] - p1[1]) / 6

        segs.append(
            f"C {cp1x:.3f},{cp1y:.3f} {cp2x:.3f},{cp2y:.3f} {p2[0]:.3f},{p2[1]:.3f}"
        )
    return " ".join(segs)


# ── SVG builder ───────────────────────────────────────────────────────────────
def build_svg(days: list, total: int, username: str) -> str:
    if not days:
        raise ValueError("No contribution data")

    counts   = [d["count"] for d in days]
    smoothed = smooth(counts, window=7)

    max_val = max(counts) if any(c > 0 for c in counts) else 1

    # Y-axis ticks: 5 evenly spaced
    tick_step = max(1, math.ceil(max_val / 5))
    y_ticks   = list(range(0, max_val + tick_step + 1, tick_step))
    # Clip top tick to max_val + small headroom
    y_max_display = y_ticks[-1]

    n = len(days)

    def xc(i):
        return MG_L + (i / (n - 1)) * CW if n > 1 else MG_L

    def yc(v):
        v = max(0, min(v, y_max_display))
        return MG_T + CH - (v / y_max_display) * CH

    base_y = MG_T + CH   # chart bottom line

    # Build smoothed data points
    pts     = [(xc(i), yc(smoothed[i])) for i in range(n)]
    curve   = catmull_rom_path(pts)
    area_d  = (
        curve
        + f" L {pts[-1][0]:.3f},{base_y:.3f}"
        + f" L {pts[0][0]:.3f},{base_y:.3f} Z"
    )

    # Month tick positions (first day of each new month)
    month_ticks = []
    prev_month  = None
    for i, d in enumerate(days):
        dt = datetime.strptime(d["date"], "%Y-%m-%d")
        key = (dt.year, dt.month)
        if key != prev_month:
            month_ticks.append((i, dt.strftime("%b")))
            prev_month = key

    # Peak dot (actual raw peak, not smoothed)
    peak_idx   = counts.index(max(counts))
    peak_x     = xc(peak_idx)
    peak_y     = yc(smoothed[peak_idx])
    peak_label = datetime.strptime(days[peak_idx]["date"], "%Y-%m-%d").strftime("%b %d")
    peak_count = counts[peak_idx]

    # ── Begin SVG ─────────────────────────────────────────────────────────────
    svg = []
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {SVG_W} {SVG_H}" '
        f'width="{SVG_W}" height="{SVG_H}" '
        f'aria-label="GitHub contribution activity for {username}">'
    )

    # ── Defs ──────────────────────────────────────────────────────────────────
    svg.append(f"""  <defs>
    <linearGradient id="wg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="{LINE_COLOR}" stop-opacity="{GRAD_TOP_OP}"/>
      <stop offset="65%"  stop-color="{LINE_COLOR}" stop-opacity="{GRAD_MID_OP}"/>
      <stop offset="100%" stop-color="{LINE_COLOR}" stop-opacity="{GRAD_BOT_OP}"/>
    </linearGradient>
    <clipPath id="cc">
      <rect x="{MG_L}" y="{MG_T}" width="{CW}" height="{CH}"/>
    </clipPath>
  </defs>""")

    # ── Background ───────────────────────────────────────────────────────────
    svg.append(f'  <rect width="{SVG_W}" height="{SVG_H}" rx="{CORNER_R}" fill="{BG_COLOR}"/>')

    # ── Horizontal grid lines ─────────────────────────────────────────────────
    for tick in y_ticks:
        gy = yc(tick)
        if MG_T - 1 <= gy <= base_y + 1:
            svg.append(
                f'  <line x1="{MG_L}" y1="{gy:.2f}" x2="{MG_L + CW}" y2="{gy:.2f}" '
                f'stroke="{GRID_COLOR}" stroke-width="1"/>'
            )

    # ── Y-axis labels ─────────────────────────────────────────────────────────
    for tick in y_ticks:
        gy = yc(tick)
        if MG_T - 2 <= gy <= base_y + 4:
            svg.append(
                f'  <text x="{MG_L - 7}" y="{gy + 4:.2f}" '
                f'fill="{LABEL_COLOR}" '
                f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
                f'font-size="10" text-anchor="end">{tick}</text>'
            )

    # ── Area fill (clipped to chart bounds) ───────────────────────────────────
    svg.append(f'  <path d="{area_d}" fill="url(#wg)" clip-path="url(#cc)"/>')

    # ── Wave line ─────────────────────────────────────────────────────────────
    svg.append(
        f'  <path d="{curve}" fill="none" stroke="{LINE_COLOR}" '
        f'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" '
        f'clip-path="url(#cc)"/>'
    )

    # ── X-axis baseline ───────────────────────────────────────────────────────
    svg.append(
        f'  <line x1="{MG_L}" y1="{base_y}" x2="{MG_L + CW}" y2="{base_y}" '
        f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
    )

    # ── Month labels on X-axis ────────────────────────────────────────────────
    prev_mx = -999
    for idx, label in month_ticks:
        mx = xc(idx)
        if mx < MG_L + 8:
            continue
        if mx - prev_mx < 40:       # avoid overlapping labels
            continue
        prev_mx = mx
        # tick mark
        svg.append(
            f'  <line x1="{mx:.2f}" y1="{base_y}" x2="{mx:.2f}" y2="{base_y + 4}" '
            f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
        )
        svg.append(
            f'  <text x="{mx:.2f}" y="{base_y + 15}" '
            f'fill="{LABEL_COLOR}" '
            f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
            f'font-size="10" text-anchor="middle">{label}</text>'
        )

    # ── Year boundary labels (only if range spans Jan of a year) ─────────────
    # Already handled by month_ticks above — "Jan" label will appear naturally.

    # ── Peak marker ───────────────────────────────────────────────────────────
    if peak_count > 0:
        svg.append(
            f'  <circle cx="{peak_x:.2f}" cy="{peak_y:.2f}" r="4.5" '
            f'fill="{PEAK_COLOR}" stroke="{BG_COLOR}" stroke-width="1.5"/>'
        )
        # Position label so it doesn't fall outside chart
        lx = min(peak_x + 8, MG_L + CW - 80)
        ly = max(peak_y - 9,  MG_T + 11)
        svg.append(
            f'  <text x="{lx:.2f}" y="{ly:.2f}" '
            f'fill="{PEAK_COLOR}" '
            f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
            f'font-size="9.5">{peak_label}: {peak_count}</text>'
        )

    # ── Total contributions label (top right) ─────────────────────────────────
    svg.append(
        f'  <text x="{SVG_W - MG_R}" y="{MG_T - 10}" '
        f'fill="{TEXT_COLOR}" '
        f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
        f'font-size="11" text-anchor="end">'
        f'{total:,} contributions in the last year</text>'
    )

    svg.append('</svg>')
    return '\n'.join(svg)


# ── CLI entry-point ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate GitHub contribution wave SVG")
    parser.add_argument("--username", default="Urvity03")
    parser.add_argument("--token",    required=True, help="GitHub token (repo scope)")
    parser.add_argument("--output",   default="assets/github-contribution-wave.svg")
    args = parser.parse_args()

    print(f"[>>] Fetching contribution data for @{args.username} ...")
    days, total = fetch_contributions(args.username, args.token)
    counts = [d["count"] for d in days]
    print(f"[OK] {len(days)} days  |  {total} total  |  peak = {max(counts)} on "
          f"{days[counts.index(max(counts))]['date']}")

    print("[>>] Building SVG wave chart ...")
    svg = build_svg(days, total, args.username)

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"[OK] Saved -> {args.output}  ({len(svg):,} bytes)")


if __name__ == "__main__":
    main()
