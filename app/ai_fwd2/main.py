"""
MegaBrainsFC Forward 2 (Right) Agent - AgentCore Runtime
Player ID: 4 | Position: FWD2 (Right Forward)
Zone: X[+5, +45], Y[0, +12]
Home: X=+25, Y=+8
Role: Score goals from right side, create crosses to FWD1
"""
import json
import math
import os
from strands import Agent
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from model.load import load_model

app = BedrockAgentCoreApp()
log = app.logger
# ==================== GATEWAY MCP TOOLS ====================
_gateway_tools = []
try:
    from mcp_client.client import get_gateway_mcp_client
    _mcp_client = get_gateway_mcp_client()
    if _mcp_client is not None:
        _mcp_client.start()
        _gateway_tools = _mcp_client.list_tools_sync()
        log.info(f"Gateway MCP: {len(_gateway_tools)} tools loaded")
    else:
        log.info("Gateway MCP: no GATEWAY_URL, running without tools")
except Exception as _mcp_err:
    log.warning(f"Gateway MCP init failed: {_mcp_err}")
    _gateway_tools = []

# ==================== CONSTANTES DE POSIÇÃO ====================
MY_PLAYER_ID = 4
ACTOR_ID = "actor_fwd2"
POSITION = "FWD2"
ZONE_X_MIN = 5
ZONE_X_MAX = 45
ZONE_Y_MIN = 0     # FWD2 fica no lado DIREITO (y positivo)
ZONE_Y_MAX = 12
HOME_X = 25
HOME_Y = 8

# Opponent goal position
OPP_GOAL_X = 50
OPP_GOAL_Y = 0
GOAL_WIDTH = 6

# ==================== V3 REFINEMENT THRESHOLDS ====================
V3_MAX_CARRY = 5.0
V3_UNDER_PRESSURE_DIST = 3.0
V3_BACKPASS_TOLERANCE = 10.0
V3_SHOOT_MIN_X = 28.0
V3_CENTER_MAX_DIST = 12.0
V3_SHOT_Y_CLAMP = 1.8
V3_PASS_CONE_HALF_WIDTH = 2.0

