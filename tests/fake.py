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


def assistant(blocks: list, age: float = 0) -> str:
    return line({"type": "assistant", "timestamp": ts(age), "message": {"role": "assistant", "content": blocks}})


def tool_use(tid: str, name: str, inp: dict | None = None, age: float = 0) -> str:
    return assistant([{"type": "tool_use", "id": tid, "name": name, "input": inp or {}}], age)


def tool_result(tid: str, is_error: bool = False, content="ok", age: float = 0) -> str:
    return line({"type": "user", "timestamp": ts(age), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}]}})


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
