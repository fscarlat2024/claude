"""Teste agent stoc: furnizor si WooCommerce simulate cu un server HTTP local."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent_stoc as a  # noqa: E402

FURNIZOR = {"data": {"items": [
    {"manufacturerCode": "ABC-1", "quantity": ">10", "name": "Laptop"},
    {"manufacturerCode": "abc-2", "quantity": 3, "name": "Mouse"},
    {"manufacturerCode": "ABC-3", "quantity": "0", "name": "Monitor"},
]}}

PRODUSE_WOO = [
    {"id": 1, "sku": "ABC-1", "name": "Laptop", "manage_stock": True, "stock_quantity": 2, "type": "simple"},
    {"id": 2, "sku": "ABC-2", "name": "Mouse", "manage_stock": True, "stock_quantity": 3, "type": "simple"},
    {"id": 3, "sku": "XYZ-9", "name": "Cablu", "manage_stock": True, "stock_quantity": 5, "type": "simple"},
    {"id": 4, "sku": "", "name": "Fara cod", "manage_stock": False, "stock_quantity": None, "type": "simple"},
    {"id": 5, "sku": "ABC-3", "name": "Monitor", "manage_stock": True, "stock_quantity": 1, "type": "simple"},
]


@pytest.fixture
def server():
    primite = {"batch": [], "auth": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, date):
            corp = json.dumps(date).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(corp)

        def do_GET(self):
            primite["auth"].append(self.headers.get("Authorization"))
            if self.path.startswith("/furnizor"):
                self._json(FURNIZOR)
            elif self.path.startswith("/wp-json/wc/v3/products?"):
                self._json(PRODUSE_WOO if self.path.endswith("&page=1") else [])
            else:
                self.send_error(404)

        def do_POST(self):
            corp = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            primite["batch"].append((self.path, corp))
            self._json({})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", primite
    srv.shutdown()


def cfg_test(url, **extra):
    return {"FURNIZOR_URL": f"{url}/furnizor", "FURNIZOR_TOKEN": "tok", "FURNIZOR_LISTA": "data.items",
            "SITE_PLATFORMA": "woocommerce", "SITE_URL": url, "SITE_CHEIE": "ck", "SITE_SECRET": "cs",
            **extra}


@pytest.mark.parametrize("val,asteptat", [
    (5, 5), ("7", 7), (">10", 10), ("10+", 10), ("5-10", 5), ("da", 1), ("nu", 0), (-2, 0), (None, None),
])
def test_parseaza_stoc(val, asteptat):
    assert a.parseaza_stoc(val) == asteptat


def test_stoc_de_publicat():
    assert a.stoc_de_publicat(10, {"STOC_REZERVA": "2", "STOC_MAXIM": "5"}) == 5
    assert a.stoc_de_publicat(1, {"STOC_REZERVA": "2"}) == 0


def test_verificare_nu_modifica_site(server, tmp_path):
    url, primite = server
    a.o_verificare(cfg_test(url, MOD="verificare"), tmp_path)
    assert primite["batch"] == []
    assert "Bearer tok" in primite["auth"]
    raport = next(tmp_path.glob("stoc_*.csv")).read_text(encoding="utf-8-sig")
    assert "ABC-1;Laptop;2;10;10;de actualizat" in raport
    assert "ABC-2;Mouse;3;3;3;la fel" in raport
    assert "XYZ-9;Cablu;5;;;lipsa la furnizor" in raport
    assert ";Fara cod;;;;fara SKU" in raport


def test_actualizare_trimite_doar_diferentele(server, tmp_path):
    url, primite = server
    a.o_verificare(cfg_test(url, MOD="actualizare", LIPSA_LA_FURNIZOR="zero"), tmp_path)
    [(cale, corp)] = primite["batch"]
    assert cale == "/wp-json/wc/v3/products/batch"
    assert sorted(corp["update"], key=lambda x: x["id"]) == [
        {"id": 1, "manage_stock": True, "stock_quantity": 10},
        {"id": 3, "manage_stock": True, "stock_quantity": 0},
        {"id": 5, "manage_stock": True, "stock_quantity": 0},
    ]
    assert "ABC-1;Laptop;2;10;10;actualizat" in next(tmp_path.glob("stoc_*.csv")).read_text(encoding="utf-8-sig")


def test_prefix_sku():
    site = [a.ProdusSite("1", "ELK-ABC-1", "Laptop", 0, {})]
    _, modificari = a.compara(site, {"ABC-1": a.ProdusFurnizor("ABC-1", 4)}, {"SKU_PREFIX": "ELK-"})
    assert [(p.id, s) for p, s in modificari] == [("1", 4)]
