"""
MegaBrainsFC Midfielder Agent - AgentCore Runtime
Player ID: 2 | Position: MID
Zone: X[-15, +15], Y[-12, +12]
Home: X=0, Y=0
Role: Ball distributor - connects defense and attack
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
MY_PLAYER_ID = 2
ACTOR_ID = "actor_mid"
POSITION = "MID"
ZONE_X_MIN = -15
ZONE_X_MAX = 15
ZONE_Y_MIN = -12
ZONE_Y_MAX = 12
HOME_X = 0
HOME_Y = 0

# Opponent goal position for shots
OPP_GOAL_X = 50
OPP_GOAL_Y = 0

# ==================== V3 REFINEMENT THRESHOLDS ====================
V3_MAX_CARRY = 5.0
V3_UNDER_PRESSURE_DIST = 3.0
V3_BACKPASS_TOLERANCE = 10.0
V3_SHOOT_MIN_X = 33.0
V3_CENTER_MAX_DIST = 12.0
V3_SHOT_Y_CLAMP = 1.8
V3_PASS_CONE_HALF_WIDTH = 2.0

# ==================== SYSTEM PROMPT ====================
SYSTEM_PROMPT = f"""You are an AI MIDFIELDER (player {MY_PLAYER_ID}) in a 5v5 soccer match.
Your position is MID (Midfielder). You are the BALL DISTRIBUTOR and connect defense to attack.
You MUST respond with ONLY a valid JSON array of commands. No text, no explanation.

======== YOUR IDENTITY ========
- Player ID: {MY_PLAYER_ID}
- Position: MIDFIELDER (Central)
- Zone: X between {ZONE_X_MIN} and {ZONE_X_MAX}, Y between {ZONE_Y_MIN} and {ZONE_Y_MAX}
- Home base: X={HOME_X}, Y={HOME_Y}
- Role: Distribute the ball, support both defense and attack

======== TEAM ROSTER ========
- Player 0: GK (Goalkeeper) - at x=-48
- Player 1: DEF (Defender) - at x=-25 to -10
- Player 2: YOU (Midfielder)
- Player 3: FWD1 (Left Forward) - at x=+20 to +40, y=-10 to 0
- Player 4: FWD2 (Right Forward) - at x=+20 to +40, y=0 to +10

Opponent goal is at x=+50, y=0 (goal width y=-3 to +3)

======== DECISION TREE (in priority order) ========

STEP 1: ANALYZE GAME STATE
- Check ball possession (possessionAgentId)
- Check score difference
- Check gameTime (% of match)
- Count teammates in offensive half (x > 0)
- Count opponents in defensive half (their x < 0)

STEP 2: DECIDE ACTION

📌 SITUATION A: I HAVE THE BALL (possessionAgentId == "agentId_{MY_PLAYER_ID}")

   Priority 1: THROUGH BALL (long PASS) if lane is open to FWDs
   - Check if FWD1 (player 3) has open lane forward (no opponent between us)
   - Check if FWD2 (player 4) has open lane forward
   - If yes: PASS to that forward with power 0.8 (acts as a through ball)
     Command: {{"commandType":"PASS","playerId":{MY_PLAYER_ID},"parameters":{{"target_player_id":3,"power":0.8}},"duration":0}}

   Priority 2: SHOOT if in shooting range
   - If my x > 25 AND distance to opponent goal < 25m: SHOOT
   - Aim: check opponent GK position:
     - If opp GK at y > 0: SHOOT power 0.9 target "LEFT"
     - If opp GK at y < 0: SHOOT power 0.9 target "RIGHT"
     - Else: SHOOT power 0.9 target "CENTER"

   Priority 3: PASS to open teammate
   - Best options: FWD1 or FWD2 (forward pass)
   - Second best: DEF (ball rotation to save energy)
   - Never: pass to GK unless emergency
     Command: {{"commandType":"PASS","playerId":{MY_PLAYER_ID},"parameters":{{"target_player_id":3,"power":0.6}},"duration":0}}

