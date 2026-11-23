"""numpy falso para os testes da voz (ADR-023): um «array» só sabe quantas amostras tem."""


class ndarray:   # o nome do numpy
    def __init__(self, size):
        self.size = size


def concatenate(partes, axis=0):
    assert axis is None
    if not partes:
        raise ValueError("need at least one array to concatenate")
    return ndarray(sum(p.size for p in partes))
