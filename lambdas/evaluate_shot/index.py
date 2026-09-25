"""Lambda: evaluate_shot

Estimate the success probability of shooting at goal from the current position,
and recommend an aim point (target_y and keyword LEFT/RIGHT/CENTER).

Input:
    my_position:      {"x": float, "y": float}
    opp_gk_position:  {"x": float, "y": float}
    opponents:        [{"position": {"x","y"}}, ...]   (in-lane blockers)

Output:
    probability:    float (0-1)
    target_y:       float (recommended aim y-coordinate, clamped to [-1.8, +1.8])
    target_keyword: "LEFT" | "RIGHT" | "CENTER"
    distance:       float (meters to goal center)
    reason:         str
"""
import json
import math

OPP_GOAL_X = 50.0
OPP_GOAL_Y = 0.0
GOAL_HALF_WIDTH = 3.0  # goal is y in [-3, +3]
Y_CLAMP = 1.8          # aim clamp per V3 rules


def _dist(a, b) -> float:
    return math.hypot(a.get("x", 0.0) - b.get("x", 0.0), a.get("y", 0.0) - b.get("y", 0.0))


def _shooting_lane_blocked(my_pos, target_y, opponents) -> int:
    """Count opponents in the shooting lane from my_pos to (50, target_y)."""
    target = {"x": OPP_GOAL_X, "y": target_y}
    dx = target["x"] - my_pos["x"]
    dy = target["y"] - my_pos["y"]
    d = math.hypot(dx, dy)
    if d < 0.5:
        return 0
    nx, ny = dx / d, dy / d
    count = 0
    for opp in opponents:
        op = opp.get("position", {})
        ox = op.get("x", 0.0) - my_pos["x"]
        oy = op.get("y", 0.0) - my_pos["y"]
        proj = ox * nx + oy * ny
        if proj <= 0 or proj >= d:
            continue
        perp = abs(ox * ny - oy * nx)
        if perp <= 2.0:
            count += 1
    return count


def lambda_handler(event, context):
    try:
        args = event.get("arguments", event)
        my_pos = args.get("my_position") or {}
        gk_pos = args.get("opp_gk_position") or {"x": 48.0, "y": 0.0}
        opponents = args.get("opponents") or []

        if not my_pos:
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "probability": 0.0,
                    "target_y": 0.0,
                    "target_keyword": "CENTER",
                    "distance": 0.0,
                    "reason": "no position",
                }),
            }

        # Distance to goal center
        d = _dist(my_pos, {"x": OPP_GOAL_X, "y": OPP_GOAL_Y})

        # Recommend aim on opposite side of GK
        gk_y = gk_pos.get("y", 0.0)
        if gk_y >= 0.5:
            target_y = -Y_CLAMP
            keyword = "LEFT"
        elif gk_y <= -0.5:
            target_y = Y_CLAMP
            keyword = "RIGHT"
        else:
            # GK centered - pick side based on my y
            target_y = -Y_CLAMP if my_pos.get("y", 0.0) >= 0 else Y_CLAMP
            keyword = "LEFT" if target_y < 0 else "RIGHT"

        # If very close to goal, CENTER can work
        if d <= 12.0:
            # Prefer keeping recommendation but flag CENTER acceptable
            pass

        # Base probability from distance (rough soccer stats)
        if d <= 10:
            base = 0.65
        elif d <= 15:
            base = 0.45
        elif d <= 22:
            base = 0.25
        elif d <= 30:
            base = 0.12
        else:
            base = 0.04

        # Angle penalty: shots from wide angles are harder
        # Angle from goal line: atan2(|my.y|, 50 - my.x)
        angle_deg = math.degrees(math.atan2(abs(my_pos.get("y", 0.0)), max(0.5, OPP_GOAL_X - my_pos.get("x", 0.0))))
        if angle_deg > 30:
            base *= max(0.3, 1.0 - (angle_deg - 30) / 60.0)

        # Blockers in shooting lane
        blockers = _shooting_lane_blocked(my_pos, target_y, opponents)
        base *= max(0.15, 1.0 - 0.2 * blockers)

        probability = round(min(0.95, base), 3)

        reason = f"distance {d:.1f}m, angle {angle_deg:.0f}deg"
        if blockers:
            reason += f", {blockers} blocker(s) in lane"
        reason += f", aim {keyword} (opp GK at y={gk_y:.1f})"

        return {
            "statusCode": 200,
            "body": json.dumps({
                "probability": probability,
                "target_y": round(target_y, 2),
                "target_keyword": keyword,
                "distance": round(d, 2),
                "reason": reason,
            }),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e),
                "probability": 0.0,
                "target_y": 0.0,
                "target_keyword": "CENTER",
                "distance": 0.0,
                "reason": "error",
            }),
        }