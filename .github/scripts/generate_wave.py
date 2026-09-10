#!/usr/bin/env python3
"""
generate_wave.py  v3 (Rolling 60-Day Window)
Generates a smooth wave/area contribution graph SVG from real GitHub contribution calendar data.

Time window:
  - Dynamically calculates the rolling 60-day window ending at the latest available contribution date.
  - Every single day in the 60-day window is plotted chronologically.
  - Sum of plotted daily counts equals the displayed total.

Visual styling:
  - Tokyo Night aesthetic with dark background (#1a1b27)
  - Smooth Catmull-Rom wave line (#7aa2f7)
  - Gradient area fill beneath the curve
  - Dynamic adaptive Y-axis
  - Clean evenly-spaced date labels on X-axis

Usage:
  python generate_wave.py --username Urvity03 --token TOKEN --output assets/github-contribution-wave.svg
"""

import argparse
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# ── Tokyo Night palette ───────────────────────────────────────────────────────
BG_COLOR    = "#1a1b27"
GRID_COLOR  = "#1e2030"
AXIS_COLOR  = "#2a2e45"
TEXT_COLOR  = "#a9b1d6"
LABEL_COLOR = "#565f89"
LINE_COLOR  = "#7aa2f7"
PEAK_COLOR  = "#bb9af7"

# ── Chart layout ──────────────────────────────────────────────────────────────
SVG_W       = 900
SVG_H       = 260
MG_L        = 48   # left margin (Y-axis labels)
MG_R        = 24   # right margin
MG_T        = 36   # top margin (title clearance)
MG_B        = 44   # bottom margin (X-axis labels)
CW          = SVG_W - MG_L - MG_R
CH          = SVG_H - MG_T - MG_B
CORNER_R    = 10
WINDOW_DAYS = 60   # rolling window length


