"""Guardião da Conversa (ADR-023, revisão S5), pedido n.º 2 do dono do projecto: o motor não lê as chaves.

O «outro uid» (o `office-conversa`, dono de `/var/lib/office-conversa`) simula-se com o `codigo.gerar()` e um
`chaves.json` (um browser registado) num tmp e `chmod 000` na pasta e no `chaves.json` (salta como root, que lê
tudo). A ponte real corre o `claude` falso com 3 passos `Bash` sem cartão (`pedir: false`, como em `espelho`): `cat`,
`base64` e `xxd` das chaves. Os 3 saem com código ≠ 0 e
nem o hex nem o base64 da chave aparecem em `eventos.jsonl`, na auditoria nem no `registo-*.json`. A configuração da
ponte não tem nenhum caminho para o estado do guardião. A sério (uids distintos) corre no fumo da CI."""
import base64
import importlib.util
import json
import os
import secrets
import shutil
import unittest
from pathlib import Path

import tests.test_chat_ponte as tcp

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("chat_host_codigo", ROOT / "chat_host" / "codigo.py")
codigo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codigo)


@unittest.skipIf(os.geteuid() == 0, "como root o chmod 000 não impede a leitura")
class MotorSemAChave(tcp.PonteBase):
    def setUp(self):
        super().setUp()
        self.gest = self.root / "guardiao"
        codigo.gerar(self.gest, iteracoes=1000)
        self.hex = secrets.token_hex(32)
        (self.gest / "chaves.json").write_text(json.dumps({"v": 1, "chaves": [
            {"kid": secrets.token_hex(8), "chave": self.hex, "criada": "2026-11-23T10:00:00+00:00"}]}))
        self.bruto = (self.gest / "chaves.json").read_bytes()
        (self.gest / "chaves.json").chmod(0o000)
        self.gest.chmod(0o000)

    def tearDown(self):
        self.gest.chmod(0o700)   # para o tearDown do PonteBase conseguir apagar
        super().tearDown()

    def segredos(self) -> list[str]:
        raw = bytes.fromhex(self.hex)
        return [self.hex, self.hex.upper(), base64.b64encode(raw).decode(), base64.b64encode(self.bruto).decode(),
                base64.b64encode(self.bruto).decode()[:24], self.bruto.hex()[:32]]   # o hex do `xxd -p`

    def test_s5_cat_base64_e_xxd_da_chave_falham_e_nada_sai(self):
        chave = self.gest / "chaves.json"
        cmds = [f"cat {chave}", f"base64 {chave}"]
        if shutil.which("xxd"):
            cmds.append(f"xxd {chave}")
        passos = [{"ferramenta": "Bash", "input": {"command": c}, "pedir": False} for c in cmds]
        cid = self.turno_com(passos + [{"texto": "feito"}])
        self.fim(cid)
        [reg] = self.registos()
        self.assertEqual(reg["corridos"], cmds)
        self.assertEqual(len(reg["codigos"]), len(cmds))
        self.assertTrue(all(c != 0 for c in reg["codigos"]), reg["codigos"])
        self.assertEqual(self.ev(cid, "aviso"), [])   # não foi o corte do «fila_tocada»: a leitura falhou mesmo
        textos = {"eventos.jsonl": (self.root / "conversas" / cid / "eventos.jsonl").read_text()}
        for f in (self.root / "estado" / "auditoria").glob("*.jsonl"):
            textos[f.name] = f.read_text()
        for f in (self.root / "trabalho").glob("registo-*.json"):
            textos[f.name] = f.read_text()
        self.assertGreaterEqual(len(textos), 3)
        for nome, texto in textos.items():
            for s in self.segredos():
                self.assertNotIn(s, texto, nome)
        self.assertIn("Permission denied", textos["eventos.jsonl"])   # o resultado do `cat` chegou ao evento

    def test_s5_a_ponte_nao_tem_caminho_para_o_estado_do_guardiao(self):
        self.nova_ponte()
        for cfg in (self.p.cfg, tcp.ponte_mod.config({})):
            texto = repr(cfg)
            self.assertNotIn(str(self.gest), texto)
            self.assertNotIn("/var/lib/office-conversa", texto)
            self.assertNotIn("chave", cfg._fields)
        fonte = (ROOT / "chat_host" / "ponte.py").read_text(encoding="utf-8")
        for proibido in ("CONVERSA_GUARDIAO_ESTADO", "/var/lib/office-conversa", "STATE_DIRECTORY",
                         "assinatura_ok"):
            self.assertNotIn(proibido, fonte)
        self.assertFalse((self.root / "estado" / "chave").exists())
        self.assertFalse((self.root / "estado" / "chaves.json").exists())
