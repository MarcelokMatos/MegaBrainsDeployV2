#!/usr/bin/env python3
"""
Deploy Gateway completo para MegaBrainsFC.

Script baseado no exemplo do workshop Agentic Football, adaptado para o projeto
MegaBrainsDeployV2 com as 4 ferramentas táticas Lambda.
"""

import os
import sys
import json
import subprocess
import time
import shutil
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).parent
AGENTCORE_DIR = PROJECT_ROOT / "agentcore"
APP_DIR = PROJECT_ROOT / "app"
LAMBDAS_DIR = PROJECT_ROOT / "lambdas"

def check_requirements() -> bool:
    """Verifica se todos os pré-requisitos estão instalados."""
    print("🔍 Verificando pré-requisitos...")
    
    required = [
        ("agentcore", ["agentcore", "--version"]),
        ("aws", ["aws", "--version"]),
        ("node", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("uv", ["uv", "--version"]),
        ("cdk", ["cdk", "--version"]),
        ("python", ["python", "--version"]),
    ]
    
    all_ok = True
    for name, cmd in required:
        try:
            result = subprocess.run(cmd, capture_output=True, shell=True, text=True)
            if result.returncode == 0:
                print(f"  ✅ {name}")
            else:
                print(f"  ❌ {name}: não encontrado")
                all_ok = False
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            all_ok = False
    
    return all_ok

def setup_aws_targets():
    """Configura aws-targets.json com conta e região."""
    target_file = AGENTCORE_DIR / "aws-targets.json"
    
    # Verificar se já existe
    if target_file.exists():
        print(f"✅ aws-targets.json já existe")
        return
    
    # Obter informações da AWS
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--output", "json"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise Exception("Falha ao obter identidade AWS")
        
        identity = json.loads(result.stdout)
        account_id = identity["Account"]
        
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        
        targets = [
            {
                "name": "default",
                "account": account_id,
                "region": region
            }
        ]
        
        with open(target_file, "w") as f:
            json.dump(targets, f, indent=2)
        
        print(f"✅ aws-targets.json criado (account: {account_id}, region: {region})")
        
    except Exception as e:
        print(f"❌ Erro ao criar aws-targets.json: {e}")
        raise

def inject_gateway_agent_base():
    """Injeta gateway_agent_base_cdk.py nos diretórios de agentes."""
    print("🔧 Preparando agentes para gateway...")
    
    # Código base do agente com suporte a gateway
    gateway_agent_base = '''
"""
Agent base with Gateway MCP client support.
Injected by deploy_gateway.py.
"""
import os
from typing import Optional
from fastapi import FastAPI

# MCP Client (if available)
try:
    from mcp_client.client import get_gateway_mcp_client
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

def create_gateway_agent(app: FastAPI, base_agent_func, **kwargs):
    """Create agent with Gateway MCP support."""
    if HAS_MCP and os.getenv("GATEWAY_URL"):
        # Agent will use gateway tools
        print(f"✅ Gateway MCP enabled: {os.getenv('GATEWAY_URL')}")
    else:
        print("ℹ️  Gateway MCP not configured")
    
    # Return the original agent function
    return base_agent_func(app, **kwargs)
'''
    
    # Copiar para cada agente
    agents = ["ai_gk", "ai_def", "ai_mid", "ai_fwd1", "ai_fwd2"]
    for agent in agents:
        agent_dir = APP_DIR / agent
        if not agent_dir.exists():
            print(f"⚠️  Diretório não encontrado: {agent_dir}")
            continue
        
        gateway_file = agent_dir / "gateway_agent_base_cdk.py"
        with open(gateway_file, "w") as f:
            f.write(gateway_agent_base)
        
        print(f"  ✅ {agent}: gateway_agent_base_cdk.py criado")
    
    return True

def validate_lambda_tools():
    """Valida que todas as ferramentas Lambda existem."""
    print("🔍 Validando ferramentas Lambda...")
    
    tools = [
        "calculate_pass_options",
        "evaluate_shot", 
        "find_open_space",
        "get_defensive_assignment"
    ]
    
    all_valid = True
    for tool in tools:
        tool_dir = LAMBDAS_DIR / tool
        index_file = tool_dir / "index.py"
        manifest_file = tool_dir / "manifest.json"
        
        if not tool_dir.exists():
            print(f"  ❌ {tool}: diretório não existe")
            all_valid = False
        elif not index_file.exists():
            print(f"  ❌ {tool}: index.py não encontrado")
            all_valid = False
        elif not manifest_file.exists():
            print(f"  ❌ {tool}: manifest.json não encontrado")
            all_valid = False
        else:
            print(f"  ✅ {tool}: index.py + manifest.json OK")
    
    return all_valid

def deploy_with_agentcore():
    """Executa agentcore deploy para criar gateway e agentes."""
    print("🚀 Iniciando deploy com AgentCore...")
    
    try:
        # Validar configuração primeiro
        print("📋 Validando agentcore.json...")
        validate_result = subprocess.run(
            ["agentcore", "validate"],
            cwd=AGENTCORE_DIR,
            capture_output=True,
            text=True
        )
        
        if "Valid" not in validate_result.stdout:
            print(f"❌ Validação falhou: {validate_result.stdout}")
            return False
        
        print("✅ Configuração válida")
        
        # Executar deploy
        print("🏗️  Executando agentcore deploy...")
        deploy_cmd = ["agentcore", "deploy", "--yes"]
        
        # Executar com timeout e monitoramento
        process = subprocess.Popen(
            deploy_cmd,
            cwd=AGENTCORE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Monitorar saída
        output_lines = []
        for line in process.stdout:
            output_lines.append(line)
            # Mostrar progresso reduzido
            if "Deploy to AWS" in line or "Ready" in line or "StackNameOutput" in line:
                print(f"  {line.strip()}")
        
        process.wait()
        
        if process.returncode == 0:
            print("✅ Deploy concluído com sucesso!")
            print("📊 Outputs da CloudFormation:")
            for line in output_lines:
                if "Outputs:" in line or "arn:aws:" in line:
                    print(f"  {line.strip()}")
            return True
        else:
            print(f"❌ Deploy falhou (exit code: {process.returncode})")
            return False
            
    except Exception as e:
        print(f"❌ Erro durante deploy: {e}")
        return False

def cleanup():
    """Remove arquivos temporários injetados."""
    print("🧹 Limpando arquivos temporários...")
    
    agents = ["ai_gk", "ai_def", "ai_mid", "ai_fwd1", "ai_fwd2"]
    for agent in agents:
        gateway_file = APP_DIR / agent / "gateway_agent_base_cdk.py"
        if gateway_file.exists():
            try:
                gateway_file.unlink()
                print(f"  ✅ {agent}: arquivo temporário removido")
            except Exception:
                pass

def main():
    print("=" * 60)
    print("🚀 MegaBrainsFC Gateway Deployment")
    print("=" * 60)
    
    try:
        # 1. Verificar requisitos
        if not check_requirements():
            print("❌ Pré-requisitos não atendidos. Instale as ferramentas necessárias.")
            sys.exit(1)
        
        # 2. Configurar AWS targets
        setup_aws_targets()
        
        # 3. Validar ferramentas Lambda
        if not validate_lambda_tools():
            print("❌ Ferramentas Lambda inválidas. Verifique o diretório lambdas/")
            sys.exit(1)
        
        # 4. Injetar código de gateway nos agentes
        inject_gateway_agent_base()
        
        # 5. Executar deploy
        deploy_success = deploy_with_agentcore()
        
        if not deploy_success:
            print("❌ Deploy falhou. Verifique logs acima.")
            cleanup()
            sys.exit(1)
        
        # 6. Obter e mostrar informações do gateway
        print("\n" + "=" * 60)
        print("🎉 Gateway implantado com sucesso!")
        print("=" * 60)
        
        print("\n📋 Próximos passos:")
        print("1. Acesse o console AWS Bedrock → AgentCore → Gateways")
        print("2. Encontre 'tactical-tools' gateway")
        print("3. Copie o URL do endpoint (termina em /mcp)")
        print("4. Acesse AgentCore → Runtime para ver os 5 agentes")
        print("5. Use agentcore status para verificar status")
        
        print("\n🔧 Dica: Para obter o GATEWAY_URL:")
        print("  agentcore status | grep 'gateway'")
        
    except KeyboardInterrupt:
        print("\n⚠️  Deploy interrompido pelo usuário")
        cleanup()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Erro fatal: {e}")
        cleanup()
        sys.exit(1)
    finally:
        cleanup()
    
    print("\n✅ Processo concluído!")

if __name__ == "__main__":
    main()
