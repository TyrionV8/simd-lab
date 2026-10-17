"""Compilação em cache, rastros, casos de validação e medições em processo C."""

from concurrent.futures import ThreadPoolExecutor
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import statistics
import struct
import subprocess
import tempfile
import threading
import time
import uuid

from engine import analyze
from numerics import float_bits
from validation import compare, test_cases

COMPILER_FLAGS = ["-std=c11", "-O2", "-g", "-ffp-contract=off", "-fno-tree-vectorize"]
COMPILE_COMMAND = "gcc " + " ".join(COMPILER_FLAGS) + " circuito.c -lm -o circuito"


def check_options(raw, cpu):
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("Configuração de execução inválida.")
    defaults = {"repetitions": 10000, "samples": 7, "warmup": 500, "randomCount": 12, "seed": 2696}
    bounds = {"repetitions": (100, 100000), "samples": (3, 11), "warmup": (0, 10000), "randomCount": (0, 32), "seed": (0, 0xFFFFFFFF)}
    options = {}
    for key, default in defaults.items():
        value = raw.get(key, default)
        lower, upper = bounds[key]
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"{key}: use um inteiro entre {lower} e {upper}.")
        options[key] = value
    options["benchmark"] = raw.get("benchmark", True)
    options["suite"] = raw.get("suite", True)
    if type(options["benchmark"]) is not bool or type(options["suite"]) is not bool:
        raise ValueError("As opções de medição e validação devem ser booleanas.")
    core = raw.get("core", cpu.get("cores", [-1])[0])
    if type(core) is not int or core not in [-1] + cpu.get("cores", []):
        raise ValueError("Escolha um núcleo disponível ou −1 para o agendador.")
    options["core"] = core
    return options


def trace_differences(native, report):
    mismatches, elements = [], 0
    for nid, node in report["nodes"].items():
        if not node["active"]:
            continue
        for port, value in node["outputs"].items():
            key = nid + "." + port
            actual = native.get(key)
            expected = value["values"]
            if actual is None or len(actual) != len(expected):
                mismatches.append({"key": key, "message": "Tamanho de vetor diferente.", "actual": actual, "expected": expected})
                continue
            for index, (a, b) in enumerate(zip(actual, expected)):
                elements += 1
                equal = float_bits(a) == float_bits(b) if value["kind"] == "f32" else a == b
                if not equal:
                    mismatches.append({"key": key, "index": index, "actual": a, "expected": b})
    return mismatches, elements


def summarize_benchmark(raw):
    repeats = raw["repetitions"]
    samples = raw["samples"]
    durations = [sample["wallNs"] / repeats for sample in samples]
    cpu_durations = [sample["cpuNs"] / repeats for sample in samples]
    median = statistics.median(durations)
    cycles = [s["cycles"] * s["enabledNs"] / s["runningNs"] / repeats for s in samples if s["perfRead"] and s["runningNs"]]
    return {
        **raw,
        "medianNs": median,
        "minNs": min(durations),
        "maxNs": max(durations),
        "cpuMedianNs": statistics.median(cpu_durations),
        "spreadPercent": (max(durations) - min(durations)) / median * 100 if median else 0,
        "blocksPerSecond": 1e9 / median if median else None,
        "tscTicksPerBlock": statistics.median(s["tscTicks"] / repeats for s in samples) if raw["hasTsc"] else None,
        "coreCyclesPerBlock": statistics.median(cycles) if len(cycles) == len(samples) else None,
        "emptyMedianNs": statistics.median(s["emptyNs"] / repeats for s in samples),
        "migrations": sum(s["migrated"] for s in samples),
        "perfReason": "Permissão negada pelo Linux" if raw["perfErrno"] in (errno.EPERM, errno.EACCES) else os.strerror(raw["perfErrno"]) if raw["perfErrno"] else ("Contador disponível" if cycles else "O contador não retornou amostras"),
        "note": "Tempos medidos dentro do C, com dados quentes e o circuito materializado. Incluem chamadas, cópias e escrita do resultado; não são latências isoladas nem tokens/s. TSC é um contador de referência, não ciclos do núcleo. O loop vazio é mostrado sem subtração.",
    }