# ─────────────────────────────────────────────────────────────────────────────
def fetch_contributions(username: str, token: str):
    """
    Fetch GitHub contribution calendar via GraphQL API.
    Returns (days, total_in_calendar) where:
      days = [{"date": "YYYY-MM-DD", "count": int}, ...] sorted oldest to newest.
    """
    now          = datetime.now(timezone.utc)
    one_year_ago = now - timedelta(days=365)

    QUERY = """
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

    payload = json.dumps({"query": QUERY, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "User-Agent":    "contribution-wave-generator/3.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())

    if "errors" in body:
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    if not body.get("data", {}).get("user"):
        raise RuntimeError("User not found or no data returned by GraphQL API")

    cal   = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    total = cal["totalContributions"]

    # Flatten weeks -> days, sort chronologically
    days = sorted(
        [{"date": d["date"], "count": d["contributionCount"]}
         for w in cal["weeks"] for d in w["contributionDays"]],
        key=lambda x: x["date"],
    )

    # Sanity check total
    computed = sum(d["count"] for d in days)
    if computed != total:
        raise RuntimeError(
            f"Data integrity FAILED: sum({computed}) != totalContributions({total})."
        )

    return days, total


# ── Nice Y-axis ticks generator ───────────────────────────────────────────────
def get_nice_y_ticks(max_val: int):
    """Dynamically determine sensible Y-axis ticks based on the window maximum."""
    if max_val <= 0:
        return [0, 1, 2, 3, 4], 4
    elif max_val <= 5:
        step = 1
    elif max_val <= 12:
        step = 2
    elif max_val <= 25:
        step = 5
    elif max_val <= 50:
        step = 10
    elif max_val <= 100:
        step = 20
    else:
        step = 25

    top = math.ceil(max_val / step) * step
    if top == max_val:
        top += step  # headroom so peak marker isn't clipped
    ticks = list(range(0, top + 1, step))
    return ticks, top


# ── Catmull-Rom -> cubic Bézier with zero-clamping ───────────────────────────
def build_wave_path(pts: list, counts: list, base_y: float) -> str:
    """
    Constructs a smooth Catmull-Rom cubic Bézier curve passing through all daily points.
    Preserves raw daily values while guaranteeing zero-activity periods stay flat on baseline.
    """
    n = len(pts)
    if n < 2:
        return ""

    segs = [f"M {pts[0][0]:.3f},{pts[0][1]:.3f}"]
    for i in range(1, n):
        p0 = pts[max(0, i - 2)]
        p1 = pts[i - 1]
        p2 = pts[i]
        p3 = pts[min(n - 1, i + 1)]

        # Flat baseline if both consecutive days have 0 contributions
        if counts[i - 1] == 0 and counts[i] == 0:
            segs.append(f"L {p2[0]:.3f},{p2[1]:.3f}")
            continue

        cp1x = p1[0] + (p2[0] - p0[0]) / 6.0
        cp1y = p1[1] + (p2[1] - p0[1]) / 6.0
        cp2x = p2[0] - (p3[0] - p1[0]) / 6.0
        cp2y = p2[1] - (p3[1] - p1[1]) / 6.0

        # Clamp control points so the wave never dips below zero baseline
        cp1y = min(cp1y, base_y)
        cp2y = min(cp2y, base_y)
        cp1y = max(cp1y, MG_T)
        cp2y = max(cp2y, MG_T)

        segs.append(f"C {cp1x:.3f},{cp1y:.3f} {cp2x:.3f},{cp2y:.3f} {p2[0]:.3f},{p2[1]:.3f}")

    return " ".join(segs)


# ── SVG builder ───────────────────────────────────────────────────────────────
def build_svg(days_60: list, username: str) -> str:
    n = len(days_60)
    if n != WINDOW_DAYS:
        print(f"[WARN] Expected {WINDOW_DAYS} days, got {n}")

    counts       = [d["count"] for d in days_60]
    window_total = sum(counts)
    max_raw      = max(counts) if any(c > 0 for c in counts) else 1

    latest_dt    = datetime.strptime(days_60[-1]["date"], "%Y-%m-%d")
    latest_str   = latest_dt.strftime("%b %d")

    # Dynamic Y-axis
    y_ticks, y_max_display = get_nice_y_ticks(max_raw)
    base_y = MG_T + CH

    def xc(i: int) -> float:
        return MG_L + (i / (n - 1)) * CW if n > 1 else MG_L

    def yc(v: float) -> float:
        ratio = max(0.0, min(float(v), float(y_max_display))) / y_max_display
        return MG_T + CH - ratio * CH

    pts = [(xc(i), yc(counts[i])) for i in range(n)]

    # Curve and area
    curve  = build_wave_path(pts, counts, base_y)
    area_d = (
        curve
        + f" L {pts[-1][0]:.3f},{base_y:.3f}"
        + f" L {pts[0][0]:.3f},{base_y:.3f} Z"
    )

    # ── X-axis date labels: evenly distributed ~8 ticks across 60 days ───────
    # Pick 8 indices: 0, 8, 17, 25, 34, 42, 51, 59 (starts on day 1, ends on latest day)
    num_x_ticks = 8
    x_tick_indices = [round(i * (n - 1) / (num_x_ticks - 1)) for i in range(num_x_ticks)]
    x_ticks = []
    for idx in x_tick_indices:
        d_str = days_60[idx]["date"]
        label = datetime.strptime(d_str, "%Y-%m-%d").strftime("%b %d")
        x_ticks.append((xc(idx), label))

    # ── Peak marker ───────────────────────────────────────────────────────────
    peak_idx   = counts.index(max(counts))
    peak_x     = xc(peak_idx)
    peak_y     = yc(counts[peak_idx])
    peak_dt    = datetime.strptime(days_60[peak_idx]["date"], "%Y-%m-%d")
    peak_label = f"{peak_dt.strftime('%b %d')}: {counts[peak_idx]}"
    peak_count = counts[peak_idx]

    # ── Begin SVG construction ────────────────────────────────────────────────
    svg = []
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {SVG_W} {SVG_H}" '
        f'width="{SVG_W}" height="{SVG_H}" '
        f'aria-label="GitHub contribution activity for {username} - Last {WINDOW_DAYS} days">'
    )

    # ── Defs ──────────────────────────────────────────────────────────────────
    svg.append(f"""  <defs>
    <linearGradient id="wg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="{LINE_COLOR}" stop-opacity="0.45"/>
      <stop offset="60%"  stop-color="{LINE_COLOR}" stop-opacity="0.12"/>
      <stop offset="100%" stop-color="{LINE_COLOR}" stop-opacity="0.00"/>
    </linearGradient>
    <clipPath id="cc">
      <rect x="{MG_L}" y="{MG_T}" width="{CW}" height="{CH}"/>
    </clipPath>
  </defs>""")

    # ── Background ────────────────────────────────────────────────────────────
    svg.append(f'  <rect width="{SVG_W}" height="{SVG_H}" rx="{CORNER_R}" fill="{BG_COLOR}"/>')

    # ── Horizontal grid lines (at each Y tick) ────────────────────────────────
    for tick in y_ticks:
        gy = yc(tick)
        if MG_T - 1 <= gy <= base_y + 1:
            svg.append(
                f'  <line x1="{MG_L}" y1="{gy:.2f}" x2="{MG_L + CW}" y2="{gy:.2f}" '
                f'stroke="{GRID_COLOR}" stroke-width="1"/>'
            )

    # ── Y-axis tick labels ────────────────────────────────────────────────────
    for tick in y_ticks:
        gy = yc(tick)
        if MG_T - 3 <= gy <= base_y + 5:
            svg.append(
                f'  <text x="{MG_L - 8}" y="{gy + 4:.2f}" '
                f'fill="{LABEL_COLOR}" '
                f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
                f'font-size="10" text-anchor="end">{tick}</text>'
            )

    # ── Area fill ─────────────────────────────────────────────────────────────
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

    # ── X-axis date labels ────────────────────────────────────────────────────
    for mx, label in x_ticks:
        svg.append(
            f'  <line x1="{mx:.2f}" y1="{base_y}" x2="{mx:.2f}" y2="{base_y + 4}" '
            f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
        )
        svg.append(
            f'  <text x="{mx:.2f}" y="{base_y + 16}" '
            f'fill="{LABEL_COLOR}" '
            f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
            f'font-size="10" text-anchor="middle">{label}</text>'
        )

    # ── Peak marker (lavender dot + label) ────────────────────────────────────
    if peak_count > 0:
        svg.append(
            f'  <circle cx="{peak_x:.2f}" cy="{peak_y:.2f}" r="4.5" '
            f'fill="{PEAK_COLOR}" stroke="{BG_COLOR}" stroke-width="1.5"/>'
        )
        # Position label cleanly
        lx = min(peak_x + 8, MG_L + CW - 80)
        ly = max(peak_y - 8, MG_T + 12)
        svg.append(
            f'  <text x="{lx:.2f}" y="{ly:.2f}" '
            f'fill="{PEAK_COLOR}" '
            f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
            f'font-size="9.5">{peak_label}</text>'
        )

    # ── Header: Left (Label) & Right (Total in last 60 days) ──────────────────
    svg.append(
        f'  <text x="{MG_L}" y="{MG_T - 12}" '
        f'fill="{LABEL_COLOR}" '
        f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
        f'font-size="11" font-weight="500">Contributions (Daily)</text>'
    )
    svg.append(
        f'  <text x="{SVG_W - MG_R}" y="{MG_T - 12}" '
        f'fill="{TEXT_COLOR}" '
        f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
        f'font-size="11" font-weight="600" text-anchor="end">'
        f'{window_total:,} contributions · Last {WINDOW_DAYS} days</text>'
    )

    svg.append('</svg>')
    return '\n'.join(svg)


# ── CLI entry-point ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate GitHub contribution wave SVG (rolling 60-day window)")
    parser.add_argument("--username", default="Urvity03")
    parser.add_argument("--token",    required=True, help="GitHub token (repo scope)")
    parser.add_argument("--output",   default="assets/github-contribution-wave.svg")
    parser.add_argument("--days",     type=int, default=60, help="Window size in days (default: 60)")
    args = parser.parse_args()

    print(f"[>>] Fetching contribution calendar for @{args.username} ...")
    all_days, calendar_total = fetch_contributions(args.username, args.token)

    # Extract dynamic rolling window of last N days
    window_days = all_days[-args.days:]
    window_counts = [d["count"] for d in window_days]
    window_total = sum(window_counts)

    print(f"[OK] Total in calendar: {calendar_total}")
    print(f"[OK] Window range     : {window_days[0]['date']} -> {window_days[-1]['date']} ({len(window_days)} days)")
    print(f"[OK] Window total     : {window_total} (sum of {len(window_days)} daily counts)")
    print(f"[OK] Max daily        : {max(window_counts)}")
    print(f"[OK] Non-zero days    : {sum(1 for c in window_counts if c > 0)} / {len(window_days)}")

    # Strict validation
    assert len(window_days) == args.days, f"Expected {args.days} days, got {len(window_days)}"
    assert sum(window_counts) == window_total, "Window sum mismatch"

    print("[>>] Generating 60-day wave SVG ...")
    svg = build_svg(window_days, args.username)

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"[OK] Saved -> {args.output} ({len(svg):,} bytes)")


if __name__ == "__main__":
    main()
