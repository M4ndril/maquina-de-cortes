"""
Chamadas ao Claude Code CLI (claude -p) com a assinatura do usuário, sem chave de API.
A resposta sempre vem num esquema JSON fixo (--json-schema): o Claude não consegue responder fora dele.
"""
import json
import shutil
import subprocess


def perguntar(entrada, esquema, modelo="sonnet", extra=(), cwd=None, timeout=900):
    """entrada vai pelo stdin (a linha de comando do Windows tem limite de ~32 mil caracteres).
    -> dict no formato do esquema. Falha do CLI ou resposta fora do formato -> RuntimeError com a mensagem."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code CLI ('claude') não encontrado no PATH.")
    cmd = [exe, "-p", "--tools", "", "--output-format", "json", "--json-schema", json.dumps(esquema), "--model", modelo, *extra]
    if "--session-id" not in extra and "--resume" not in extra:
        cmd.append("--no-session-persistence")  # pedido avulso: não guarda conversa
    try:
        r = subprocess.run(cmd, input=entrada, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"O Claude não respondeu em {timeout // 60} minutos.")
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError((r.stderr or r.stdout).strip()[:500] or f"Claude CLI saiu com código {r.returncode}")
    if r.returncode != 0 or out.get("is_error"):
        raise RuntimeError(str(out.get("result", ""))[:500])
    if not isinstance(out.get("structured_output"), dict):
        raise RuntimeError(f"Resposta fora do formato: {str(out.get('result', ''))[:300]}")
    return out["structured_output"]
