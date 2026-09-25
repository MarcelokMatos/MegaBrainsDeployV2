"""Deploy the Gateway infrastructure for MegaBrainsFC tactical tools.

This script:
1. Creates (or reuses) an IAM role for Lambda execution
2. Packages and deploys the 4 tactical Lambda functions
3. Creates a Bedrock AgentCore Gateway
4. Registers each Lambda as a Gateway target
5. Prints the GATEWAY_URL

Prerequisites:
- AWS credentials configured (aws login / SSO / env vars)
- boto3 installed
- bedrock-agentcore Python SDK installed (`pip install bedrock-agentcore`)
- The Lambda source code exists under `./lambdas/<tool_name>/index.py`

Usage:
    python setup_gateway_infra.py
    python setup_gateway_infra.py --region us-east-1 --project-prefix megabrainsfc
"""
import argparse
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Dict, Any

import boto3
from botocore.exceptions import ClientError


TOOLS = [
    "calculate_pass_options",
    "evaluate_shot",
    "find_open_space",
    "get_defensive_assignment",
]

LAMBDA_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

GATEWAY_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


def get_or_create_role(iam, role_name: str, trust: dict, description: str, extra_policies: list[str]) -> str:
    """Create (or fetch) an IAM role. Returns the role ARN."""
    try:
        resp = iam.get_role(RoleName=role_name)
        print(f"[iam] role {role_name} already exists")
        return resp["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise

    print(f"[iam] creating role {role_name}")
    resp = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description=description,
    )
    arn = resp["Role"]["Arn"]

    for policy_arn in extra_policies:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        print(f"[iam]   attached {policy_arn}")

    # Wait for IAM propagation
    time.sleep(10)
    return arn


