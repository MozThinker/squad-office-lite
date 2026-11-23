#!/usr/bin/env python3
"""`claude -p` falso para os testes da Conversa (ADR-023): nunca chama nada de fora nem gasta quota. O tests/bin/claude
passa para aqui quando vê `--permission-prompt-tool` no argv (o modo stream-json da ponte).

Imita o `claude -p --input-format stream-json --output-format stream-json`: lê a 1.ª linha do stdin (a mensagem do
dono), arranca o servidor MCP do `--mcp-config` (o chat_host/mcp.py real) e fala com ele por stdio, escreve no stdout
`system/init`, `assistant`, `user` (tool_result) e `result`, e depois espera que o stdin feche (como o real, que fica à
espera de mais mensagens). O cenário vem de `<cwd>/cenario.json`:

  {"session_id": "s-1",          # do init quando não há --resume (com --resume, o id dado)
   "mcp_falha": false,           # o init diz que o servidor «ponte» falhou
   "passos": [
     {"texto": "olá"},
     {"ferramenta": "Bash", "input": {"command": "…"}, "id": "toolu_1",
      "pedir": true,             # pergunta ao mcp__ponte__permissao (omissão); false = corre sem perguntar;
                                 # "regras" = decide como o real: pelas regras do --settings e pelo --permission-mode
      "negado": false},          # «classificador» recusa: tool_result de erro sem perguntar nem correr
     {"pergunta": [{"question": "…", "header": "…", "options": [{"label": "…", "description": "…"}],
                    "multiSelect": false}]},
     {"plano": "texto do plano"},
     {"filho": true},            # um `sleep 300` no mesmo grupo de processos (o «Parar» tem de o matar)
     {"dormir": 2.5}],
   "result": {"total_cost_usd": 0.5, …},   # chaves que trocam as do result de omissão
   "sem_result": false, "codigo": 0,
   "dormir_no_fim": 0,            # depois de o stdin fechar, demora isto a sair (motor lento a sair)
   "sem_init_se_resume": false}   # com --resume, sai com 1 sem escrever nada (sessão perdida)

Uma ferramenta aprovada corre de verdade só se for `Bash` (no cwd, `bash -c`, com o `updatedInput`); as outras dão «ok».
Cada processo grava `<cwd>/registo-<pid>.json` (reescrito a cada passo): argv, env, cwd, stdin, conteúdo do
`--mcp-config`, respostas do MCP, pid do filho, os comandos corridos e o código de saída de cada um (`codigos`).
"""
import fnmatch
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CWD = Path.cwd()
REG = CWD / f"registo-{os.getpid()}.json"


def arg(nome):
    a = sys.argv[1:]
    return a[a.index(nome) + 1] if nome in a and a.index(nome) + 1 < len(a) else None


def regras(tipo):
    # [(ferramenta, padrão ou None)] das regras `tipo` (ask/deny) do ficheiro do --settings
    try:
        lista = json.loads(Path(arg("--settings")).read_text())["permissions"].get(tipo, [])
    except (OSError, ValueError, KeyError, TypeError):
        return []
    out = []
    for r in lista:
        nome, _, resto = r.partition("(")
        out.append((nome, resto[:-1] if resto.endswith(")") else None))
    return out


def casa(lista, nome, entrada):
    alvo = entrada.get("command") or entrada.get("file_path") or entrada.get("url") or ""
    return any(n == nome and (p is None or fnmatch.fnmatchcase(str(alvo), p)) for n, p in lista)


def decidir(nome, entrada):
    """"regras": "negado" (deny casa), "pedir" (ask casa ou modo sem auto) ou "correr" (auto sem ask)."""
    if casa(regras("deny"), nome, entrada):
        return "negado"
    if casa(regras("ask"), nome, entrada) or arg("--permission-mode") != "auto":
        return "pedir"
    return "correr"


def sair(d):
    sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class Mcp:
    def __init__(self, cfg_path):
        self.p, self.ok, self.i = None, False, 0
        try:
            srv = json.loads(Path(cfg_path).read_text())["mcpServers"]["ponte"]
            env = {**os.environ, **srv.get("env", {})}
            self.p = subprocess.Popen([srv["command"], *srv.get("args", [])], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True, env=env)
            r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "claude-falso", "version": "0"}})
            self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            self.p.stdin.flush()
            nomes = [t["name"] for t in self.rpc("tools/list", {})["result"]["tools"]]
            self.ok = "result" in r and "permissao" in nomes
        except (OSError, ValueError, KeyError, TypeError):
            self.ok = False

    def rpc(self, metodo, params):
        self.i += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.i, "method": metodo, "params": params}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def permissao(self, nome, entrada, tid):
        r = self.rpc("tools/call", {"name": "permissao",
                                    "arguments": {"tool_name": nome, "input": entrada, "tool_use_id": tid}})
        return json.loads(r["result"]["content"][0]["text"])

    def fechar(self):
        if self.p:
            self.p.stdin.close()
            self.p.wait(5)


