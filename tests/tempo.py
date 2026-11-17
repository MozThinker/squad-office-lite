"""Limites de tempo dos testes «sem regex quadrática» (r1, r2, r3).

Mede o tempo de CPU da própria thread (`time.thread_time`), não o do relógio: com a máquina carregada (vários agentes
ou suites em paralelo) o relógio conta o tempo à espera de CPU e os testes falhavam sem nada estar errado. Uma regex
quadrática continua a falhar: em 200 KB adversariais gasta minutos de CPU, não segundos.

O limite (2 s) dá ≈ 5× o pior caso medido sozinho (≈ 0,42 s em 200 KB, numa máquina de 4 núcleos); o tempo cresce
linearmente com a entrada (0,1 / 0,2 / 0,4 / 0,8 s para 50 / 100 / 200 / 400 KB).
"""
import time

CPU_MAX_S = 2.0


def cpu(fn, *args, **kwargs) -> float:
    t = time.thread_time()
    fn(*args, **kwargs)
    return time.thread_time() - t
