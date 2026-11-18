"""Linhas de transcript falsas para os testes v2 (helpers, sem testes).

Tokens falsos são construídos por concatenação em runtime para o gitleaks não os apanhar.
"""
import json
from datetime import datetime, timezone

from tests.test_server import NOW


def ts(age: float) -> str:
    return datetime.fromtimestamp(NOW - age, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def line(d: dict) -> str:
    # linhas user/assistant levam entrypoint como as sessões headless reais (o leitor só abre "sdk-cli")
    if d.get("type") in ("user", "assistant"):
        d = {**d, "entrypoint": d.get("entrypoint", "sdk-cli")}
    return json.dumps(d) + "\n"


def _extra(model=None, git_branch=None) -> tuple[dict, dict]:
    # (chaves de topo, chaves da message): só as que foram pedidas, para os testes antigos não mudarem
    top = {"gitBranch": git_branch} if git_branch is not None else {}
    return top, ({"model": model} if model is not None else {})


def assistant(blocks: list, age: float = 0, model=None, git_branch=None) -> str:
    top, msg = _extra(model, git_branch)
    return line({"type": "assistant", "timestamp": ts(age), **top,
                 "message": {"role": "assistant", "content": blocks, **msg}})


def tool_use(tid: str, name: str, inp: dict | None = None, age: float = 0, model=None, git_branch=None) -> str:
    return assistant([{"type": "tool_use", "id": tid, "name": name, "input": inp or {}}], age, model, git_branch)


def tool_result(tid: str, is_error: bool = False, content="ok", age: float = 0, git_branch=None,
                tool_use_result=None) -> str:
    top, _ = _extra(None, git_branch)
    if tool_use_result is not None:
        top["toolUseResult"] = tool_use_result
    return line({"type": "user", "timestamp": ts(age), **top, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}]}})


# --- progresso, linhas alteradas (office v3 ambiente): o input traz texto que nunca pode sair ---
def todo_write(tid: str, estados: list, content: str = "tarefa", age: float = 0, **kw) -> str:
    todos = [{"content": f"{content} {i}", "status": s, "activeForm": f"{content} a fazer {i}"}
             for i, s in enumerate(estados)]
    return tool_use(tid, "TodoWrite", {"todos": todos}, age, **kw)


def task_create(tid: str, subject: str = "tarefa", age: float = 0, **kw) -> str:
    return tool_use(tid, "TaskCreate", {"subject": subject, "description": subject + " (descrição)",
                                        "activeForm": subject}, age, **kw)


def task_update(tid: str, task_id, status, age: float = 0, **kw) -> str:
    return tool_use(tid, "TaskUpdate", {"taskId": task_id, "status": status}, age, **kw)


def edit(tid: str, old: str, new: str, age: float = 0, file_path: str = "/x/a.py", **kw) -> str:
    return tool_use(tid, "Edit", {"file_path": file_path, "old_string": old, "new_string": new}, age, **kw)


def multi_edit(tid: str, pares: list, age: float = 0, file_path: str = "/x/a.py", **kw) -> str:
    return tool_use(tid, "MultiEdit", {"file_path": file_path,
                                       "edits": [{"old_string": o, "new_string": n} for o, n in pares]}, age, **kw)


def write(tid: str, content: str, age: float = 0, file_path: str = "/x/a.py", **kw) -> str:
    return tool_use(tid, "Write", {"file_path": file_path, "content": content}, age, **kw)


def edit_result(tid: str, patch: list | None = None, is_error: bool = False, age: float = 0, **kw) -> str:
    # patch = as «lines» de um bloco do structuredPatch (com o +/-/espaço à frente); sem patch, sem toolUseResult
    tur = None
    if patch is not None:
        tur = {"filePath": "/x/a.py", "originalFile": "orig", "structuredPatch": [
            {"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1, "lines": patch}]}
    return tool_result(tid, is_error, "ok", age, tool_use_result=tur, **kw)


def thinking(age: float = 0) -> str:
    return assistant([{"type": "thinking", "thinking": "", "signature": "x"}], age)


def text(t: str, age: float = 0) -> str:
    return assistant([{"type": "text", "text": t}], age)


def prompt(t: str, age: float = 0) -> str:
    return line({"type": "user", "timestamp": ts(age), "message": {"role": "user", "content": t}})


def pr_link(n: int, url: str, age: float = 0) -> str:
    return line({"type": "pr-link", "prNumber": n, "prUrl": url, "timestamp": ts(age)})


def junk() -> str:
    # linhas que o rastreador e o leitor têm de ignorar (algumas trazem tool_use/thinking lá dentro)
    blob = {"content": [{"type": "tool_use", "id": "jx", "name": "Bash", "input": {"command": "rm -rf /"}},
                        {"type": "thinking", "thinking": ""}]}
    out = "".join(line({"type": t, "timestamp": ts(0), "message": blob})
                  for t in ("attachment", "queue-operation", "api-request", "api-request-blob", "atis-latch",
                            "last-prompt", "cost-state"))
    return out + ('{"type": "assistant", "message": {"content": [{"type": "tool_use"\n'   # truncada
                  + "isto não é json \"tool_use\"\n[1, \"tool_use\"]\n"
                  + line({"type": "assistant", "message": {"content": "tool_use"}})
                  + line({"type": "assistant", "message": {"content": [5, None, {"type": "thinking_x"}]}}))


SID = "0f0e0d0c-1111-4222-8333-123456789abc"   # session_id (UUID) das metas falsas de marketing/R&D/Support


def gh_token() -> str:
    return "gh" + "p_" + "A1b2C3d4" * 5


# --- claude.log (ADR-013): linha "result" realista do Claude Code headless; kw substitui ---
SEGREDO_FIM = "SEGREDO-x1"
CRED = "1767225600987"            # credential_expires_at falso, fácil de procurar
SESSAO_FIM = "5e55a0de-0000-4000-8000-c0ffee000001"
TOOLU = "toolu_01ZZsegredoZZ"


def recusa(nome="Bash") -> dict:
    # como no claude.log real: tool_input traz o comando inteiro (nunca pode sair)
    return {"tool_name": nome, "tool_use_id": TOOLU, "tool_input": {"command": f"echo {SEGREDO_FIM}",
                                                                     "description": "x"}}


def result_line(**kw) -> str:
    d = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 351343, "duration_api_ms": 1,
         "num_turns": 4, "result": f"resumo final {SEGREDO_FIM}", "stop_reason": "end_turn", "session_id": SESSAO_FIM,
         "total_cost_usd": 3.3, "usage": {"input_tokens": 1}, "modelUsage": {}, "permission_denials": [],
         "terminal_reason": "completed", "api_error_status": None, "credential_expires_at": int(CRED),
         "uuid": "u-1", "fast_mode_state": "off"}
    d.update(kw)
    return json.dumps(d) + "\n"