def main() -> int:
    try:
        cen = json.loads((CWD / "cenario.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cen = {}
    stdin = sys.stdin.readline()
    mcp_cfg = arg("--mcp-config")
    reg = {"argv": sys.argv[1:], "env": dict(os.environ), "cwd": str(CWD), "stdin": stdin,
           "mcp_config": Path(mcp_cfg).read_text() if mcp_cfg and os.path.exists(mcp_cfg) else None,
           "respostas": [], "filho": None, "corridos": [], "codigos": []}

    def gravar():
        tmp = REG.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, ensure_ascii=False))
        os.replace(tmp, REG)

    gravar()
    if cen.get("sem_init_se_resume") and arg("--resume"):
        return 1
    mcp = Mcp(mcp_cfg) if mcp_cfg else None
    sid = arg("--resume") or cen.get("session_id", "s-falsa")
    estado = "failed" if cen.get("mcp_falha") or not (mcp and mcp.ok) else "connected"
    sair({"type": "system", "subtype": "init", "session_id": sid, "cwd": str(CWD),
          "mcp_servers": [{"name": "ponte", "status": estado}], "tools": ["Bash", "mcp__ponte__permissao"]})

    def assistente(bloco):
        sair({"type": "assistant", "session_id": sid,
              "message": {"role": "assistant", "content": [bloco]}})

    def resultado(tid, texto, erro=False):
        sair({"type": "user", "session_id": sid, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": texto, "is_error": erro}]}})

    n = 0
    for p in cen.get("passos", []):
        n += 1
        if "texto" in p:
            assistente({"type": "text", "text": p["texto"]})
        elif "dormir" in p:
            time.sleep(p["dormir"])
        elif "filho" in p:
            reg["filho"] = subprocess.Popen(["sleep", "300"]).pid
        else:
            if "ferramenta" in p:
                nome, entrada = p["ferramenta"], p.get("input", {})
            elif "pergunta" in p:
                nome, entrada = "AskUserQuestion", {"questions": p["pergunta"]}
            else:
                nome, entrada = "ExitPlanMode", {"plan": p["plano"]}
            tid = p.get("id") or f"toolu_{n:03d}"
            assistente({"type": "tool_use", "id": tid, "name": nome, "input": entrada})
            pedir = p.get("pedir", True)
            if pedir == "regras":
                d = decidir(nome, entrada)
                reg.setdefault("decisoes", []).append({"id": tid, "decisao": d})
                pedir = d == "pedir"
                if d == "negado":
                    p = {**p, "negado": True}
            if p.get("negado"):
                resultado(tid, "Permission to use Bash has been denied.", True)
                gravar()
                continue
            if pedir:
                r = mcp.permissao(nome, entrada, tid) if mcp else {"behavior": "deny", "message": "sem mcp"}
                reg["respostas"].append({"id": tid, "ferramenta": nome, "resposta": r})
            else:
                r = {"behavior": "allow", "updatedInput": entrada}
            if r.get("behavior") != "allow":
                resultado(tid, r.get("message", ""), True)
            elif nome == "Bash":
                cmd = r["updatedInput"].get("command", "")
                c = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=False)
                reg["corridos"].append(cmd)
                reg["codigos"].append(c.returncode)
                resultado(tid, c.stdout + c.stderr, c.returncode != 0)
            else:
                resultado(tid, "ok")
        gravar()
    if mcp:
        mcp.fechar()
    if not cen.get("sem_result"):
        r = {"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "num_turns": n + 1,
             "total_cost_usd": 0.0123, "result": "feito",
             "usage": {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 3,
                       "cache_read_input_tokens": 4}}
        r.update(cen.get("result", {}))
        sair(r)
        sys.stdin.read()   # como o real em stream-json: só sai quando a ponte fecha o stdin
        time.sleep(cen.get("dormir_no_fim", 0))
    gravar()
    return int(cen.get("codigo", 0))


if __name__ == "__main__":
    sys.exit(main())
