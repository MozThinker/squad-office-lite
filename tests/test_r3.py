"""Máscara reforçada (pedido do Ana, 26/10).

Nomes por segmento, valor na linha seguinte, PEM, DSN, Stripe/Meta/Google e heartbeat das squads.

Tokens falsos construídos em runtime (o gitleaks corre na CI).
"""
import os
import time
import unittest

import reader
import server
from tests import fake
from tests import test_server as base
from tests import cfg

OFFICE_CFG = cfg.LEGADO

S = reader.SECRET
R = ("Zq9" + "x7Kp" * 40)


def r(n: int) -> str:
    return R[:n]


class MaskBase(unittest.TestCase):
    def check(self, cases):
        for raw, want in cases:
            with self.subTest(raw=raw[:60]):
                self.assertEqual(reader.mask(raw), want)


class NamesTest(MaskBase):
    def test_names_by_segment(self):
        v = r(20)
        names = ("API_TOKEN", "CLIENT_SECRET", "AWS_SECRETS", "OPENAI_API_KEY", "DB_PASSWORD", "MYSQL_PASSWD",
                 "SMTP_PASS", "PWD", "STRIPE_SK", "sk", "ADMIN_JWT", "jwt_secret", "SALT", "PRIVATE", "CREDENTIALS",
                 "gcp_credential", "AUTH", "oauth_auth", "SENTRY_DSN", "COOKIE", "session_cookie", "SESSION",
                 "session_id", "apiKey", "clientSecret", "x-api-key-2", "app.jwt", "privateKey", "sessionToken")
        self.check([(f"{n}={v}", f"{n}={S}") for n in names])

    def test_three_forms(self):
        v = r(20)
        self.check([
            (f"STRIPE_SK={v}", f"STRIPE_SK={S}"),
            (f"jwt: {v}", f"jwt: {S}"),
            (f'"salt": "{v}"', f'"salt": "{S}"'),
            (f'{{"sessionToken":"{v}","a":1}}', f'{{"sessionToken":"{S}","a":1}}'),
            (f"  Cookie_Value: {v} fim", f"  Cookie_Value: {S} fim"),
        ])

    def test_no_false_positives(self):
        for s in ("skill: 3", "skills=python", "desk=2", "desktop: true", "KEYBOARD=1", "monkey=banana",
                  "max_tokens=4096", "subagent_tokens: 12", "tokens: 1200", "author: Ana", "authority=x",
                  "passage: texto", "saltar=1", "jwtish=1", "sessions: 3", "dsnx=1", "task: SPEC",
                  "a chave do problema", "pytest -k test_key", "risk=alto", "disk_usage: 80%"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)


class NextLineTest(MaskBase):
    def test_yaml_value_below(self):
        v, w = r(20), r(18)
        self.check([
            (f"db:\n  password:\n    {v}\n  host: x", f"db:\n  password:\n    {S}\n  host: x"),
            (f"private_key: |\n  {v}\n  {w}\nname: ok", f"private_key: |\n  {S}\n  {S}\nname: ok"),
            (f"- token: >-\n    {v}\nfim", f"- token: >-\n    {S}\nfim"),
        ])

    def test_env_continuation_and_multiline_quotes(self):
        v, w = r(20), r(18)
        self.check([
            (f"export GH_TOKEN=\\\n  {v}\nls", f"export GH_TOKEN=\\\n  {S}\nls"),
            (f'SECRET_KEY="{v}\n{w}"\nNEXT=1', f'SECRET_KEY="{S}"\nNEXT=1'),
        ])

    def test_next_line_false_positives(self):
        for s in ("steps:\n  - run: pytest\n", "keyboard:\n  layout: pt\n", "tokens:\n  1200\n",
                  "password:\nfim sem indentação"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)


