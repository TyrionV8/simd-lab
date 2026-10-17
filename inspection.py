"""Informações didáticas dos tipos, funções C e relógio lógico do circuito."""

import math
import re

TYPE_INFO = {
    "p4s": (4, "inteiro com sinal", "int4 · pares assinados"),
    "p4u": (4, "inteiro com sinal, código com viés +8", "int4 · planar"),
    "u8": (8, "inteiro sem sinal", "uint8"),
    "i8": (8, "inteiro com sinal", "int8"),
    "i16": (16, "inteiro com sinal", "int16"),
    "i32": (32, "inteiro com sinal", "int32"),
    "f32": (32, "ponto flutuante", "FP32"),
}


def type_description(kind, count, register_bits=128):
    bits, family, label = TYPE_INFO[kind]
    packed = kind.startswith("p4")
    logical_count = count * 2 if packed else count
    return {
        "bits": bits, "family": family, "label": label,
        "logicalCount": logical_count, "packed": packed,
        "lanes": register_bits // bits,
        "groups": math.ceil(logical_count * bits / register_bits),
        "generic": f"{logical_count} números de {bits} bits · {family}",
    }


def functions_in(source):
    """Extrai corpos de funções ignorando chaves dentro de strings e comentários."""
    functions = {}
    for match in re.finditer(r"(?m)^static[^\n{]*\b([a-zA-Z_]\w*)\([^;\n]*\)\s*\{", source):
        name = match.group(1)
        start, index, depth, quote, escaped = match.start(), match.end(), 1, None, False
        while index < len(source) and depth:
            char = source[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in ('"', "'"):
                quote = char
            elif source[index:index + 2] == "/*":
                end = source.find("*/", index + 2)
                index = len(source) if end < 0 else end + 1
            elif source[index:index + 2] == "//":
                end = source.find("\n", index + 2)
                index = len(source) if end < 0 else end
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
        functions[name] = source[start:index]
    return functions


def enrich(report, operations):
    code = report.get("code", "")
    helpers = functions_in(code)
    markers = list(re.finditer(r"/\* (\w+) · [^\n]*\*/", code))
    snippets = {}
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else code.find('if (trace)', marker.end())
        if end < 0:
            end = code.find('printf("{', marker.end())
        snippets[marker.group(1)] = code[marker.start():end].strip()
    graph = report["graph"]
    clock = graph.get("simulation", {"clockGHz": 2.5, "cyclesPerBatch": 1})
    incoming = {n["id"]: [] for n in graph["nodes"]}
    for edge in graph["edges"]:
        incoming[edge["to"]].append(edge["from"])
    finishes, timeline, serial = {}, [], 0
    nodes = {n["id"]: n for n in graph["nodes"]}
    for nid in report["order"]:
        node = nodes[nid]
        output = report["nodes"][nid]["outputs"].get("value")
        if not output:
            continue
        isa = node["params"].get("isa", "scalar")
        width = 256 if isa in ("avx", "avx2", "fma") or node["op"] in ("f16c", "bits32") else 128 if isa in ("sse", "ssse3") or node["op"] in ("dot_abs", "dot_int16", "dot_lookup") else 32
        bits = TYPE_INFO[output["kind"]][0]
        logical_count = output["count"] * (2 if output["kind"].startswith("p4") else 1)
        batches = max(1, math.ceil(logical_count * bits / width))
        cost = batches * clock["cyclesPerBatch"]
        start = max((finishes.get(parent, 0) for parent in incoming[nid]), default=0)
        finishes[nid] = start + cost
        serial += cost
        timeline.append({"id": nid, "title": operations[node["op"]]["title"], "start": start, "finish": start + cost, "cycles": cost, "batches": batches, "width": width})
        snippet = snippets.get(nid, "")
        used = {name for name in helpers if re.search(r"\b" + re.escape(name) + r"\s*\(", snippet)}
        pending = list(used)
        while pending:
            parent = pending.pop()
            for name, body in helpers.items():
                if name not in used and re.search(r"\b" + re.escape(name) + r"\s*\(", helpers[parent]):
                    used.add(name)
                    pending.append(name)
        instructions = operations[node["op"]]["instructions"]
        name = node["op"]
        if "isa" in node["params"] and isa == "scalar":
            instructions = "Referência escalar em C" + (" · fmaf da biblioteca matemática" if name == "fma_lanes" else " · sem instrução vetorial neste bloco")
        elif name == "float":
            instructions = {"sse": "Expansão para int32 + CVTDQ2PS · quatro FP32 por operação", "avx": "Duas conversões de 128 bits + VINSERTF128 · oito FP32", "avx2": "VPMOVSXBD / VPMOVSXWD + VCVTDQ2PS · oito FP32"}.get(isa, instructions)
        elif name in ("mul", "add"):
            operation = "MULPS" if name == "mul" else "ADDPS"
            instructions = ("V" if isa == "avx" else "") + operation + (" · 256 bits" if isa == "avx" else " · 128 bits")
        elif name == "sum":
            instructions = "Função C reduce_float · ordem de soma selecionada; veja o assembly para as instruções efetivas"
        elif name == "fma_lanes":
            instructions = "Família VFMADD*PS · 256 bits · requer AVX e FMA"
        report["nodes"][nid]["implementation"] = {
            "snippet": snippet, "functions": {name: helpers[name] for name in sorted(used)},
            "instructions": instructions,
            "width": width, "batches": batches, "cycles": cost,
        }
    critical = max(finishes.values(), default=0)
    report["clock"] = {
        **clock, "criticalCycles": critical, "serialCycles": serial,
        "criticalNs": critical / clock["clockGHz"], "timeline": timeline,
        "model": "Modelo didático: custo configurado por lote de resultados; dependências respeitadas, ramos independentes sobrepostos. Não modela portas, cache, loads, loops ou a latência real de cada instrução.",
    }
    report["functions"] = helpers
    return report
