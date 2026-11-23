#!/usr/bin/env python3
"""Servidor MCP stdio da Conversa (ADR-023): é o `mcp__ponte__permissao` do `claude -p --permission-prompt-tool`.

O `claude` arranca-o a partir do `--mcp-config` que a ponte escreve por turno (CONVERSA_PONTE_SOCK e
CONVERSA_PONTE_TURNO no `env` desse ficheiro, nunca no ambiente do motor). JSON-RPC 2.0, uma mensagem por linha:
`initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`; o resto com id → -32601. Cada
`tools/call` abre uma ligação ao socket Unix da ponte, manda `{"v":1,"turno","tool_name","input","tool_use_id"}` numa
linha e bloqueia até à resposta (clique, regra, expiração ou fim do turno). Socket inacessível, resposta mal formada
ou ligação cortada → `deny` («a ponte não respondeu»); nunca `allow` por omissão. Só stdlib.
"""
from __future__ import annotations

import json
import os
import socket
import sys

NOME = "permissao"
VERSAO_MCP = "2025-06-18"
PEDIDO_MAX = 1024 * 1024
RESPOSTA_MAX = 1024 * 1024
ESPERA_S = 700          # a ponte responde sempre em ≤ 10 min (expiração); isto só apanha uma ponte pendurada
SEM_PONTE = "a ponte não respondeu"
FERRAMENTA = {
    "name": NOME,
    "description": "Pede ao dono, na Conversa do Office, permissão para usar uma ferramenta.",
    "inputSchema": {"type": "object",
                    "properties": {"tool_name": {"type": "string"}, "input": {"type": "object"},
                                   "tool_use_id": {"type": "string"}},
                    "required": ["tool_name", "input"]},
}


def _negar(msg: str = SEM_PONTE) -> dict:
    return {"behavior": "deny", "message": msg}


def _valida(r) -> bool:
    # só as duas formas do contrato (contrato da Conversa); outra coisa vinda do socket é tratada como ponte avariada
    if not isinstance(r, dict):
        return False
    if r.get("behavior") == "allow":
        return set(r) == {"behavior", "updatedInput"} and isinstance(r["updatedInput"], dict)
    if r.get("behavior") == "deny":
        return set(r) == {"behavior", "message"} and isinstance(r["message"], str)
    return False


def perguntar(sock_path: str | None, turno: str | None, tool_name, entrada, tool_use_id,
              espera: float = ESPERA_S) -> dict:
    """Uma ligação ao socket da ponte, um pedido, uma resposta; qualquer falha → deny."""
    if not sock_path or not turno:
        return _negar()
    linha = json.dumps({"v": 1, "turno": turno, "tool_name": tool_name, "input": entrada,
                        "tool_use_id": tool_use_id if isinstance(tool_use_id, str) else None},
                       ensure_ascii=False).encode() + b"\n"
    if len(linha) > PEDIDO_MAX:
        return _negar("pedido grande demais")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(espera)
            s.connect(sock_path)
            s.sendall(linha)
            buf = b""
            while b"\n" not in buf:
                bloco = s.recv(65536)
                if not bloco:
                    return _negar()
                buf += bloco
                if len(buf) > RESPOSTA_MAX:
                    return _negar()
        r = json.loads(buf.split(b"\n", 1)[0])
    except (OSError, ValueError):
        return _negar()
    return r if _valida(r) else _negar()


def tratar(msg, env=os.environ) -> dict | None:
    """Uma mensagem JSON-RPC → resposta (None para notificações)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "pedido inválido"}}
    metodo, mid = msg["method"], msg.get("id")
    if "id" not in msg:   # notificação (initialized, cancelled, …): nunca se responde
        return None
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

    def ok(res):
        return {"jsonrpc": "2.0", "id": mid, "result": res}

    def erro(code, texto):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": texto}}

    if metodo == "initialize":
        pv = params.get("protocolVersion")
        return ok({"protocolVersion": pv if isinstance(pv, str) else VERSAO_MCP, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "ponte", "version": "1"}})
    if metodo == "ping":
        return ok({})
    if metodo == "tools/list":
        return ok({"tools": [FERRAMENTA]})
    if metodo == "tools/call":
        if params.get("name") != NOME:
            return erro(-32602, "ferramenta desconhecida")
        a = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not isinstance(a.get("tool_name"), str) or not isinstance(a.get("input"), dict):
            r = _negar("pedido de permissão mal formado")
        else:
            r = perguntar(env.get("CONVERSA_PONTE_SOCK"), env.get("CONVERSA_PONTE_TURNO"), a["tool_name"], a["input"],
                          a.get("tool_use_id"))
        return ok({"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}]})
    return erro(-32601, "método desconhecido")


def main(entrada=None, saida=None, env=os.environ) -> int:
    entrada = entrada or sys.stdin
    saida = saida or sys.stdout
    for linha in entrada:
        if not linha.strip():
            continue
        try:
            msg = json.loads(linha)
        except ValueError:
            r = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}}
        else:
            r = tratar(msg, env)
        if r is not None:
            saida.write(json.dumps(r, ensure_ascii=False) + "\n")
            saida.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