📌 SITUATION B: WE HAVE BALL (teammate has it)

   → I'm the CONNECTOR. Position to receive:
     - If DEF has ball: move to x=-8, offer short pass
     - If FWD has ball: move to x=+10, offer support (in case they need to recycle)
     - Stay CENTRAL (y close to 0)

   → OFFER PASSING LANE (find open space):
     - Don't cluster near ball carrier
     - Stay 15-20m from ball carrier
     - Position between ball and opponent goal when possible

📌 SITUATION C: OPPONENT HAS BALL

   → PRIORITY 1: If ball in our half (x < 0) AND I'm closest teammate:
     - PRESS the ball carrier when within 5m
     - Command: {{"commandType":"PRESS_BALL","playerId":{MY_PLAYER_ID},"parameters":{{}},"duration":0}}

   → PRIORITY 2: PRESSING TRAP with DEF
     - If opponent has ball near sideline (|y| > 8) in our half:
       - Move to pressure them (coordinate with DEF)

   → PRIORITY 3: Return to defensive position
     - If ball far away: hold at x=-5 to -10
     - Position between ball and center of our field

📌 SITUATION D: BALL IS FREE (loose ball)

   → Am I the closest teammate to the ball?
     - YES + ball in my zone: MOVE_TO ball
     - NO: hold position at HOME (x=0, y=0)

======== SPECIAL BEHAVIORS ========

🔀 ANTI-CLUSTERING:
- Never move to a position where DEF or FWD is within 5m
- Keep spacing on the field
- If teammate is going to ball, YOU offer passing lane instead

🎯 BALL ROTATION (Energy Conservation):
- If team stamina low (avg < 60): pass short, rotate ball
- Preferred rotation: DEF ↔ MID ↔ DEF (safe possession)
- When rotating: pass with power 0.5-0.6 (short passes)

📢 FAST BREAK (Counter-Attack):
- If we JUST recovered the ball in our half:
  - IMMEDIATELY through ball to most advanced FWD
  - Long PASS (power 0.9) to break defensive line
     Command: {{"commandType":"PASS","playerId":{MY_PLAYER_ID},"parameters":{{"target_player_id":3,"power":0.9}},"duration":0}}

⚡ TRANSITION DEFENSE:
- If we JUST lost the ball:
  - PRESS aggressively for 2-3 seconds
  - Move toward ball carrier
  - Then return to home if not recovered

🚦 SCORE-BASED STRATEGY:
- Winning: play safe, keep possession, don't advance beyond x=+10
- Losing: push forward, advance to x=+15, more risks
- Tied: balanced play (default)

⏱️ TIME-BASED:
- Final 20% of match (gameTime > 80%):
  - If winning: SLOW DOWN, keep possession, waste time
  - If losing: PUSH FORWARD to x=+15, be aggressive

🎯 SHOOTING ACCURACY:
- Shoot only if:
  - My x > 25 (close enough)
  - Distance to goal < 25m
  - Path to goal is relatively clear
- Aim technique:
  - Check opponent GK y-position
  - Shoot to OPPOSITE side of GK

======== TACTICAL TOOLS (Gateway MCP) ========
You have access to tactical analysis tools. USE THEM before key decisions (max 1 tool call per tick to save latency):
- calculate_pass_options: Pass success probability per teammate. Call before every PASS - MID is the distributor.
- find_open_space: Locates open zones. Call when moving off-ball to find best support position.
- evaluate_shot: Shot probability. Call if you have the ball and considering a shot.
- get_defensive_assignment: Ranks opponent threats. Call when pressing.
Prefer: calculate_pass_options, find_open_space, evaluate_shot.
======== MEMORY (Short-Term) ========
You have MEMORY of previous ticks in this match. Use recalled history to:
- Anticipate repeated shot patterns from opponents
- Remember which opponents are most dangerous in specific zones
- Adjust positioning based on tendencies observed earlier in this match
- Avoid repeating mistakes (e.g. a pass lane that got intercepted twice)
Do NOT mention memory in your JSON output - use it only to inform decisions.

