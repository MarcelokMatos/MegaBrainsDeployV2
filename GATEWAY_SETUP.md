# MegaBrainsFC Gateway Setup

Instructions to deploy the AgentCore Gateway with the 4 tactical analysis tools,
and wire our team's agents to consume them.

## What's included in this repo

- `lambdas/` — Python source for the 4 tactical Lambda functions:
  - `calculate_pass_options/` — Pass success probability calculator
  - `evaluate_shot/` — Shot success probability + best aim
  - `find_open_space/` — Grid-based open-space finder per zone
  - `get_defensive_assignment/` — Opponent threat ranker
- `setup_gateway_infra.py` — One-shot infra deployment script
- `app/*/mcp_client/client.py` — Already updated to read `GATEWAY_URL` env var
- `app/*/main.py` — Already wire MCP tools into each agent

## Step 1: Deploy the Gateway infrastructure

You need to run this ONCE. It creates:

- IAM role for Lambda execution (basic Lambda + CloudWatch logs)
- 4 Lambda functions (Python 3.11, 256MB, 10s timeout)
- Gateway execution IAM role (with permission to invoke the Lambdas)
- Bedrock AgentCore Gateway (MCP protocol)
- 4 Gateway targets (one per Lambda)

Prerequisites:
- AWS credentials configured (SSO login, env vars, or profile)
- Python 3.10+ with `boto3` and `bedrock-agentcore` installed

Install SDK if needed:
```bash
pip install boto3 bedrock-agentcore
```

Run the setup:
```powershell
cd C:\Users\marcelo.matos\Documents\Projetos\MegaBrainsDeployV2
python setup_gateway_infra.py --region us-east-1 --project-prefix megabrainsfc
```

The script prints the `GATEWAY_URL` at the end (URL ending in `/mcp`).
Copy this URL — you need it in Step 2.

## Step 2: Inject GATEWAY_URL into the runtimes

Our agents read `GATEWAY_URL` from the environment. Set it in the AgentCore
runtime config in one of two ways:

### Option A - Add to agentcore.json (recommended)

Edit `agentcore/agentcore.json` — for each entry in `runtimes`, add
an `environmentVariables` block:

```json
{
  "name": "ai_gk",
  "build": "CodeZip",
  "entrypoint": "main.py",
  "codeLocation": "app/ai_gk/",
  "runtimeVersion": "PYTHON_3_14",
  "networkMode": "PUBLIC",
  "protocol": "HTTP",
  "environmentVariables": {
    "GATEWAY_URL": "https://xxxxxxxx.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
  }
}
```

Repeat for `ai_def`, `ai_mid`, `ai_fwd1`, `ai_fwd2` (same URL for all).

### Option B - Hardcode in mcp_client/client.py

If `agentcore.json` does not accept `environmentVariables`, edit
`app/*/mcp_client/client.py`:

```python
def get_gateway_mcp_client(url: Optional[str] = None) -> Optional[MCPClient]:
    endpoint = url or os.getenv("GATEWAY_URL") or "https://YOUR_URL_HERE/mcp"
    ...
```

## Step 3: Grant IAM permission for runtimes to call the Gateway

Each of your 5 agent runtimes has an execution IAM role. Attach the
AgentCore Gateway access policy to each:

```powershell
$roles = @(
  "AgentCore-MegaBrainsDeplo-ApplicationAgentAiGkRunti-98HkN3udYuZ8",
  "AgentCore-MegaBrainsDeplo-ApplicationAgentAiDefRunt-7sAbhNZ54TDk",
  "AgentCore-MegaBrainsDeplo-ApplicationAgentAiMidRunt-ZIbEZ85EZjDo",
  "AgentCore-MegaBrainsDeplo-ApplicationAgentAiFwd1Run-pzgpS6UURSpf",
  "AgentCore-MegaBrainsDeplo-ApplicationAgentAiFwd2Run-yBGVta2LkJK5"
)
foreach ($r in $roles) {
  aws iam attach-role-policy --role-name $r --policy-arn arn:aws:iam::aws:policy/AgentCoreGatewayAccess
}
```

If the managed policy `AgentCoreGatewayAccess` does not exist yet in your
account, create an inline policy on each role that grants
`bedrock-agentcore:InvokeGateway` on your Gateway ARN.

## Step 4: Redeploy the agents

```powershell
cd C:\Users\marcelo.matos\Documents\Projetos\MegaBrainsDeployV2
agentcore deploy
```

## Step 5: Verify tool usage

After playing a match, check CloudWatch Logs for each agent runtime. Look for:

```
Gateway MCP: 4 tools loaded
Tool #N: calculate-pass-options
Tool #N: evaluate-shot
Tool #N: find-open-space
Tool #N: get-defensive-assignment
```

If you see `Gateway MCP init failed`, the runtime cannot reach the Gateway.
Common causes:
- `GATEWAY_URL` not set on the runtime
- Runtime IAM role missing Gateway invoke permission
- Gateway status is not `READY`

## Troubleshooting

**Latency spike after Gateway added:**
Each tool call adds ~150-300ms round-trip. Our agent prompts already
instruct "max 1 tool call per tick". If latency exceeds 400ms average,
consider skipping tool calls for GK / DEF (they need tools less).

**Gateway auth failing (401 Unauthorized):**
Check whether the Gateway requires bearer token authentication. If so,
extend `app/*/mcp_client/client.py` to include an Authorization header
with the token fetched from AWS Cognito or Secrets Manager.

**Lambda cold-start:**
First tool call after idle can be slow (~1s). Subsequent calls warm up.