def zip_lambda_source(source_dir: Path) -> bytes:
    """Zip the contents of a Lambda source directory into a bytes buffer."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in source_dir.iterdir():
            if f.is_file() and f.suffix == ".py":
                zf.write(f, arcname=f.name)
    buf.seek(0)
    return buf.read()


def deploy_lambda(lam, function_name: str, role_arn: str, code_zip: bytes) -> str:
    """Create or update a Lambda function. Returns the function ARN."""
    try:
        resp = lam.get_function(FunctionName=function_name)
        print(f"[lambda] {function_name} already exists, updating code")
        lam.update_function_code(FunctionName=function_name, ZipFile=code_zip)
        # Wait for update
        waiter = lam.get_waiter("function_updated")
        waiter.wait(FunctionName=function_name)
        return resp["Configuration"]["FunctionArn"]
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("ResourceNotFoundException", "NoSuchEntity"):
            raise

    print(f"[lambda] creating {function_name}")
    resp = lam.create_function(
        FunctionName=function_name,
        Runtime="python3.11",
        Role=role_arn,
        Handler="index.lambda_handler",
        Code={"ZipFile": code_zip},
        Timeout=10,
        MemorySize=256,
        Description=f"MegaBrainsFC tactical tool: {function_name.rsplit('-', 1)[-1]}",
    )
    waiter = lam.get_waiter("function_active")
    waiter.wait(FunctionName=function_name)
    return resp["FunctionArn"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--project-prefix", default="megabrainsfc")
    parser.add_argument("--lambda-role-arn", default=None,
                        help="Pre-existing Lambda execution role ARN. If set, skip role creation.")
    parser.add_argument("--gateway-role-arn", default=None,
                        help="Pre-existing Gateway execution role ARN. If set, skip role creation and skip putting invoke policy.")
    parser.add_argument("--gateway-name", default="megabrainsfc-tactical-tools")
    parser.add_argument("--lambdas-dir", default=None,
                        help="Path to the lambdas/ folder. Defaults to <script_dir>/lambdas")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    lambdas_root = Path(args.lambdas_dir) if args.lambdas_dir else script_dir / "lambdas"

    if not lambdas_root.exists():
        print(f"ERROR: lambdas folder not found at {lambdas_root}", file=sys.stderr)
        sys.exit(1)

    session = boto3.Session(region_name=args.region)
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    account = identity["Account"]
    print(f"AWS account: {account}, region: {args.region}")

    iam = session.client("iam")
    lam = session.client("lambda")

    # 1) Lambda execution role - prefer pre-existing (workshop provides afwc-gateway-tool-lambda-role)
    if args.lambda_role_arn:
        lambda_role_arn = args.lambda_role_arn
        print(f"[iam] using existing Lambda role: {lambda_role_arn}")
    else:
        lambda_role_arn = get_or_create_role(
            iam,
            role_name=f"{args.project_prefix}-lambda-role",
            trust=LAMBDA_TRUST_POLICY,
            description="MegaBrainsFC Lambda execution role",
            extra_policies=[
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            ],
        )

    # 2) Deploy Lambdas
    tool_arns: Dict[str, str] = {}
    for tool in TOOLS:
        src = lambdas_root / tool
        if not (src / "index.py").exists():
            print(f"[skip] {tool} has no index.py at {src}", file=sys.stderr)
            continue
        code = zip_lambda_source(src)
        fn_name = f"{args.project_prefix}-{tool.replace('_', '-')}"
        arn = deploy_lambda(lam, fn_name, lambda_role_arn, code)
        tool_arns[tool] = arn
        print(f"[lambda] {tool} ARN: {arn}")

    # 3) Gateway execution role - prefer pre-existing (workshop provides AfwcGatewayExecutionRole)
    if args.gateway_role_arn:
        gateway_role_arn = args.gateway_role_arn
        print(f"[iam] using existing Gateway role: {gateway_role_arn}")
        print("[iam] SKIPPING put_role_policy - assuming pre-existing role can invoke Lambdas")
    else:
        gateway_role_arn = get_or_create_role(
            iam,
            role_name=f"{args.project_prefix}-gateway-role",
            trust=GATEWAY_TRUST_POLICY,
            description="MegaBrainsFC Gateway execution role",
            extra_policies=[],
        )
        # Grant gateway role permission to invoke our Lambdas (only when we created it)
        inline_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": list(tool_arns.values()),
                }
            ],
        }
        iam.put_role_policy(
            RoleName=f"{args.project_prefix}-gateway-role",
            PolicyName="invoke-tactical-lambdas",
            PolicyDocument=json.dumps(inline_policy),
        )
        print("[iam] gateway role has permission to invoke tactical Lambdas")
        time.sleep(5)

    # 4) Create Gateway via boto3 bedrock-agentcore-control client (no external SDK required)
    try:
        gw_client = session.client("bedrock-agentcore-control")
    except Exception as e:
        print(f"ERROR: cannot create bedrock-agentcore-control client. Update boto3? Details: {e}", file=sys.stderr)
        sys.exit(1)

    # Check if gateway already exists
    existing_gws = []
    try:
        paginator = gw_client.get_paginator("list_gateways")
        for page in paginator.paginate():
            existing_gws.extend(page.get("items", []))
    except Exception:
        # Fallback: single-page list
        resp = gw_client.list_gateways()
        existing_gws = resp.get("items", [])

    matching = [g for g in existing_gws if g.get("name") == args.gateway_name]
    if matching:
        gw_id = matching[0].get("gatewayId") or matching[0].get("gatewayIdentifier")
        print(f"[gateway] {args.gateway_name} already exists: {gw_id}")
    else:
        print(f"[gateway] creating {args.gateway_name}")
        resp = gw_client.create_gateway(
            name=args.gateway_name,
            roleArn=gateway_role_arn,
            protocolType="MCP",
            description="MegaBrainsFC tactical analysis Gateway",
            authorizerType="AWS_IAM",
        )
        gw_id = resp.get("gatewayId") or resp.get("gatewayIdentifier")
        print(f"[gateway] created: {gw_id}")

    # 5) Register each Lambda as a Gateway target
    for tool, lambda_arn in tool_arns.items():
        manifest_path = lambdas_root / tool / "manifest.json"
        if not manifest_path.exists():
            print(f"[skip] {tool}: no manifest.json")
            continue
        with open(manifest_path) as f:
            manifest = json.load(f)

        target_name = tool.replace("_", "-")
        target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {
                        "inlinePayload": [
                            {
                                "name": manifest["name"],
                                "description": manifest["description"],
                                "inputSchema": manifest["inputSchema"],
                            }
                        ]
                    },
                }
            }
        }
        try:
            gw_client.create_gateway_target(
                gatewayIdentifier=gw_id,
                name=target_name,
                targetConfiguration=target_config,
                credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            )
            print(f"[target] {target_name} registered")
        except ClientError as e:
            code = e.response["Error"].get("Code", "")
            msg = str(e).lower()
            if "already exists" in msg or code in ("ConflictException", "ResourceInUseException"):
                print(f"[target] {target_name} already exists, skipping")
            else:
                raise

    # 6) Print GATEWAY_URL
    gw = gw_client.get_gateway(gatewayIdentifier=gw_id)
    endpoint = (
        gw.get("gatewayUrl")
        or gw.get("endpoint")
        or gw.get("mcpUrl")
        or gw.get("gatewayEndpoint")
    )
    print("")
    print("=" * 60)
    print("Gateway setup complete!")
    print("=" * 60)
    print(f"Gateway ID:  {gw_id}")
    print(f"GATEWAY_URL: {endpoint}")
    print("")
    print("Next steps:")
    print("1. Add this to your agent env vars (via agentcore.json or CDK stack):")
    print(f"   GATEWAY_URL={endpoint}")
    print("2. Run: agentcore deploy")


if __name__ == "__main__":
    main()