class FormatsTest(MaskBase):
    def test_pem(self):
        for kind in ("RSA ", "EC ", "OPENSSH ", ""):
            h, f = f"-----BEGIN {kind}" + "PRIVATE KEY-----", f"-----END {kind}" + "PRIVATE KEY-----"
            self.check([(f"x\n{h}\n{r(64)}\n{r(40)}\n{f}\ny", f"x\n{S}\ny")])
        self.assertEqual(reader.mask("-----BEGIN " + "PUBLIC KEY-----"), "-----BEGIN " + "PUBLIC KEY-----")

    def test_db_urls(self):
        p = r(14)
        self.check([
            (f"postgres://app:{p}@db:5432/x", f"postgres://app:{S}@db:5432/x"),
            (f"postgresql://:{p}@db/x", f"postgresql://:{S}@db/x"),
            (f"redis://default:{p}@cache:6379/0", f"redis://default:{S}@cache:6379/0"),
            (f"rediss://{p}@cache:6380", f"rediss://{S}@cache:6380"),
            (f"mongodb+srv://u:{p}@c0.mongodb.net/db", f"mongodb+srv://u:{S}@c0.mongodb.net/db"),
            (f"mongodb://{p}@h:27017", f"mongodb://{S}@h:27017"),
        ])
        for s in ("postgres://db:5432/x", "redis://cache:6379/0", "mongodb://h:27017/db", "git@github.com:o/r.git"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)

    def test_vendor_tokens(self):
        self.check([
            ("sk_" + "live_" + r(24) + " fim", f"{S} fim"),
            ("sk_" + "test_" + r(24), S),
            ("rk_" + "live_" + r(24), S),
            ("rk_" + r(24), S),
            ("whsec_" + r(32), S),
            ("EAA" + "Bw" + r(40), S),
            ("AIza" + "Sy" + r(33), S),
        ])
        for s in ("pk_live_publico", "rk_curto", "EAAB", "AIzaCurto", "risk_level"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)


class SpeedTest(unittest.TestCase):
    def test_linear(self):
        for adv in ("k_" * 100000 + "=", 'KEY="' + '\\"' * 100000, "aB" * 100000 + ":",
                    "a_key:\n" + "  x\n" * 50000, "x-" * 100000 + "sk", "password:\n" + " " * 200000):
            with self.subTest(adv=adv[:20]):
                t = time.perf_counter()
                reader.mask(adv)
                self.assertLess(time.perf_counter() - t, 1.5)


class SquadLaunchersTest(unittest.TestCase):
    # mkt/rnd/sup_task.py gravam <office>/<id>/heartbeat e meta["result"] (commits de 26/10 nos três repos)
    def setUp(self):
        base.ServerTest.setUp(self)
        for d in (self.rnd, self.sup):
            d.mkdir()

    def tearDown(self):
        base.ServerTest.tearDown(self)

    def run_meta(self, root, tid, beat_age):
        now = time.time()
        base.write_json(root / tid / "meta.json", {"id": tid, "status": "a correr", "pid": 123,
                                                   "started_at": "2026-10-26T09:00:00", "session_id": fake.SID,
                                                   "claude_dir": "-x", "result": "nunca no estado"})
        if beat_age is not None:
            hb = root / tid / "heartbeat"
            hb.write_text("")
            os.utime(hb, (now - beat_age, now - beat_age))
        return now

    def test_heartbeat_of_every_squad(self):
        for root, squad in ((self.mkt, "marketing"), (self.rnd, "rnd"), (self.sup, "support")):
            self.run_meta(root, f"{squad}-morta", 6 * 60)
            self.run_meta(root, f"{squad}-viva", 30)
        st = server.build_state()
        by = {t["id"]: t for t in st["tasks"]}
        for squad in ("marketing", "rnd", "support"):
            with self.subTest(squad=squad):
                self.assertEqual((by[f"{squad}-morta"]["status"], by[f"{squad}-morta"]["squad"]), ("morreu", squad))
                self.assertEqual(by[f"{squad}-viva"]["status"], "a correr")
        self.assertEqual(sorted(i for i in st["alerts"]["items"] if i.startswith("tarefa:")),
                         ["tarefa:marketing-morta", "tarefa:rnd-morta", "tarefa:support-morta"])
        # 0.1.3: o result só sai mascarado em tasks[].resultado (cartão com o erro), em mais nenhum sítio
        self.assertEqual({t["resultado"] for t in st["tasks"]}, {"nunca no estado"})
        sem = dict(st, tasks=[{k: v for k, v in t.items() if k != "resultado"} for t in st["tasks"]])
        self.assertNotIn("nunca no estado", str(sem))


if __name__ == "__main__":
    unittest.main()
