"""Lambda: get_defensive_assignment

Rank opponents by threat level to prioritize marking or interception.

Threat model:
    threat = (proximity to our goal) + (proximity to ball) + (advancement)
Higher threat = mark this opponent first.

Input:
    my_position:   {"x", "y"}
    opponents:     [{"agentId": str, "position": {"x", "y"}}, ...]
    ball_position: {"x", "y"}

Output:
    assignments: [
        {
            "opp_id": int,
            "position": {"x", "y"},
            "threat_score": float (0-1 normalized),
            "distance_from_me": float,
            "distance_to_ball": float,
            "distance_to_our_goal": float,
            "reason": str
        }, ...
    ] sorted by threat_score desc.
"""
import json
import math

OUR_GOAL_X = -50.0
OUR_GOAL_Y = 0.0


def _parse_agent_id(agent_id: str) -> int:
    if isinstance(agent_id, str) and agent_id.startswith("agentId_"):
        try:
            return int(agent_id.split("_", 1)[1])
        except (ValueError, IndexError):
            return -1
    return -1


def _dist(a, b) -> float:
    return math.hypot(a.get("x", 0.0) - b.get("x", 0.0), a.get("y", 0.0) - b.get("y", 0.0))


def lambda_handler(event, context):
    try:
        args = event.get("arguments", event)
        my_pos = args.get("my_position") or {}
        opponents = args.get("opponents") or []
        ball_pos = args.get("ball_position") or {}

        assignments = []
        for opp in opponents:
            opp_pos = opp.get("position") or {}
            if not opp_pos:
                continue
            opp_id = _parse_agent_id(opp.get("agentId", ""))
            if opp_id < 0:
                continue

            dist_to_goal = _dist(opp_pos, {"x": OUR_GOAL_X, "y": OUR_GOAL_Y})
            dist_to_ball = _dist(opp_pos, ball_pos) if ball_pos else 100.0
            dist_from_me = _dist(my_pos, opp_pos) if my_pos else 100.0

            # Threat components (each 0-1)
            # 1) Advancement: opponent x closer to our goal (x=-50) = more dangerous
            # Map [-50, +50] -> [1.0, 0.0]
            adv = max(0.0, min(1.0, (50.0 - opp_pos.get("x", 0.0)) / 100.0))
            # 2) Ball proximity: closer to ball = more threat
            ball_prox = max(0.0, 1.0 - dist_to_ball / 30.0)
            # 3) Goal proximity: closer to our goal = more urgent
            goal_prox = max(0.0, 1.0 - dist_to_goal / 60.0)

            threat = 0.4 * adv + 0.3 * ball_prox + 0.3 * goal_prox
            threat = round(min(1.0, threat), 3)

            reason_parts = []
            if goal_prox > 0.7:
                reason_parts.append("near our goal")
            if ball_prox > 0.6:
                reason_parts.append("near ball")
            if adv > 0.7:
                reason_parts.append("advanced position")
            reason = ", ".join(reason_parts) if reason_parts else "background threat"

            assignments.append({
                "opp_id": opp_id,
                "position": opp_pos,
                "threat_score": threat,
                "distance_from_me": round(dist_from_me, 2),
                "distance_to_ball": round(dist_to_ball, 2),
                "distance_to_our_goal": round(dist_to_goal, 2),
                "reason": reason,
            })

        assignments.sort(key=lambda a: a["threat_score"], reverse=True)

        return {"statusCode": 200, "body": json.dumps({"assignments": assignments})}

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e), "assignments": []}),
        }