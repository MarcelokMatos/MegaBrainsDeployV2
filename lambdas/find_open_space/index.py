"""Lambda: find_open_space

Find the grid cell within a zone with the most distance from opponents.
Useful for off-ball repositioning.

Input:
    zone:               "attack" | "midfield" | "defense"
    opponents:          [{"position": {"x","y"}}, ...]
    current_position:   {"x", "y"} (optional, for preferring nearby cells)

Output:
    target_position: {"x", "y"}
    space_score:     float (distance to nearest opponent, meters)
    reason:          str
"""
import json
import math


# Zone boundaries (x_min, x_max, y_min, y_max)
ZONES = {
    "attack":   (25.0, 45.0, -12.0, 12.0),
    "midfield": (-15.0, 15.0, -12.0, 12.0),
    "defense":  (-40.0, -10.0, -12.0, 12.0),
}


def _dist(a, b) -> float:
    return math.hypot(a.get("x", 0.0) - b.get("x", 0.0), a.get("y", 0.0) - b.get("y", 0.0))


def _closest_opp_dist(pos, opponents) -> float:
    if not opponents:
        return 999.0
    return min(_dist(pos, o.get("position", {})) for o in opponents)


def lambda_handler(event, context):
    try:
        args = event.get("arguments", event)
        zone = (args.get("zone") or "midfield").lower()
        opponents = args.get("opponents") or []
        current = args.get("current_position") or {}

        if zone not in ZONES:
            zone = "midfield"

        xmin, xmax, ymin, ymax = ZONES[zone]

        # 5x5 grid
        step_x = (xmax - xmin) / 4.0
        step_y = (ymax - ymin) / 4.0

        best_pos = {"x": (xmin + xmax) / 2.0, "y": (ymin + ymax) / 2.0}
        best_score = -1.0

        for i in range(5):
            for j in range(5):
                x = xmin + i * step_x
                y = ymin + j * step_y
                pos = {"x": x, "y": y}
                space = _closest_opp_dist(pos, opponents)

                # Slight preference for cells nearer to current position (efficiency)
                if current:
                    dist_from_current = _dist(pos, current)
                    score = space - 0.1 * dist_from_current
                else:
                    score = space

                if score > best_score:
                    best_score = score
                    best_pos = pos

        return {
            "statusCode": 200,
            "body": json.dumps({
                "target_position": {"x": round(best_pos["x"], 1), "y": round(best_pos["y"], 1)},
                "space_score": round(_closest_opp_dist(best_pos, opponents), 2),
                "reason": f"zone={zone}, best cell has {_closest_opp_dist(best_pos, opponents):.1f}m clearance",
            }),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e),
                "target_position": {"x": 0.0, "y": 0.0},
                "space_score": 0.0,
                "reason": "error",
            }),
        }