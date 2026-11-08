"""Config por omissão dos testes: o módulo que declara OFFICE_CFG corre cada teste com essa config."""
import pytest


@pytest.fixture(autouse=True)
def _office_cfg(request):
    cfg = getattr(request.module, "OFFICE_CFG", None)
    if cfg is None:
        yield
        return
    import server
    from tests import cfg as tcfg
    server.configurar(cfg)
    yield
    tcfg.repor()
