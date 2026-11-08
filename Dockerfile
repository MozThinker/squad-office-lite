FROM python:3.13-alpine@sha256:1e1f6334529d1fb26a2ef619d7cffb30a7c7d7cc1cc7338ebee283a58d1aeaca
WORKDIR /app
# fuso da config (office.yaml) com ZoneInfo; sem tzdata só existiria UTC
RUN apk add --no-cache tzdata
# dependências opcionais (ver requirements.txt): só wheel, versões fixas
COPY requirements.txt ./
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt
COPY server.py reader.py aprovacoes.py biblioteca.py quadro.py push.py progresso.py lancar.py officecfg.py office.example.yaml ./
COPY static ./static
# sem USER fixo: o compose gerado corre o contentor com o uid/gid de quem instalou (OFFICE_UID/OFFICE_GID)
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
CMD ["python", "-u", "server.py"]