class NativeRunner:
    def __init__(self, cpu):
        self.cpu = cpu
        self.directory = tempfile.TemporaryDirectory(prefix="simd-lab-builds-")
        self.lock = threading.Lock()
        self.cache = {}

    def close(self):
        self.directory.cleanup()

    def compile(self, report, progress, cancel):
        digest = hashlib.sha256(report["code"].encode()).hexdigest()
        with self.lock:
            cached = self.cache.get(digest)
            if cached:
                progress("compile", 15, "Código já compilado · reutilizando o binário")
                return cached, True, 0.0
            folder = Path(self.directory.name) / digest
            folder.mkdir(exist_ok=True)
            source, binary = folder / "circuito.c", folder / "circuito"
            source.write_text(report["code"])
            progress("compile", 8, "Compilando C com entradas de execução")
            started = time.monotonic()
            self._process([self.cpu["compiler"], *COMPILER_FLAGS, str(source), "-lm", "-o", str(binary)], cancel, timeout=20)
            elapsed = (time.monotonic() - started) * 1000
            objdump = shutil.which("objdump")
            assembly = "objdump não está disponível neste computador."
            if objdump:
                assembly = self._process([objdump, "-d", "-Mintel", str(binary)], cancel, timeout=5).decode()
            artifact = {"binary": binary, "assembly": assembly, "source": report["code"]}
            self.cache[digest] = artifact
            if len(self.cache) > 6:
                oldest = next(iter(self.cache))
                if oldest != digest:
                    self.cache.pop(oldest)
                    shutil.rmtree(Path(self.directory.name) / oldest)
            return artifact, False, elapsed

    @staticmethod
    def _process(command, cancel, timeout=15, input_data=None):
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(command, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
            if input_data is not None:
                process.stdin.write(input_data)
                process.stdin.close()
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if cancel.is_set():
                        raise InterruptedError("Execução cancelada.")
                    if time.monotonic() - started > timeout:
                        raise TimeoutError("A etapa excedeu o limite de tempo.")
                    time.sleep(.02)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
            stdout.seek(0)
            stderr.seek(0)
            if process.returncode:
                raise ValueError("A etapa terminou com código " + str(process.returncode) + ":\n" + stderr.read().decode(errors="replace"))
            return stdout.read()

    def run(self, graph, options=None, progress=lambda *args: None, cancel=None):
        cancel = cancel or threading.Event()
        options = check_options(options, self.cpu)
        report = analyze(graph, self.cpu)
        if not report["valid"]:
            raise ValueError(report["message"])
        if report["missing"]:
            raise ValueError("Execução bloqueada: este processador não oferece " + ", ".join(report["missing"]) + ". A simulação e o código continuam disponíveis.")
        if not self.cpu["compiler"]:
            raise ValueError("O compilador gcc não foi encontrado.")
        artifact, cached, compile_ms = self.compile(report, progress, cancel)
        progress("trace", 25, "Executando e conferindo todos os valores intermediários")
        command = [str(artifact["binary"])]
        if options["benchmark"]:
            command += [str(options[k]) for k in ("repetitions", "samples", "core", "warmup")]
            progress("measure", 30, "Aquecimento e amostras de tempo no processo C")
        output = self._process(command, cancel, timeout=20).decode().splitlines()
        trace = json.loads(output[0])["values"]
        differences, element_count = trace_differences(trace, report)
        output_id = next(n["id"] for n in report["graph"]["nodes"] if n["op"] == "output")
        native_output = trace[output_id + ".value"][0]
        result = {
            "values": trace, "matched": not differences,
            "differences": differences, "elementsChecked": element_count,
            "comparison": compare(native_output, report["reference"], report["graph"]["inputs"]),
            "benchmark": summarize_benchmark(json.loads(output[1])["benchmark"]) if len(output) > 1 else None,
            "compileMs": compile_ms, "cached": cached,
            "command": COMPILE_COMMAND, "assembly": artifact["assembly"],
            "options": options, "cpu": self.cpu,
            "inputs": report["graph"]["inputs"], "referenceMode": report["oracle"]["mode"],
        }
        if options["suite"]:
            result["suite"] = self.validate(report["graph"], options, progress, cancel, artifact)
        progress("done", 100, "Execução, medições e conferências concluídas")
        return result

    def validate(self, graph, options=None, progress=lambda *args: None, cancel=None, artifact=None):
        cancel = cancel or threading.Event()
        options = check_options(options, self.cpu)
        report = analyze(graph, self.cpu, include_code=False)
        if not report["valid"]:
            raise ValueError(report["message"])
        cases = test_cases(report["graph"]["inputs"], options["randomCount"], options["seed"])
        results = []
        for index, case in enumerate(cases):
            if cancel.is_set():
                raise InterruptedError("Validação cancelada.")
            progress("validate", 50 + int(45 * index / len(cases)), f"Caso {index + 1} de {len(cases)} · {case['name']}")
            candidate = copy.deepcopy(report["graph"])
            candidate["inputs"] = case["inputs"]
            simulated = analyze(candidate, self.cpu, include_code=False)
            record = {"name": case["name"], "inputs": case["inputs"], "error": None, "comparison": simulated["comparison"], "nativeMatched": None}
            if artifact and simulated["valid"]:
                inputs = simulated["graph"]["inputs"]
                packet = struct.pack("<64b64ff", *inputs["weights"], *inputs["activations"], inputs["weightScale"])
                native = json.loads(self._process([str(artifact["binary"]), "--stdin"], cancel, timeout=4, input_data=packet))
                diff, _ = trace_differences(native["values"], simulated)
                record["nativeMatched"] = not diff
                record["nativeDifferences"] = diff
            if not simulated["valid"]:
                record["error"] = simulated["message"]
            record["passed"] = simulated["passed"] and record["nativeMatched"] is not False
            results.append(record)
        return {"cases": results, "passed": sum(r["passed"] for r in results), "total": len(results), "seed": options["seed"], "referenceMode": report["graph"]["inputs"]["referenceMode"], "policy": report["graph"]["inputs"]["validationPolicy"], "native": artifact is not None}


class JobManager:
    def __init__(self, runner):
        self.runner = runner
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simd-lab")
        self.lock = threading.Lock()
        self.jobs = {}

    def submit(self, kind, graph, options):
        report = analyze(graph, self.runner.cpu, include_code=False)
        if not report["valid"]:
            raise ValueError(report["message"])
        options = check_options(options, self.runner.cpu)
        with self.lock:
            if any(j["status"] in ("queued", "running") for j in self.jobs.values()):
                raise ValueError("Já há um trabalho ativo. Aguarde ou cancele antes de iniciar outro.")
            jid = uuid.uuid4().hex
            job = {"id": jid, "status": "queued", "phase": "queued", "progress": 0, "message": "Preparando trabalho", "result": None, "error": None, "cancel": threading.Event()}
            self.jobs[jid] = job
            if len(self.jobs) > 8:
                self.jobs.pop(next(iter(self.jobs)))

        def update(phase, percent, message):
            with self.lock:
                job.update(phase=phase, progress=percent, message=message)

        def work():
            with self.lock:
                job["status"] = "running"
            try:
                method = self.runner.run if kind == "native" else self.runner.validate
                result = method(graph, options, update, job["cancel"])
                with self.lock:
                    job.update(status="done", progress=100, result=result, message="Concluído")
            except InterruptedError as exc:
                with self.lock:
                    job.update(status="cancelled", error=str(exc), message=str(exc))
            except Exception as exc:
                with self.lock:
                    job.update(status="error", error=str(exc), message=str(exc))

        self.executor.submit(work)
        return {"jobId": jid}

    def get(self, jid):
        with self.lock:
            if jid not in self.jobs:
                raise ValueError("Trabalho não encontrado.")
            return {key: value for key, value in self.jobs[jid].items() if key != "cancel"}

    def cancel(self, jid):
        with self.lock:
            if jid not in self.jobs:
                raise ValueError("Trabalho não encontrado.")
            self.jobs[jid]["cancel"].set()
        return {"cancelled": True}
