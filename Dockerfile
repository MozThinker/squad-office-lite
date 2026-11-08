FROM python:3.13-alpine
WORKDIR /app
# fuso da config (office.yaml) com ZoneInfo; sem tzdata só existiria UTC
RUN apk add --no-cache tzdata
# miniaturas da Biblioteca (sem Pillow o servidor serve os originais) e Web Push, ADR-004 (sem cryptography o push
# fica a 503); só wheel, versões fixas, também as dependências da cryptography (wheel abi3 musllinux_1_2_x86_64)
RUN pip install --no-cache-dir --only-binary=:all: pillow==12.0.0 cryptography==46.0.3 cffi==2.1.1 pycparser==3.12
COPY server.py reader.py aprovacoes.py biblioteca.py quadro.py push.py progresso.py lancar.py officecfg.py office.example.yaml ./
COPY static ./static
# sem USER fixo: o compose gerado corre o contentor com o uid/gid de quem instalou (OFFICE_UID/OFFICE_GID)
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
CMD ["python", "-u", "server.py"]