# ==================== SYSTEM PROMPT ====================
SYSTEM_PROMPT = f"""You are an AI FORWARD RIGHT (player {MY_PLAYER_ID}) in a 5v5 soccer match.
Your position is FWD2 (Right Forward Striker). Your JOB is to SCORE GOALS and CREATE from the right side.
You MUST respond with ONLY a valid JSON array of commands. No text, no explanation.

======== YOUR IDENTITY ========
- Player ID: {MY_PLAYER_ID}
- Position: RIGHT FORWARD (Striker)
- Zone: X between {ZONE_X_MIN} and {ZONE_X_MAX}, Y between {ZONE_Y_MIN} and {ZONE_Y_MAX} (RIGHT SIDE)
- Home base: X={HOME_X}, Y={HOME_Y}
- YOU ARE A STRIKER - your job is to SCORE and CROSS

======== TEAM ROSTER ========
- Player 0: GK (Goalkeeper) - at x=-48
- Player 1: DEF (Defender) - at x=-25
- Player 2: MID (Midfielder) - center of field
- Player 3: FWD1 (Left Forward) - your partner on the left (y negative)
- Player 4: YOU (Right Forward)

======== FIELD INFO ========
- Opponent goal: X=50, Y between -3 and +3
- Goal center: (50, 0)
- Field limits: X[-50,+50], Y[-15,+15]

======== DECISION TREE (in priority order) ========

STEP 1: ANALYZE STATE
- My position and stamina
- Ball possession
- Distance to opponent goal
- Opponent GK position (away agentId_0)
- Space around me
- FWD1 position (my crossing target)

STEP 2: DECIDE ACTION

📌 SITUATION A: I HAVE THE BALL (possessionAgentId == "agentId_{MY_PLAYER_ID}")

   🎯 PRIORITY 1 - SHOOT (if in shooting range):
   - If my x > 35: SHOOT immediately
   - If my x > 25 AND distance to goal < 25m: SHOOT
   - Aim (check opponent GK position):
     - If opp GK at y > 1: SHOOT target "LEFT" (aim y=-2)
     - If opp GK at y < -1: SHOOT target "RIGHT" (aim y=+2)
     - Else: SHOOT target "CENTER"
   - Power: always 1.0
     Command: {{"commandType":"SHOOT","playerId":{MY_PLAYER_ID},"parameters":{{"power":1.0,"target":"CENTER"}},"duration":0}}

   🎯 PRIORITY 2 - CROSS to FWD1 (right wing specialty):
   - If I'm on wing (y > 5) AND close to goal (x > 30):
     - CROSS (long PASS) to FWD1 (player 3) for a header opportunity
     Command: {{"commandType":"PASS","playerId":{MY_PLAYER_ID},"parameters":{{"target_player_id":3,"power":0.8}},"duration":0}}

   🎯 PRIORITY 3 - PASS TO FWD1 (better positioned):
   - If FWD1 is CLOSER to goal than me: PASS
   - If lane is clear
     Command: {{"commandType":"PASS","playerId":{MY_PLAYER_ID},"parameters":{{"target_player_id":3,"power":0.7}},"duration":0}}

   🎯 PRIORITY 4 - CARRY BALL FORWARD:
   - If space ahead is clear
   - Move toward goal
     Command: {{"commandType":"MOVE_TO","playerId":{MY_PLAYER_ID},"parameters":{{"x":40.0,"y":5.0}},"duration":0}}

   🎯 PRIORITY 5 - PASS BACK to MID:
   - If blocked, pass back to player 2
     Command: {{"commandType":"PASS","playerId":{MY_PLAYER_ID},"parameters":{{"target_player_id":2,"power":0.6}},"duration":0}}

📌 SITUATION B: TEAMMATE HAS BALL

   🏃 OFF-THE-BALL MOVEMENT:
   - MID has ball: RUN to open space (x=+30, y=+8)
   - DEF has ball: Wait at x=+25, y=+8 (home)
   - FWD1 has ball: Support at x=+35, y=+5 (close to goal for rebound)

   🎯 MAKE DIAGONAL RUNS:
   - Move behind opponent defenders
   - Create width on right side
   - Position for through ball or cross reception

📌 SITUATION C: OPPONENT HAS BALL

   → Move to HOME position (x=+25, y=+8)
   → Wait for counter-attack
   → DO NOT chase to defense (DEF/MID handle that)
   → Save stamina

📌 SITUATION D: BALL IS FREE

   → If ball is in my zone (x > +5 AND y > 0): MOVE_TO ball
   → Else: stay at HOME position

======== SPECIAL BEHAVIORS ========

🎪 REBOUND POSITIONING:
- After I shoot: STAY in the box for rebound
- After FWD1 shoots: I move to x=+40, y=+3 for rebound
- After MID shoots: I run to x=+40 for rebound

🎯 SHOOTING ACCURACY:
- ALWAYS check opponent GK position before shooting
- Best angles: y between 0 and +5 (for right forward)
- Prefer shooting from x=35-45 (close range)

🏃 MOVEMENT PATTERNS:
- I play on the RIGHT side (y positive)
- Never go to y < -2 (that's FWD1's territory)
- Make diagonal runs toward goal
- Create width when needed (y=+10) to stretch defense

🎯 CROSSING SPECIALTY (Right Wing):
- When I'm at wing position (y > 5) close to goal:
  - CROSS (long PASS) to FWD1 in the center/left
  - This creates high-percentage scoring chances
  Command: {{"commandType":"PASS","playerId":{MY_PLAYER_ID},"parameters":{{"target_player_id":3,"power":0.8}},"duration":0}}

🔀 ANTI-CLUSTERING:
- Never move to a position where FWD1 is within 8m
- Keep width - I'm RIGHT, FWD1 is LEFT
- If FWD1 comes right: I move central or wait

🚦 SCORE-BASED STRATEGY:
- Winning: play safe, hold position at x=+20
- Losing: push forward AGGRESSIVELY, take more shots
- Tied: balanced attacking play

⏱️ TIME-BASED:
- Final 20% of match (gameTime > 80%):
  - If winning: hold position
  - If losing: HIGH RISK - shoot from anywhere, cross more

⚡ FAST BREAK:
- If we JUST recovered ball in our half:
  - SPRINT forward IMMEDIATELY
  - Position at x=+35 to receive through ball
  - CREATE the counter attack

======== ONE-TWO PLAY WITH FWD1 ========
- If FWD1 is close to me and marked:
  - Short pass to FWD1 with power 0.5
  - Move to open space for return pass
- Coordinate one-two combinations near the box

======== TACTICAL TOOLS (Gateway MCP) ========
You have access to tactical analysis tools. USE THEM before key decisions (max 1 tool call per tick to save latency):
- evaluate_shot: Shot probability + best aim. Call before every SHOOT decision when x > 25.
- calculate_pass_options: Pass success probability. Call before PASS in tight spots.
- find_open_space: Locates open zones. Call when making off-ball runs.
- get_defensive_assignment: Skip - you don't defend.
Prefer: evaluate_shot, calculate_pass_options, find_open_space.
======== MEMORY (Short-Term) ========
You have MEMORY of previous ticks in this match. Use recalled history to:
- Anticipate repeated shot patterns from opponents
- Remember which opponents are most dangerous in specific zones
- Adjust positioning based on tendencies observed earlier in this match
- Avoid repeating mistakes (e.g. a pass lane that got intercepted twice)
- SHOT RECALL: If you scored from a position/angle earlier in this match, take a similar shot again when you get the ball nearby. If a shot was SAVED or missed, aim to a DIFFERENT corner next time.
Do NOT mention memory in your JSON output - use it only to inform decisions.

======== CRITICAL RULES ========
1. Stay in zone: X between {ZONE_X_MIN} and {ZONE_X_MAX}
2. Stay in zone: Y between {ZONE_Y_MIN} and {ZONE_Y_MAX} (RIGHT SIDE ONLY)
3. NEVER go to Y < 0 (that's FWD1's zone)
4. NEVER go to X < 5 (don't come back to defense)
5. Your #1 job is SHOOTING - be goal-hungry
6. Your #2 job is CROSSING to FWD1
7. When teammate has ball: make INTELLIGENT runs
8. Output ONLY a JSON array of commands, no explanations

======== V3 REFINEMENT RULES (STRICT) ========
- PASS FIRST (build-up only, x <= 28): When you have the ball in your own half or midfield, PASS if any teammate is open. Never carry more than 5 meters.
- SHOOT FIRST (attacking third, x > 28): When you have the ball with x > 28, SHOOT is your TOP priority. The PASS FIRST rule DOES NOT apply here - take the shot. Only pass instead if a teammate is CLEARLY closer to goal AND has a clean lane.
- FORWARD PASSES: Prefer teammates with x >= your x. NEVER pass to a teammate whose x is more than 10m behind you, unless an opponent is within 3m of you.
- SHOT AIM: Do NOT use target "CENTER" unless distance to opponent goal < 12m. Prefer LEFT or RIGHT opposite to opponent GK. Keep aim |y| <= 1.8 (avoid the posts). Power always 1.0.

======== SHOT DECISION GUIDE ========
When x > 28 with the ball: GREEN LIGHT to shoot. Do NOT hesitate.
Distance from you to goal (50, 0):
- <= 15m: SHOOT power 1.0 no matter what (very high conversion)
- 15-22m: SHOOT if you have any reasonable angle
- > 22m: MOVE_TO to close the gap first, then shoot next tick

======== OUTPUT FORMAT ========
[{{"commandType":"MOVE_TO","playerId":{MY_PLAYER_ID},"parameters":{{"x":25.0,"y":8.0}},"duration":0}}]
"""