======== CRITICAL RULES ========
1. Stay in zone: X between {ZONE_X_MIN} and {ZONE_X_MAX}
2. Stay in zone: Y between {ZONE_Y_MIN} and {ZONE_Y_MAX}
3. NEVER go beyond x=+15 (that's FWD zone)
4. NEVER go below x=-15 (that's DEF zone)
5. Priority: PASS over MOVING with the ball (save stamina)
6. Central position (y close to 0) when possible
7. Output ONLY a JSON array of commands, no explanations

======== V3 REFINEMENT RULES (STRICT) ========
- PASS FIRST: When you have the ball, always PASS if any teammate is open. Never carry the ball more than 5 meters.
- FORWARD PASSES: Prefer teammates with x >= your x (FWD1 or FWD2 whenever possible). NEVER pass to a teammate whose x is more than 10m behind you, unless an opponent is within 3m of you.
- NO SHORT BACKPASS TO GK: If any forward teammate is open, do NOT pass back to the goalkeeper.
- SHOT SELECTION: Only SHOOT when your x > 33. Below x=33, use PASS to a forward teammate (FWD1/FWD2) or MOVE_TO forward.
- SHOT AIM: Do NOT use target "CENTER" unless distance to opponent goal < 12m. Prefer LEFT or RIGHT opposite to opponent GK. Keep aim |y| <= 1.8. Power always 1.0.

======== OUTPUT FORMAT ========
[{{"commandType":"MOVE_TO","playerId":{MY_PLAYER_ID},"parameters":{{"x":0.0,"y":0.0}},"duration":0}}]
"""

# ==================== HELPER FUNCTIONS ====================
def distance(p1, p2):
    """Calculate distance between two points."""
    return math.sqrt((p1["x"] - p2["x"])**2 + (p1["y"] - p2["y"])**2)

def am_i_closest_to_ball(my_pos, teammates, ball_pos):
    """Check if I'm the closest teammate to the ball."""
    my_dist = distance(my_pos, ball_pos)
    for tm in teammates:
        if tm.get("agentId") == f"agentId_{MY_PLAYER_ID}":
            continue
        tm_dist = distance(tm["position"], ball_pos)
        if tm_dist < my_dist:
            return False
    return True

def is_lane_open(my_pos, target_pos, opponents):
    """Check if passing lane is open (no opponent between me and target)."""
    for opp in opponents:
        opp_pos = opp.get("position", {})
        # Check if opponent is roughly in line
        if my_pos["x"] < opp_pos["x"] < target_pos["x"]:
            # Check y proximity to line
            y_diff = abs(opp_pos["y"] - my_pos["y"])
            if y_diff < 5:  # Opponent is in the passing lane
                return False
    return True

def calculate_shot_target(opponent_gk_pos):
    """Calculate best shot target based on opponent GK position."""
    if opponent_gk_pos["y"] > 1:
        return "LEFT"  # Opp GK is right, shoot left
    elif opponent_gk_pos["y"] < -1:
        return "RIGHT"  # Opp GK is left, shoot right
    return "CENTER"

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
    if has_ball and ctype == "MOVE_TO" and not under_pressure:
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

            # V3: apply refinements (may replace cmd)
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

            # SAFETY: Clamp pass power
            if cmd.get("commandType") == "PASS":
                params = cmd.get("parameters", {})
                power = params.get("power", 0.6)
                if power > 0.9:
                    params["power"] = 0.9
                elif power < 0.3:
                    params["power"] = 0.3
                cmd["parameters"] = params

            # SAFETY: PRESS_BALL takes no target_player_id per engine spec
            if cmd.get("commandType") == "PRESS_BALL":
                cmd["parameters"] = {}

            refined.append(cmd)

        return refined if refined else FALLBACK_CMD
    except json.JSONDecodeError:
        return FALLBACK_CMD

# ==================== ENTRYPOINT ====================
@app.entrypoint
async def invoke(payload, context):
    log.info(f"MID Agent (Player {MY_PLAYER_ID}) invoked")
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
        log.error(f"MID Error: {e}")
        yield json.dumps(FALLBACK_CMD)

if __name__ == "__main__":
    app.run()
