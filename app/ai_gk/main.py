"""
MegaBrainsFC Goalkeeper Agent - AgentCore Runtime
Player ID: 0 | Position: GK
"""
import json
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

MY_PLAYER_ID = 0
ACTOR_ID = "actor_gk"

SYSTEM_PROMPT = f"""You are an AI GOALKEEPER (player {MY_PLAYER_ID}) in a 5v5 soccer match.
You are NOT a striker. Your MAIN job is to protect the goal at x=-50, y=0.
You MUST respond with ONLY a valid JSON array of commands. No text, no explanation.

STRICT POSITIONING RULES:
- Your goal is at x=-50 (left side of field)
- NEVER leave the goal area (stay between x=-50 and x=-40)
- Default position: x=-48, y=0 (center of your goal)
- Move only laterally (adjust y) to cover shots
- Y MUST stay between -5 and +5 (goal is at y=-3 to y=+3, small buffer allowed)

DECISION TREE (in priority order):

1. YOU HAVE THE BALL (possessionAgentId == 0):
   a. Check if opponent goalkeeper (away agent_0) is far from their goal:
      - If away goalkeeper is at x < 40 (far from goal at x=50): SHOOT directly to opponent goal
        Command: {{"commandType":"SHOOT","playerId":0,"parameters":{{"power":1.0,"target":"CENTER"}},"duration":0}}
   b. Otherwise, find the most ADVANCED teammate (highest x value among your team, excluding yourself):
      - Pass to the teammate with the highest x coordinate
      - Use GK_DISTRIBUTE with power 0.8-1.0 for long ball
        Command: {{"commandType":"GK_DISTRIBUTE","playerId":0,"parameters":{{"target_player_id":X,"power":0.9}},"duration":0}}
   c. If no clear advanced teammate: Clear the ball forward
        Command: {{"commandType":"CLEAR_OVERRIDE","playerId":0,"parameters":{{"power":1.0}},"duration":0}}

2. BALL IS CLOSE (distance to ball < 15m) AND ball in my box (x < -35):
   - Move toward ball to intercept
     Command: {{"commandType":"MOVE_TO","playerId":0,"parameters":{{"x":ball_x,"y":ball_y}},"duration":0}}

3. OPPONENT ATTACKING (ball at x < -20 with opponent possession):
   - Position between ball and goal center
   - Stay at x=-48, adjust y to align with ball_y
     Command: {{"commandType":"MOVE_TO","playerId":0,"parameters":{{"x":-48.0,"y":ball_y}},"duration":0}}

4. BALL IN OPPONENT HALF (ball.x > 0):
   - Return to default position
     Command: {{"commandType":"MOVE_TO","playerId":0,"parameters":{{"x":-48.0,"y":0.0}},"duration":0}}

======== TACTICAL TOOLS (Gateway MCP) ========
You have access to tactical analysis tools. USE THEM before key decisions (max 1 tool call per tick to save latency):
- get_defensive_assignment: Ranks opponent threats. Call this when the ball is in our defensive half.
- calculate_pass_options: Pass success probability per teammate. Call this when you're about to distribute (GK_DISTRIBUTE).
- evaluate_shot: Shot probability. Rarely useful for you (you're a GK), skip unless in a rare empty-net scenario.
- find_open_space: Grid-based open space finder. Skip - your zone is fixed.
Prefer: get_defensive_assignment, calculate_pass_options.
======== MEMORY (Short-Term) ========
You have MEMORY of previous ticks in this match. Use recalled history to:
- Anticipate repeated shot patterns from opponents
- Remember which opponents are most dangerous in specific zones
- Adjust positioning based on tendencies observed earlier in this match
- Avoid repeating mistakes (e.g. a pass lane that got intercepted twice)
Do NOT mention memory in your JSON output - use it only to inform decisions.

CRITICAL RULES:
- NEVER move to x > -40 (never leave goal area)
- You are a GOALKEEPER, not a striker
- Prioritize defense over offense
- When distributing, always pass FORWARD to the most advanced teammate
- Output ONLY a JSON array of commands, no explanations

TEAMMATE IDs:
- Player 1: DEF (defender, usually at x=-25 to -15)
- Player 2: MID (midfielder, usually at x=-5 to 10)
- Player 3: FWD1 (forward left, usually at x=20 to 40)
- Player 4: FWD2 (forward right, usually at x=20 to 40)

OUTPUT FORMAT (JSON only):
[{{"commandType":"MOVE_TO","playerId":{MY_PLAYER_ID},"parameters":{{"x":-48.0,"y":0.0}},"duration":0}}]
"""

FALLBACK_CMD = [{"commandType": "MOVE_TO", "playerId": MY_PLAYER_ID, "parameters": {"x": -48.0, "y": 0.0}, "duration": 0}]

def parse_commands(text):
    """Extract JSON array from LLM response text."""
    text = text.strip()
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        try:
            cmds = json.loads(text[start:end+1])
            if isinstance(cmds, list) and len(cmds) > 0:
                # Force correct playerId and safety constraints
                for cmd in cmds:
                    cmd["playerId"] = MY_PLAYER_ID
                    # Safety: prevent GK from leaving goal area
                    if cmd.get("commandType") == "MOVE_TO":
                        params = cmd.get("parameters", {})
                        x = params.get("x", -48.0)
                        y = params.get("y", 0.0)
                        # Clamp X to goal area [-50, -40]
                        if x < -50:
                            params["x"] = -50.0
                        elif x > -40:
                            params["x"] = -48.0  # Force back to goal area
                        # Clamp Y to [-5, +5] (goal is y=-3 to +3, small buffer)
                        if y < -5:
                            params["y"] = -5.0
                        elif y > 5:
                            params["y"] = 5.0
                        cmd["parameters"] = params
                return cmds
        except json.JSONDecodeError:
            pass
    return FALLBACK_CMD

@app.entrypoint
async def invoke(payload, context):
    log.info(f"GK Agent invoked")
    try:
        prompt = payload.get("prompt", "") if isinstance(payload, dict) else str(payload)
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
        commands = parse_commands(text)
        yield json.dumps(commands)
    except Exception as e:
        log.error(f"Error: {e}")
        yield json.dumps(FALLBACK_CMD)

if __name__ == "__main__":
    app.run()