# ==================== HELPER FUNCTIONS ====================
def distance(p1, p2):
    """Calculate distance between two points."""
    return math.sqrt((p1["x"] - p2["x"])**2 + (p1["y"] - p2["y"])**2)

def distance_to_goal(my_pos):
    """Distance to opponent goal."""
    return math.sqrt((OPP_GOAL_X - my_pos["x"])**2 + (OPP_GOAL_Y - my_pos["y"])**2)

def calculate_shot_target(opponent_gk_pos):
    """Calculate best shot target based on opponent GK position."""
    if opponent_gk_pos["y"] > 1:
        return "LEFT"
    elif opponent_gk_pos["y"] < -1:
        return "RIGHT"
    return "CENTER"

def is_lane_open_to_goal(my_pos, opponents):
    """Check if shooting lane is clear."""
    for opp in opponents:
        opp_pos = opp.get("position", {})
        if my_pos["x"] < opp_pos["x"] < OPP_GOAL_X:
            y_between = min(my_pos["y"], OPP_GOAL_Y) - 2 <= opp_pos["y"] <= max(my_pos["y"], OPP_GOAL_Y) + 2
            if y_between:
                return False
    return True

def is_crossing_position(my_pos):
    """Check if I'm in a good crossing position."""
    return my_pos["x"] > 30 and my_pos["y"] > 5

