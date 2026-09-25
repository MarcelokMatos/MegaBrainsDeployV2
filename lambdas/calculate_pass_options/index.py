"""Lambda: calculate_pass_options

Given the current game state, estimate the pass success probability
for each teammate. Higher probability = safer pass.

Input:
    my_position: {"x": float, "y": float}
    teammates:  [{"agentId": str, "position": {"x", "y"}}, ...]
    opponents:  [{"agentId": str, "position": {"x", "y"}}, ...]

Output:
    options: [
        {
            "target_player_id": int,
            "target_position": {"x", "y"},
            "distance": float,
            "success_probability": float (0-1),
            "reason": str
        }, ...
    ] sorted by success_probability desc.
"""
import json
import math


def _parse_agent_id(agent_id: str) -> int:
    """agentId_3 -> 3."""
    if isinstance(agent_id, str) and agent_id.startswith("agentId_"):
        try:
            return int(agent_id.split("_", 1)[1])
        except (ValueError, IndexError):
            return -1
    return -1


def _dist(a, b) -> float:
    return math.hypot(a.get("x", 0.0) - b.get("x", 0.0), a.get("y", 0.0) - b.get("y", 0.0))


def _pass_success_probability(from_pos, to_pos, opponents) -> tuple[float, str]:
    """Estimate probability that a pass from from_pos to to_pos succeeds.

    Model: base probability decays with distance; each opponent close to the
    passing line reduces probability further.
    """
    d = _dist(from_pos, to_pos)
    if d < 0.5:
        return 1.0, "target at same position"

    # Base probability: 0.95 at 5m, drops linearly to 0.35 at 40m
    base = max(0.35, 0.95 - 0.015 * max(0.0, d - 5.0))

    # Vector along pass line
    dx = to_pos["x"] - from_pos["x"]
    dy = to_pos["y"] - from_pos["y"]
    nx, ny = dx / d, dy / d

    interceptors = 0
    closest_perp = 999.0
    for opp in opponents:
        op = opp.get("position", {})
        ox = op.get("x", 0.0) - from_pos["x"]
        oy = op.get("y", 0.0) - from_pos["y"]
        proj = ox * nx + oy * ny
        # Skip opponents behind us or beyond the target
        if proj <= 0 or proj >= d:
            continue
        perp = abs(ox * ny - oy * nx)
        if perp < closest_perp:
            closest_perp = perp
        if perp <= 2.5:
            interceptors += 1

    # Each interceptor costs 25% probability
    prob = base * max(0.05, 1.0 - 0.25 * interceptors)

    if interceptors == 0:
        reason = f"clear lane, distance {d:.1f}m"
    elif interceptors == 1:
        reason = f"1 opponent near lane ({closest_perp:.1f}m off), distance {d:.1f}m"
    else:
        reason = f"{interceptors} opponents near lane, distance {d:.1f}m"

    return round(prob, 3), reason


def lambda_handler(event, context):
    """Entry point. `event` is the MCP tool call arguments (dict)."""
    try:
        args = event.get("arguments", event)  # MCP nests args under "arguments"
        my_pos = args.get("my_position") or {}
        teammates = args.get("teammates") or []
        opponents = args.get("opponents") or []

        if not my_pos or not teammates:
            return {"statusCode": 200, "body": json.dumps({"options": []})}

        options = []
        for tm in teammates:
            tm_pos = tm.get("position") or {}
            if not tm_pos:
                continue
            tid = _parse_agent_id(tm.get("agentId", ""))
            if tid < 0:
                continue
            prob, reason = _pass_success_probability(my_pos, tm_pos, opponents)
            options.append({
                "target_player_id": tid,
                "target_position": tm_pos,
                "distance": round(_dist(my_pos, tm_pos), 2),
                "success_probability": prob,
                "reason": reason,
            })

        options.sort(key=lambda o: o["success_probability"], reverse=True)
        return {"statusCode": 200, "body": json.dumps({"options": options})}

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e), "options": []}),
        }