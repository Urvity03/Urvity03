#!/usr/bin/env python3
"""
generate_wave.py  v2
Generates a smooth wave/area contribution graph SVG from real GitHub data.

Data source : GitHub GraphQL API → contributionsCollection → contributionCalendar
             sum(contributionDays[].contributionCount) == totalContributions (validated)

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
from collections import defaultdict

# ── Tokyo Night palette ───────────────────────────────────────────────────────
BG_COLOR    = "#1a1b27"
GRID_COLOR  = "#1e2030"
AXIS_COLOR  = "#2a2e45"
TEXT_COLOR  = "#a9b1d6"
LABEL_COLOR = "#565f89"
LINE_COLOR  = "#7aa2f7"
PEAK_COLOR  = "#bb9af7"
ZERO_COLOR  = "#1e2030"   # baseline zero line

# ── Chart layout ──────────────────────────────────────────────────────────────
SVG_W    = 900
SVG_H    = 260
MG_L     = 48   # left  margin (Y-axis labels)
MG_R     = 18   # right margin
MG_T     = 30   # top   margin (title clearance)
MG_B     = 42   # bottom margin (X-axis labels)
CW       = SVG_W - MG_L - MG_R
CH       = SVG_H - MG_T  - MG_B
CORNER_R = 10

# ── Smoothing control ─────────────────────────────────────────────────────────
# Window of 3 preserves individual day spikes while softening pure noise.
SMOOTH_WINDOW = 3

# ── Y-scale: log1p transform ──────────────────────────────────────────────────
# log(1+x) lifts near-zero values dramatically:
#   count=0  ->  0.000  (flat baseline, correct)
#   count=1  ->  0.693  (visible blip)
#   count=5  ->  1.792
#   count=32 ->  3.497  (peak)
# Axis labels still display the original linear contribution counts.
USE_LOG_SCALE = True


# ─────────────────────────────────────────────────────────────────────────────
def fetch_contributions(username: str, token: str):
    """
    Fetch the GitHub contribution calendar via GraphQL.
    Returns (days, total) where:
      days  = [{"date": "YYYY-MM-DD", "count": int}, ...]  sorted oldest→newest
      total = contributionCalendar.totalContributions

    Validation: sum(d["count"] for d in days) == total  (enforced below).
    """
    now          = datetime.now(timezone.utc)
    one_year_ago = now - timedelta(days=365)

    # Exact GraphQL query — uses contributionCalendar exclusively.
    # No Events API, no commit search, no repository history.
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
            "User-Agent":    "contribution-wave-generator/2.0",
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

    # Flatten weeks → days, sort chronologically
    days = sorted(
        [{"date": d["date"], "count": d["contributionCount"]}
         for w in cal["weeks"] for d in w["contributionDays"]],
        key=lambda x: x["date"],
    )

    # ── Integrity check: sum must equal totalContributions ───────────────────
    computed = sum(d["count"] for d in days)
    if computed != total:
        raise RuntimeError(
            f"Data integrity FAILED: sum({computed}) != totalContributions({total}). "
            f"Do not proceed — data is inconsistent."
        )

    return days, total


# ── Smoothing (centred rolling average, small window) ─────────────────────────
def smooth(values: list, window: int) -> list:
    """Centred rolling average.  Window kept small to preserve day-level shape."""
    n, half = len(values), window // 2
    out = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        chunk = values[lo:hi]
        out.append(sum(chunk) / len(chunk))
    return out


# ── Y transform helpers ────────────────────────────────────────────────────────
def y_transform(v: float) -> float:
    """Map raw contribution count -> plot-space value using log1p."""
    if USE_LOG_SCALE:
        return math.log1p(max(0.0, v))
    return max(0.0, v)


def y_transform_inv_approx(pv: float) -> float:
    """Approximate inverse for axis tick labelling."""
    if USE_LOG_SCALE:
        return math.expm1(max(0.0, pv))
    return pv


# ── Catmull-Rom → cubic Bézier SVG path ──────────────────────────────────────
def catmull_rom_path(pts: list) -> str:
    """Return an SVG 'd' string for a smooth curve through all (x, y) points."""
    n = len(pts)
    if n < 2:
        return ""
    segs = [f"M {pts[0][0]:.3f},{pts[0][1]:.3f}"]
    for i in range(1, n):
        p0 = pts[max(0, i - 2)]
        p1 = pts[i - 1]
        p2 = pts[i]
        p3 = pts[min(n - 1, i + 1)]
        cp1x = p1[0] + (p2[0] - p0[0]) / 6
        cp1y = p1[1] + (p2[1] - p0[1]) / 6
        cp2x = p2[0] - (p3[0] - p1[0]) / 6
        cp2y = p2[1] - (p3[1] - p1[1]) / 6
        segs.append(
            f"C {cp1x:.3f},{cp1y:.3f} {cp2x:.3f},{cp2y:.3f} {p2[0]:.3f},{p2[1]:.3f}"
        )
    return " ".join(segs)


# ── SVG builder ───────────────────────────────────────────────────────────────
def build_svg(days: list, total: int, username: str) -> str:
    if not days:
        raise ValueError("Empty days list")

    counts = [d["count"] for d in days]
    n      = len(counts)

    # ── Smoothed series (small window — preserves shape, softens jaggedness) ──
    smoothed = smooth(counts, SMOOTH_WINDOW)

    # ── Transform to plot-space (log1p or linear) ─────────────────────────────
    max_raw  = max(counts) if any(c > 0 for c in counts) else 1
    max_plot = y_transform(max_raw)
    # Add 5 % headroom so peak dot isn't clipped
    y_plot_max = max_plot * 1.05

    # ── Y-axis ticks in LINEAR units (human-readable) ─────────────────────────
    # Generate 5 meaningful tick values in raw contribution space
    tick_step  = max(1, math.ceil(max_raw / 5))
    raw_ticks  = list(range(0, max_raw + tick_step, tick_step))
    if raw_ticks[-1] < max_raw:
        raw_ticks.append(max_raw)

    # ── Coordinate mappers ────────────────────────────────────────────────────
    base_y = MG_T + CH   # SVG Y of the X-axis baseline

    def xc(i: int) -> float:
        return MG_L + (i / (n - 1)) * CW if n > 1 else MG_L

    def yc_raw(raw: float) -> float:
        """Map a raw contribution count -> SVG Y coordinate."""
        plot_val = y_transform(raw)
        ratio    = plot_val / y_plot_max
        return MG_T + CH - ratio * CH

    def yc_smooth(sv: float) -> float:
        """Map a smoothed value (already in raw space) -> SVG Y coordinate."""
        return yc_raw(sv)

    # ── Build chart points from smoothed data ─────────────────────────────────
    pts    = [(xc(i), yc_smooth(smoothed[i])) for i in range(n)]
    curve  = catmull_rom_path(pts)
    area_d = (
        curve
        + f" L {pts[-1][0]:.3f},{base_y:.3f}"
        + f" L {pts[0][0]:.3f},{base_y:.3f} Z"
    )

    # ── Month labels ──────────────────────────────────────────────────────────
    month_ticks = []
    prev_month  = None
    for i, d in enumerate(days):
        dt  = datetime.strptime(d["date"], "%Y-%m-%d")
        key = (dt.year, dt.month)
        if key != prev_month:
            month_ticks.append((i, dt.strftime("%b")))
            prev_month = key

    # ── Peak marker (raw peak, positioned on the SMOOTHED curve) ─────────────
    peak_idx   = counts.index(max(counts))
    peak_x     = xc(peak_idx)
    peak_y_svg = yc_smooth(smoothed[peak_idx])
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

    # ── Grid lines (at each Y-axis tick) ─────────────────────────────────────
    for raw_tick in raw_ticks:
        gy = yc_raw(raw_tick)
        if MG_T - 1 <= gy <= base_y + 1:
            svg.append(
                f'  <line x1="{MG_L}" y1="{gy:.2f}" x2="{MG_L + CW}" y2="{gy:.2f}" '
                f'stroke="{GRID_COLOR}" stroke-width="1"/>'
            )

    # ── Y-axis labels (linear / human-readable counts) ────────────────────────
    for raw_tick in raw_ticks:
        gy = yc_raw(raw_tick)
        if MG_T - 3 <= gy <= base_y + 5:
            svg.append(
                f'  <text x="{MG_L - 7}" y="{gy + 4:.2f}" '
                f'fill="{LABEL_COLOR}" '
                f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
                f'font-size="10" text-anchor="end">{raw_tick}</text>'
            )

    # ── Area fill ─────────────────────────────────────────────────────────────
    svg.append(f'  <path d="{area_d}" fill="url(#wg)" clip-path="url(#cc)"/>')

    # ── Wave line ─────────────────────────────────────────────────────────────
    svg.append(
        f'  <path d="{curve}" fill="none" stroke="{LINE_COLOR}" '
        f'stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round" '
        f'clip-path="url(#cc)"/>'
    )

    # ── X-axis baseline ───────────────────────────────────────────────────────
    svg.append(
        f'  <line x1="{MG_L}" y1="{base_y}" x2="{MG_L + CW}" y2="{base_y}" '
        f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
    )

    # ── Month labels ──────────────────────────────────────────────────────────
    prev_mx = -9999
    for idx, label in month_ticks:
        mx = xc(idx)
        if mx < MG_L + 6:
            continue
        if mx - prev_mx < 38:
            continue
        prev_mx = mx
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

    # ── Peak marker ───────────────────────────────────────────────────────────
    if peak_count > 0:
        svg.append(
            f'  <circle cx="{peak_x:.2f}" cy="{peak_y_svg:.2f}" r="4.5" '
            f'fill="{PEAK_COLOR}" stroke="{BG_COLOR}" stroke-width="1.5"/>'
        )
        lx = min(peak_x + 7, MG_L + CW - 84)
        ly = max(peak_y_svg - 8, MG_T + 11)
        svg.append(
            f'  <text x="{lx:.2f}" y="{ly:.2f}" '
            f'fill="{PEAK_COLOR}" '
            f'font-family="\'Segoe UI\',system-ui,Arial,sans-serif" '
            f'font-size="9.5">{peak_label}: {peak_count}</text>'
        )

    # ── Total contributions label ─────────────────────────────────────────────
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
    parser = argparse.ArgumentParser(description="Generate GitHub contribution wave SVG v2")
    parser.add_argument("--username", default="Urvity03")
    parser.add_argument("--token",    required=True, help="GitHub token (repo scope)")
    parser.add_argument("--output",   default="assets/github-contribution-wave.svg")
    args = parser.parse_args()

    print(f"[>>] Fetching GraphQL contributionCalendar for @{args.username} ...")
    days, total = fetch_contributions(args.username, args.token)

    counts      = [d["count"] for d in days]
    computed    = sum(counts)
    peak_idx    = counts.index(max(counts))

    print(f"[OK] Days     : {len(days)}")
    print(f"[OK] Total    : {total}  (GitHub)")
    print(f"[OK] Computed : {computed}  (sum of daily counts)  --> {'MATCH' if computed == total else 'MISMATCH - ABORT'}")
    if computed != total:
        sys.exit(1)
    print(f"[OK] Peak     : {counts[peak_idx]} on {days[peak_idx]['date']}")
    print(f"[OK] Non-zero : {sum(1 for c in counts if c > 0)} days out of {len(days)}")

    print("[>>] Building SVG ...")
    svg = build_svg(days, total, args.username)

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"[OK] Saved -> {args.output}  ({len(svg):,} bytes)")


if __name__ == "__main__":
    main()