# ==================== V3 REFINEMENT HELPERS ====================
def _v3_extract_state(payload, prompt_text):
    """Best-effort extraction of the game-state JSON from payload dict or embedded in the prompt."""
    if isinstance(payload, dict):
        for k in ("gameState", "game_state", "state", "match", "matchState"):
            v = payload.get(k)
            if isinstance(v, dict) and ("ball" in v or "players" in v):
                return v
    try:
        text = prompt_text if isinstance(prompt_text, str) else str(prompt_text)
        s = 0
        while True:
            idx = text.find("{", s)
            if idx == -1:
                break
            depth = 0
            end = -1
            for i in range(idx, len(text)):
                c = text[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end == -1:
                break
            try:
                obj = json.loads(text[idx:end + 1])
                if isinstance(obj, dict) and ("ball" in obj or "players" in obj):
                    return obj
            except Exception:
                pass
            s = end + 1
    except Exception:
        pass
    return None


def _v3_dist(a, b):
    return math.hypot(a.get("x", 0.0) - b.get("x", 0.0), a.get("y", 0.0) - b.get("y", 0.0))


def _v3_my_pos(state, my_id):
    if not state:
        return None
    for p in state.get("players", []):
        if p.get("teamCode") == "home" and p.get("agentId") == f"agentId_{my_id}":
            return p.get("position")
    return None


def _v3_teammates(state, my_id):
    if not state:
        return []
    return [p for p in state.get("players", [])
            if p.get("teamCode") == "home"
            and p.get("agentId") != f"agentId_{my_id}"
            and isinstance(p.get("position"), dict)]


def _v3_opponents(state):
    if not state:
        return []
    return [p for p in state.get("players", [])
            if p.get("teamCode") == "away" and isinstance(p.get("position"), dict)]


def _v3_opp_gk(state):
    opps = _v3_opponents(state)
    if not opps:
        return None
    return max(opps, key=lambda p: p["position"].get("x", 0.0))


def _v3_teammate_num(tm):
    aid = tm.get("agentId", "")
    if isinstance(aid, str) and aid.startswith("agentId_"):
        try:
            return int(aid.split("_", 1)[1])
        except Exception:
            return None
    return None


def _v3_has_possession(state, my_id):
    if not state:
        return False
    ball = state.get("ball", {})
    return ball.get("possessionAgentId") == f"agentId_{my_id}"


def _v3_ball_pos(state):
    if not state:
        return None
    ball = state.get("ball", {})
    pos = ball.get("position")
    return pos if isinstance(pos, dict) else None


def _v3_under_pressure(my_pos, opponents):
    if not my_pos:
        return False
    return any(_v3_dist(my_pos, o.get("position", {})) <= V3_UNDER_PRESSURE_DIST for o in opponents)


def _v3_in_pass_cone(from_pos, to_pos, opponents):
    dx = to_pos.get("x", 0.0) - from_pos.get("x", 0.0)
    dy = to_pos.get("y", 0.0) - from_pos.get("y", 0.0)
    d = math.hypot(dx, dy)
    if d < 0.5:
        return True
    nx, ny = dx / d, dy / d
    for opp in opponents:
        op = opp.get("position", {})
        ox = op.get("x", 0.0) - from_pos.get("x", 0.0)
        oy = op.get("y", 0.0) - from_pos.get("y", 0.0)
        proj = ox * nx + oy * ny
        if proj <= 0 or proj >= d:
            continue
        perp = abs(ox * ny - oy * nx)
        if perp <= V3_PASS_CONE_HALF_WIDTH:
            return False
    return True


def _v3_most_forward_teammate_in_cone(my_pos, teammates, opponents, min_x=None):
    cands = []
    for tm in teammates:
        pos = tm.get("position")
        if not pos:
            continue
        if min_x is not None and pos.get("x", 0.0) < min_x:
            continue
        if _v3_in_pass_cone(my_pos, pos, opponents):
            cands.append(tm)
    if not cands:
        return None
    return max(cands, key=lambda tm: tm.get("position", {}).get("x", -999.0))


def _v3_dist_to_opp_goal(pos):
    return math.hypot(OPP_GOAL_X - pos.get("x", 0.0), OPP_GOAL_Y - pos.get("y", 0.0))


def _v3_refine(cmd, state, my_id):
    """Apply V3 rules to a single command. May return a new command or the same one mutated."""
    if not state or not isinstance(cmd, dict):
        return cmd

    my_pos = _v3_my_pos(state, my_id)
    if not my_pos:
        return cmd

    teammates = _v3_teammates(state, my_id)
    opponents = _v3_opponents(state)
    has_ball = _v3_has_possession(state, my_id)
    under_pressure = _v3_under_pressure(my_pos, opponents)
    ctype = cmd.get("commandType")

    # R1: pass-first + carry clamp
    # NOTE (V3.1): SKIP for strikers in the attacking third (x > 28) - they need to carry/shoot freely.
    if has_ball and ctype == "MOVE_TO" and not under_pressure and my_pos.get("x", 0.0) <= 28.0:
        target = _v3_most_forward_teammate_in_cone(my_pos, teammates, opponents)
        if target is not None:
            tnum = _v3_teammate_num(target)
            if tnum is not None:
                log.info(f"V3 R1.3: MOVE_TO -> PASS(target={tnum})")
                return {
                    "commandType": "PASS",
                    "playerId": my_id,
                    "parameters": {"target_player_id": tnum, "power": 0.7},
                    "duration": 0,
                }

    if has_ball and ctype == "MOVE_TO":
        ball_pos = _v3_ball_pos(state) or my_pos
        params = cmd.get("parameters", {})
        tx = params.get("x", ball_pos.get("x", 0.0))
        ty = params.get("y", ball_pos.get("y", 0.0))
        dx = tx - ball_pos.get("x", 0.0)
        dy = ty - ball_pos.get("y", 0.0)
        d = math.hypot(dx, dy)
        if d > V3_MAX_CARRY:
            scale = V3_MAX_CARRY / d
            params["x"] = ball_pos.get("x", 0.0) + dx * scale
            params["y"] = ball_pos.get("y", 0.0) + dy * scale
            cmd["parameters"] = params
            log.info(f"V3 R1.4: MOVE_TO clamped carry {d:.1f}m -> 5.0m")

    # R2: forward-biased passing
    if ctype == "PASS" and not under_pressure:
        params = cmd.get("parameters", {})
        target_id = params.get("target_player_id")
        target_pos = None
        for tm in teammates:
            if _v3_teammate_num(tm) == target_id:
                target_pos = tm.get("position")
                break
        if target_pos and target_pos.get("x", 0.0) < my_pos.get("x", 0.0) - V3_BACKPASS_TOLERANCE:
            alt = _v3_most_forward_teammate_in_cone(
                my_pos, teammates, opponents,
                min_x=my_pos.get("x", 0.0) - V3_BACKPASS_TOLERANCE,
            )
            if alt is not None:
                anum = _v3_teammate_num(alt)
                if anum is not None and anum != target_id:
                    params["target_player_id"] = anum
                    if "power" not in params:
                        params["power"] = 0.7
                    cmd["parameters"] = params
                    log.info(f"V3 R2.3: backpass rewritten target {target_id} -> {anum}")
            else:
                new_x = min(my_pos.get("x", 0.0) + 5.0, 45.0)
                log.info("V3 R2.4: backpass w/ no forward option -> MOVE_TO forward")
                return {
                    "commandType": "MOVE_TO",
                    "playerId": my_id,
                    "parameters": {"x": new_x, "y": my_pos.get("y", 0.0)},
                    "duration": 0,
                }

    # R2.7: short backpass to GK -> CLEAR_OVERRIDE
    if my_id != 0 and ctype == "PASS" and not under_pressure:
        params = cmd.get("parameters", {})
        if params.get("target_player_id") == 0:
            alt = _v3_most_forward_teammate_in_cone(
                my_pos, teammates, opponents, min_x=my_pos.get("x", 0.0)
            )
            if alt is not None:
                log.info("V3 R2.7: PASS-to-GK -> CLEAR_OVERRIDE forward")
                return {
                    "commandType": "CLEAR_OVERRIDE",
                    "playerId": my_id,
                    "parameters": {"power": 1.0},
                    "duration": 0,
                }

    # R3: shot selection and aim
    if ctype == "SHOOT":
        my_x = my_pos.get("x", 0.0)
        if my_x <= V3_SHOOT_MIN_X:
            fwd_tm = _v3_most_forward_teammate_in_cone(
                my_pos, teammates, opponents, min_x=my_x
            )
            if fwd_tm is not None:
                fnum = _v3_teammate_num(fwd_tm)
                if fnum is not None:
                    log.info(f"V3 R3.5: SHOOT at x={my_x:.1f} -> PASS(target={fnum})")
                    return {
                        "commandType": "PASS",
                        "playerId": my_id,
                        "parameters": {"target_player_id": fnum, "power": 0.8},
                        "duration": 0,
                    }
            new_x = min(my_x + 5.0, 45.0)
            log.info(f"V3 R3.6: SHOOT at x={my_x:.1f} w/ no forward option -> MOVE_TO forward")
            return {
                "commandType": "MOVE_TO",
                "playerId": my_id,
                "parameters": {"x": new_x, "y": my_pos.get("y", 0.0)},
                "duration": 0,
            }

        params = cmd.get("parameters", {})
        dgoal = _v3_dist_to_opp_goal(my_pos)
        target_kw = params.get("target")
        opp_gk = _v3_opp_gk(state)
        gk_y = opp_gk.get("position", {}).get("y", 0.0) if opp_gk else 0.0

        if target_kw == "CENTER" and dgoal > V3_CENTER_MAX_DIST:
            params["target"] = "LEFT" if gk_y >= 0.0 else "RIGHT"
            target_kw = params["target"]
            log.info(f"V3 R3.7: SHOOT target CENTER -> {target_kw} (dist={dgoal:.1f}m)")

        if "y" in params:
            y = params.get("y", 0.0)
            if y > V3_SHOT_Y_CLAMP:
                params["y"] = V3_SHOT_Y_CLAMP
                log.info(f"V3 R3.8: SHOOT y clamped {y:.1f} -> {V3_SHOT_Y_CLAMP}")
            elif y < -V3_SHOT_Y_CLAMP:
                params["y"] = -V3_SHOT_Y_CLAMP
                log.info(f"V3 R3.8: SHOOT y clamped {y:.1f} -> -{V3_SHOT_Y_CLAMP}")

        if target_kw in ("LEFT", "RIGHT") and "y" not in params:
            params["y"] = -V3_SHOT_Y_CLAMP if target_kw == "LEFT" else V3_SHOT_Y_CLAMP

        params["power"] = 1.0
        cmd["parameters"] = params

    return cmd


# ==================== FALLBACK ====================
FALLBACK_CMD = [{
    "commandType": "MOVE_TO",
    "playerId": MY_PLAYER_ID,
    "parameters": {"x": HOME_X, "y": HOME_Y},
    "duration": 0
}]

# ==================== PARSE & SAFETY RAILS ====================
def parse_commands(text, state=None):
    """Extract JSON array from LLM response with V2 safety rails + V3 refinements."""
    text = text.strip()
    start = text.find('[')
    end = text.rfind(']')

    if start == -1 or end == -1 or end <= start:
        return FALLBACK_CMD

    try:
        cmds = json.loads(text[start:end+1])
        if not isinstance(cmds, list) or len(cmds) == 0:
            return FALLBACK_CMD

        refined = []
        for cmd in cmds:
            if not isinstance(cmd, dict):
                continue

            cmd["playerId"] = MY_PLAYER_ID
            cmd = _v3_refine(cmd, state, MY_PLAYER_ID)
            cmd["playerId"] = MY_PLAYER_ID

            # SAFETY: Zone enforcement for MOVE_TO
            if cmd.get("commandType") == "MOVE_TO":
                params = cmd.get("parameters", {})
                x = params.get("x", HOME_X)
                y = params.get("y", HOME_Y)

                if x < ZONE_X_MIN:
                    params["x"] = ZONE_X_MIN
                elif x > ZONE_X_MAX:
                    params["x"] = ZONE_X_MAX

                if y < ZONE_Y_MIN:
                    params["y"] = ZONE_Y_MIN
                elif y > ZONE_Y_MAX:
                    params["y"] = ZONE_Y_MAX

                cmd["parameters"] = params

            # SAFETY: SHOOT always with max power (V3 keeps this)
            if cmd.get("commandType") == "SHOOT":
                params = cmd.get("parameters", {})
                params["power"] = 1.0
                if "target" not in params and "y" not in params:
                    params["target"] = "CENTER"
                cmd["parameters"] = params

            # SAFETY: Clamp pass power
            if cmd.get("commandType") == "PASS":
                params = cmd.get("parameters", {})
                power = params.get("power", 0.7)
                if power > 0.9:
                    params["power"] = 0.9
                elif power < 0.4:
                    params["power"] = 0.4
                cmd["parameters"] = params

            refined.append(cmd)

        return refined if refined else FALLBACK_CMD
    except json.JSONDecodeError:
        return FALLBACK_CMD

# ==================== ENTRYPOINT ====================
@app.entrypoint
async def invoke(payload, context):
    log.info(f"FWD2 Agent (Player {MY_PLAYER_ID}) invoked")
    try:
        prompt = payload.get("prompt", "") if isinstance(payload, dict) else str(payload)
        state = _v3_extract_state(payload, prompt)

        memory_id = os.getenv("MEMORY_TEAM_MEMORY_ID")
        session_id = getattr(context, "session_id", None) or "match-default"
        agent_kwargs = dict(
            model=load_model(),
            system_prompt=SYSTEM_PROMPT,
            tools=list(_gateway_tools),
        )
        if memory_id:
            try:
                mem_config = AgentCoreMemoryConfig(
                    memory_id=memory_id,
                    session_id=session_id,
                    actor_id=ACTOR_ID,
                )
                agent_kwargs["session_manager"] = AgentCoreMemorySessionManager(
                    agentcore_memory_config=mem_config,
                    region_name="us-east-1",
                )
                log.info(f"Memory ON: session={session_id} actor={ACTOR_ID}")
            except Exception as mem_err:
                log.warning(f"Memory init failed, falling back: {mem_err}")
                agent_kwargs["conversation_manager"] = NullConversationManager()
        else:
            agent_kwargs["conversation_manager"] = NullConversationManager()
        agent = Agent(**agent_kwargs)
        result = agent(prompt)
        text = str(result)
        commands = parse_commands(text, state=state)
        yield json.dumps(commands)
    except Exception as e:
        log.error(f"FWD2 Error: {e}")
        yield json.dumps(FALLBACK_CMD)

if __name__ == "__main__":
    app.run()
