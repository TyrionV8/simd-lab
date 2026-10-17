#!/usr/bin/env python3
"""Servidor local: recebe circuitos tipados, nunca C arbitrário do navegador."""

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse

from engine import OPS, analyze
from presets import PRESETS, make_preset
from runner import JobManager, NativeRunner
from validation import REFERENCES

ROOT = Path(__file__).resolve().parent


def cpu_info():
    flags, name = set(), "Processador não identificado"
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith("model name"):
            name = line.split(":", 1)[1].strip()
        if line.startswith("flags"):
            flags = set(line.split(":", 1)[1].split())
            break
    compiler = shutil.which("gcc")
    version = subprocess.run([compiler, "--version"], capture_output=True, text=True, timeout=3).stdout.splitlines()[0] if compiler else None
    return {
        "name": name, "flags": sorted(flags), "compiler": compiler,
        "compilerVersion": version, "cores": sorted(os.sched_getaffinity(0)),
        "assembly": bool(shutil.which("objdump")),
    }


CPU = cpu_info()
NATIVE = NativeRunner(CPU)
JOBS = JobManager(NATIVE)


def run_native(graph):
    """Verificação síncrona usada pelos testes e integrações locais simples."""
    return NATIVE.run(graph, {"benchmark": False, "suite": False})


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'")
        super().end_headers()

    def json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/meta":
                return self.json(200, {
                    "ops": OPS, "cpu": CPU, "references": REFERENCES,
                    "presets": [dict(id=p[0], title=p[1], group=p[2], description=p[3]) for p in PRESETS],
                })
            if path.startswith("/api/preset/"):
                return self.json(200, make_preset(path.rsplit("/", 1)[1]))
            if path.startswith("/api/jobs/"):
                return self.json(200, JOBS.get(path.rsplit("/", 1)[1]))
            if path.startswith("/api/"):
                return self.json(404, {"error": "Recurso não encontrado."})
        except ValueError as exc:
            return self.json(400, {"error": str(exc)})
        return super().do_GET()

    def do_POST(self):
        allowed = {"127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port)}
        origin = self.headers.get("Origin")
        if self.headers.get("Host") not in allowed or (origin and origin not in {"http://" + host for host in allowed}):
            return self.json(403, {"error": "Origem ou Host não permitido."})
        path = urlparse(self.path).path
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 131072:
                raise ValueError("Circuito muito grande ou vazio.")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("A requisição deve ser um objeto JSON.")
            if path == "/api/analyze":
                return self.json(200, analyze(payload, CPU))
            if path in ("/api/jobs/native", "/api/jobs/validate"):
                kind = path.rsplit("/", 1)[1]
                return self.json(202, JOBS.submit(kind, payload.get("graph"), payload.get("options")))
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                return self.json(200, JOBS.cancel(path.split("/")[-2]))
            if path == "/api/run":
                return self.json(200, run_native(payload))
            return self.json(404, {"error": "Recurso não encontrado."})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return self.json(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="Laboratório local de instruções vetoriais")
    parser.add_argument("--port", type=int, default=8765, help="porta local (padrão: 8765)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("A porta deve estar entre 1 e 65535.")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"SIMD Lab: http://127.0.0.1:{args.port}/", flush=True)
    print("Ctrl+C encerra. Compilação, validação e medições ficam neste computador.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for job in JOBS.jobs.values():
            job["cancel"].set()
        JOBS.executor.shutdown(wait=True, cancel_futures=True)
        NATIVE.close()


if __name__ == "__main__":
    main()
