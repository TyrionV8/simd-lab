"""Circuitos tipados, simulação FP32 e geração de C para pequenos produtos escalares."""
from __future__ import annotations

import copy
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from numerics import f32, fma
from validation import REFERENCES, oracle, compare
from inspection import enrich


def op(title, group, inputs, outputs, description, instructions="", choices=None):
    return dict(title=title, group=group, inputs=inputs, outputs=outputs,
                description=description, instructions=instructions, choices=choices or {})


INTEGER = ["u8", "i8", "i16", "i32"]
OPS = {
    "weights": op("Entrada · pesos int4", "Entradas", {}, {"value": "p4"},
        "64 pesos entre −8 e 7. Em pares assinados, cada byte guarda dois vizinhos. No planar, guarda as posições k e k+32 com viés +8.", choices={"format": ["pairs", "planar"]}),
    "activations": op("Entrada · ativações FP32", "Entradas", {}, {"value": "f32"}, "Os 64 números que chegam à multiplicação da rede."),
    "scale": op("Escala dos pesos", "Entradas", {}, {"value": "f32"}, "Um FP32 para este grupo de 64 pesos. Peso efetivo = inteiro × escala."),
    "constant": op("Constante FP32", "Entradas", {}, {"value": "f32"}, "Um valor escalar; pode ser distribuído por todos os elementos de um vetor.", choices={"value": "number"}),
    "unpack": op("Expandir pares → int8", "Bits e representação", {"value": ["p4s"]}, {"value": "i8"}, "Expansão sem perda usada pelo caminho antigo. Os 32 bytes de pesos passam a 64 bytes int8.", "Máscaras, deslocamentos e extensão do sinal"),
    "repack": op("Reorganizar → planar int4", "Bits e representação", {"value": ["p4s"]}, {"value": "p4u"}, "Reorganiza os mesmos pesos: vizinhos no disco → metades planares na RAM. Continua com 32 bytes.", "Extração dos nibbles e XOR com 8"),
    "decode": op("Separar códigos 0…15", "Bits e representação", {"value": ["p4u"]}, {"value": "u8"}, "Seleciona os quatro bits inferiores e superiores. Cada código passa a ocupar um byte; ainda contém o viés +8.", "AND 0x0F · deslocamento de 4 bits"),
    "center": op("Subtrair viés 8", "Bits e representação", {"value": ["u8"]}, {"value": "i8"}, "Converte cada código 0…15 no peso inteiro −8…7, subtraindo 8.", "PSUBB ou operação escalar"),
    "int16": op("Expandir → int16", "Conversões", {"value": ["u8", "i8"]}, {"value": "i16"}, "Preserva o valor e o sinal; aumenta cada elemento para 16 bits."),
    "float": op("Converter → FP32", "Conversões", {"value": INTEGER}, {"value": "f32"}, "Cada inteiro se torna um FP32 independente. SSE: quatro por vez. AVX: dois grupos preparados em 128 bits, unidos em 256. AVX2: expansão direta de oito.", "PMOVSXBD · CVTDQ2PS · VINSERTF128", {"isa": ["scalar", "sse", "avx", "avx2"]}),
    "f16c": op("Construção FP16 → FP32", "Conversões", {"value": ["u8"]}, {"value": "f32"}, "Constrói FP16 de 1024+código, converte oito valores e subtrai 1032. Exato para os 16 códigos; FP16 é apenas intermediário.", "PMOVZXBW · POR · VCVTPH2PS · VSUBPS"),
    "bits32": op("Construção dos bits FP32", "Conversões", {"value": ["u8"]}, {"value": "f32"}, "Constrói os bits de 8388608+código e subtrai 8388616. Exato para os 16 códigos.", "PMOVZXBD · VINSERTF128 · VORPS · VSUBPS"),
    "quantize": op("Quantizar ativações → int8", "Quantização", {"value": ["f32"]}, {"value": "i8", "scale": "f32", "sum": "i32"}, "Encontra o maior valor absoluto, calcula a escala e arredonda para −127…127. A escala permite reconstruir uma aproximação da entrada.", "Máximo absoluto · divisão · arredondamento", {"group": ["row", "32"]}),
    "mul": op("Multiplicar FP32", "Aritmética", {"a": ["f32"], "b": ["f32"]}, {"value": "f32"}, "Multiplica elemento por elemento, com arredondamento FP32. Uma entrada escalar pode ser repetida no vetor.", "MULPS / VMULPS", {"isa": ["scalar", "sse", "avx"]}),
    "add": op("Somar FP32", "Aritmética", {"a": ["f32"], "b": ["f32"]}, {"value": "f32"}, "Soma elemento por elemento, com arredondamento FP32; aceita distribuição de um escalar.", "ADDPS / VADDPS", {"isa": ["scalar", "sse", "avx"]}),
    "fma_lanes": op("Acumular FMA · 8 vias", "Aritmética", {"weights": ["f32"], "x": ["f32"]}, {"value": "f32"}, "Via l recebe os elementos l, l+8, …, l+56. Cada produto e soma tem um único arredondamento. O modo escalar usa fmaf da biblioteca matemática.", "fmaf ou VFMADDPS", {"isa": ["scalar", "fma"]}),
    "fma_scale": op("Aplicar escala com FMA", "Aritmética", {"value": ["f32"], "scale": ["f32"]}, {"value": "f32"}, "Calcula fmaf(valor, escala, 0) para cada via. Este laboratório cobre um grupo; a acumulação entre vários grupos fica para um circuito maior.", "fmaf(valor, escala, 0)"),
    "dot_unsigned": op("Produto u8 × int8 · 8 vias", "Produtos inteiros", {"weights": ["u8"], "x": ["i8"]}, {"value": "i32"}, "64 produtos exatos. Via l soma as posições 4l…4l+3 e 32+4l…35+4l, como a referência i8 da dev. Códigos limitados a 0…15 evitam saturação.", "PMADDUBSW · PMADDWD · PADDD", {"isa": ["scalar", "ssse3", "avx2"]}),
    "dot_abs": op("Produto com ajuste de sinal", "Produtos inteiros", {"weights": ["i8"], "x": ["i8"]}, {"value": "i32"}, "Valor absoluto do peso e sinal transferido à ativação. Exato aqui porque as ativações estão entre −127 e 127.", "PABSB · PSIGNB · PMADDUBSW · PMADDWD"),
    "dot_int16": op("Produto int16 × int16", "Produtos inteiros", {"weights": ["i16"], "x": ["i16"]}, {"value": "i32"}, "Multiplica pares de inteiros de 16 bits e soma em 32 bits. As faixas deste laboratório não provocam transbordamento.", "PMADDWD · PADDD"),
    "dot_lookup": op("Produto por tabela · LUT", "Produtos inteiros", {"weights": ["u8"], "x": ["i8"]}, {"value": "i32"}, "Para cada quatro ativações, monta 16 somas possíveis. Os quatro planos de bits dos pesos selecionam somas usando PSHUFB. É uma demonstração, não a implementação otimizada do T-MAC.", "PSHUFB · somas · deslocamentos"),
    "dot_halves": op("Produto assinado · 2 metades", "Produtos inteiros", {"weights": ["i8"], "x": ["i8"]}, {"value": "i32"}, "Dois produtos exatos de 32 elementos. Representa os números da variante A32 da dev, por uma linha; não reorganiza 16 linhas em R16.", "Referência inteira por metade de 32"),
    "finish32": op("Finalizar A32 com FMA", "Redução e saída", {"value": ["i32"], "scale": ["f32"]}, {"value": "f32"}, "p0×escala0; depois fmaf(p1, escala1, parcial); depois fmaf(parcial, escala dos pesos, 0). Mesma ordem da referência A32.", "Duas chamadas fmaf"),
    "sum": op("Reduzir / somar vetor", "Redução e saída", {"value": INTEGER + ["f32"]}, {"value": "number"}, "Inteiros são somados exatamente. Em FP32, escolha a ordem: sequencial, oito vias da main/SSE ou árvore. A ordem altera arredondamentos.", "ADDPS · permutações · soma horizontal", {"order": ["sequential", "main_sse", "tree"]}),
    "bias_int": op("Corrigir viés em int32", "Redução e saída", {"value": ["i32"], "sum": ["i32"]}, {"value": "i32"}, "Produto sem sinal − 8 × soma das ativações. A correção inteira é exata."),
    "bias_float": op("Corrigir viés após a escala", "Redução e saída", {"value": ["f32"], "sum": ["i32"], "scale": ["f32"]}, {"value": "f32"}, "Calcula saída − 8×fmaf(escala dos pesos, soma das ativações, 0), como o caminho i8 de uma linha na dev."),
    "output": op("Saída · validar resultado", "Redução e saída", {"value": ["f32"]}, {"value": "f32"}, "Compare um escalar com a soma dos produtos das entradas originais. A tolerância não esconde erro de quantização: a diferença é mostrada."),
}
OPS.update({
    "lowbits": op("Selecionar 4 bits inferiores", "Bits e representação", {"value":["p4s","p4u"]}, {"value":"u8"}, "Uma máscara 00001111 seleciona o código inferior de cada byte. Produz 32 códigos em bytes separados.", "PAND / VPAND · máscara 0x0F", {"isa":["scalar","sse","avx2"]}),
    "highbits": op("Extrair 4 bits superiores", "Bits e representação", {"value":["p4s","p4u"]}, {"value":"u8"}, "Desloca e aplica a máscara 00001111 para extrair o código superior de cada byte.", "PSRLW / VPSRLW · PAND / VPAND", {"isa":["scalar","sse","avx2"]}),
    "concat": op("Concatenar duas metades", "Bits e representação", {"a":["u8"],"b":["u8"]}, {"value":"u8"}, "A metade A vem inteira antes da B. Recupera a ordem dos 64 códigos do formato planar.", "Cópia das duas metades, sem aritmética"),
    "interleave": op("Intercalar códigos vizinhos", "Bits e representação", {"a":["u8"],"b":["u8"]}, {"value":"u8"}, "Produz A0,B0,A1,B1,…; recupera a ordem dos códigos no formato de pares assinados."),
    "sign4": op("Estender sinal de 4 bits", "Bits e representação", {"value":["u8"]}, {"value":"i8"}, "No complemento de dois, códigos 8…15 representam −8…−1. Subtrai 16 somente nesses códigos; não é a subtração de viés do formato planar."),
    "maddubs": op("PMADDUBSW · pares int16", "Produtos inteiros", {"weights":["u8"],"x":["i8"]}, {"value":"i16"}, "64 produtos u8×int8 viram 32 somas de pares int16. Com pesos 0…15 e ativações ±127, o limite por par é 3810: a saturação não ocorre.", "PMADDUBSW / VPMADDUBSW", {"isa":["scalar","ssse3","avx2"]}),
    "maddwd": op("PMADDWD · grupos int32", "Produtos inteiros", {"value":["i16"]}, {"value":"i32"}, "Multiplica as 32 somas int16 por 1 e combina pares, produzindo 16 somas int32; cada uma representa quatro produtos originais.", "PMADDWD / VPMADDWD · multiplicadores iguais a 1", {"isa":["scalar","sse","avx2"]}),
    "merge": op("Somar as duas metades", "Produtos inteiros", {"value":["i32"]}, {"value":"i32"}, "Soma os elementos k e k+8 do vetor de 16 somas. Produz as oito vias da referência i8.", "PADDD / VPADDD", {"isa":["scalar","sse","avx2"]}),
})

ISA = {
    "scalar": [], "sse": ["sse4_1"], "avx": ["avx", "sse4_1"],
    "avx2": ["avx2"], "ssse3": ["ssse3"], "fma": ["avx", "fma"],
}
CTYPE = {"p4s": "uint8_t", "p4u": "uint8_t", "u8": "uint8_t", "i8": "int8_t", "i16": "int16_t", "i32": "int32_t", "f32": "float"}
KINDS = {"p4s": 0, "p4u": 0, "u8": 0, "i8": 1, "i16": 2, "i32": 3}


@dataclass
class Value:
    kind: str
    values: list
    origins: list | None = None

    def public(self):
        size = {"p4s": 1, "p4u": 1, "u8": 1, "i8": 1, "i16": 2, "i32": 4, "f32": 4}[self.kind]
        fmt = {"p4s": "B", "p4u": "B", "u8": "B", "i8": "b", "i16": "h", "i32": "i", "f32": "f"}[self.kind]
        raw = [struct.pack("<"+fmt, x) for x in self.values]
        return dict(kind=self.kind, values=self.values, count=len(self.values), bytes=size*len(self.values),
                    bits=[format(int.from_bytes(b,"little"), f"0{size*8}b") for b in raw],
                    hex=[b.hex(" ") for b in raw], elementBytes=size, origins=self.origins,
                    decimal=[format(x,".9g") if self.kind=="f32" else str(x) for x in self.values])


def pack(weights, layout):
    if layout == "pairs":
        return [(weights[i]&15) | ((weights[i+1]&15)<<4) for i in range(0,64,2)]
    return [(weights[i]+8) | ((weights[i+32]+8)<<4) for i in range(32)]


def reduce_fp(values, order):
    if order == "sequential":
        out = 0.0
        for x in values: out = f32(out+x)
        return out
    if order == "main_sse":
        lanes = [0.0]*8
        for i,x in enumerate(values): lanes[i&7] = f32(lanes[i&7]+x)
        a = [f32(lanes[i]+lanes[i+4]) for i in range(4)]
        return f32(f32(a[0]+a[2])+f32(a[1]+a[3]))
    values = list(values)
    step = len(values)//2
    while step:
        values[:step] = [f32(values[i]+values[i+step]) for i in range(step)]
        step //= 2
    return values[0]


def quantize(values, group):
    q, scales, sums = [], [], []
    for start in range(0,len(values),group):
        block = values[start:start+group]
        maximum = max(map(abs,block))
        scale = f32(maximum/127) if maximum else (1.0 if group==64 else 0.0)
        if maximum and scale==0:
            raise ValueError("A escala de quantização ficou menor que o FP32 consegue representar. Use ativações de maior magnitude.")
        inverse = (f32(1/scale) if group==64 else f32(127/maximum)) if maximum else 0.0
        codes = [max(-127,min(127,round(f32(x*inverse)))) for x in block]
        q.extend(codes); scales.append(scale); sums.append(sum(codes))
    return {"value": Value("i8",q), "scale": Value("f32",scales), "sum": Value("i32",sums)}


def evaluate(opname, p, args, inputs):
    get = lambda key: args[key].values
    val = get("value") if "value" in args else []
    out = lambda kind,data: {"value": Value(kind,list(data))}
    if opname == "weights": return out("p4s" if p["format"]=="pairs" else "p4u", pack(inputs["weights"],p["format"]))
    if opname == "activations": return out("f32",inputs["activations"])
    if opname == "scale": return out("f32",[inputs["weightScale"]])
    if opname == "constant": return out("f32",[f32(p["value"])])
    if opname in ("unpack","repack"):
        weights = []
        for b in val:
            for n in (b&15,b>>4): weights.append(n-16 if n>=8 else n)
        return out("i8",weights) if opname=="unpack" else out("p4u",pack(weights,"planar"))
    if opname=="decode": return out("u8",[b&15 for b in val]+[b>>4 for b in val])
    if opname in ("lowbits","highbits"):
        return out("u8",[(b&15) if opname=="lowbits" else (b>>4) for b in val])
    if opname in ("concat","interleave"):
        a,b=get("a"),get("b")
        if len(a)!=32 or len(b)!=32: raise ValueError("Conecte duas metades de 32 códigos.")
        return out("u8",a+b if opname=="concat" else [v for pair in zip(a,b) for v in pair])
    if opname=="sign4":
        if any(not 0<=v<=15 for v in val): raise ValueError("A extensão de sinal exige códigos de 4 bits.")
        return out("i8",[v-16 if v>=8 else v for v in val])
    if opname=="center":
        if any(not 0<=v<=15 for v in val): raise ValueError("A entrada deve conter códigos de 0 a 15.")
        return out("i8",[x-8 for x in val])
    if opname=="int16": return out("i16",val)
    if opname=="float": return out("f32",map(f32,val))
    if opname in ("f16c","bits32"):
        if any(not 0<=v<=15 for v in val): raise ValueError("A construção aceita somente códigos de 0 a 15.")
        return out("f32",[float(x-8) for x in val])
    if opname=="quantize":
        if len(val)!=64: raise ValueError("Quantização: conecte as 64 ativações.")
        return quantize(val,64 if p["group"]=="row" else 32)
    if opname in ("mul","add"):
        a,b = get("a"),get("b"); n=max(len(a),len(b))
        if len(a) not in (1,n) or len(b) not in (1,n): raise ValueError("Os vetores devem ter o mesmo tamanho, ou uma entrada deve ser escalar.")
        return out("f32",[f32(a[0 if len(a)==1 else i]*b[0 if len(b)==1 else i] if opname=="mul" else a[0 if len(a)==1 else i]+b[0 if len(b)==1 else i]) for i in range(n)])
    if opname=="fma_lanes":
        w,x=get("weights"),get("x")
        if len(w)!=64 or len(x)!=64: raise ValueError("A acumulação por vias recebe dois vetores de 64 valores.")
        lanes=[0.0]*8
        for i in range(64): lanes[i&7]=fma(w[i],x[i],lanes[i&7])
        return out("f32",lanes)
    if opname=="fma_scale":
        scale=get("scale")
        if len(scale)!=1: raise ValueError("Conecte uma única escala dos pesos.")
        return out("f32",[fma(x,scale[0]) for x in val])
    if opname=="maddubs":
        w,x=get("weights"),get("x")
        if len(w)!=64 or len(x)!=64 or any(not 0<=v<=15 for v in w) or any(not -127<=v<=127 for v in x): raise ValueError("PMADDUBSW exige 64 códigos 0…15 e 64 ativações −127…127.")
        return out("i16",[w[i]*x[i]+w[i+1]*x[i+1] for i in range(0,64,2)])
    if opname=="maddwd":
        if len(val)!=32: raise ValueError("Conecte as 32 somas de pares int16.")
        return out("i32",[val[i]+val[i+1] for i in range(0,32,2)])
    if opname=="merge":
        if len(val)!=16: raise ValueError("Conecte as 16 somas em int32.")
        return out("i32",[val[i]+val[i+8] for i in range(8)])
    if opname.startswith("dot_"):
        w,x=get("weights"),get("x")
        if len(w)!=64 or len(x)!=64: raise ValueError("Este produto recebe dois vetores de 64 valores.")
        if any(not -127<=v<=127 for v in x): raise ValueError("As ativações inteiras devem estar entre −127 e 127.")
        if opname in ("dot_unsigned","dot_lookup") and any(not 0<=v<=15 for v in w): raise ValueError("Os códigos dos pesos devem estar entre 0 e 15.")
        if opname=="dot_unsigned": return out("i32",[sum(w[k]*x[k]+w[k+32]*x[k+32] for k in range(4*l,4*l+4)) for l in range(8)])
        if opname=="dot_halves": return out("i32",[sum(w[k]*x[k] for k in range(start,start+32)) for start in (0,32)])
        if opname=="dot_lookup": return out("i32",[sum((a-8)*b for a,b in zip(w,x))])
        return out("i32",[sum(a*b for a,b in zip(w,x))])
    if opname=="finish32":
        scales=get("scale")
        if len(val)!=2 or len(scales)!=2: raise ValueError("A32 exige dois produtos e duas escalas de ativação.")
        return out("f32",[fma(fma(float(val[1]),scales[1],f32(val[0]*scales[0])),inputs["weightScale"])])
    if opname=="sum":
        if args["value"].kind=="f32":
            if len(val) & (len(val)-1): raise ValueError("A árvore exige tamanho potência de dois.")
            return out("f32",[reduce_fp(val,p["order"])])
        return out("i32",[sum(val)])
    if opname in ("bias_int","bias_float"):
        sums=get("sum")
        if len(val)!=1 or len(sums)!=1: raise ValueError("A correção recebe um produto escalar e uma soma escalar (quantização por linha).")
        if opname=="bias_int": return out("i32",[val[0]-8*sums[0]])
        scales=get("scale")
        if len(scales)!=1: raise ValueError("Conecte uma escala escalar.")
        return out("f32",[f32(val[0]-f32(8*fma(scales[0],float(sums[0]))))])
    if opname=="output":
        if len(val)!=1: raise ValueError("A saída deve ser escalar. Falta reduzir o vetor?")
        return out("f32",val)
    raise ValueError("Operação desconhecida.")


def clean_graph(raw):
    if not isinstance(raw,dict): raise ValueError("O circuito deve ser um objeto JSON.")
    raw=copy.deepcopy(raw)
    nodes=raw.get("nodes",[]); edges=raw.get("edges",[])
    if not isinstance(nodes,list) or not isinstance(edges,list) or len(nodes)>80 or len(edges)>200: raise ValueError("Limite: 80 blocos e 200 conexões.")
    seen=set()
    for n in nodes:
        if not isinstance(n,dict) or not isinstance(n.get("id"),str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,31}",n["id"]): raise ValueError("Identificador de bloco inválido.")
        if n["id"] in seen or n.get("op") not in OPS: raise ValueError("Bloco duplicado ou operação desconhecida.")
        seen.add(n["id"])
        p=n.get("params",{})
        if not isinstance(p,dict): raise ValueError("Parâmetros inválidos.")
        n["params"]={}
        for key,choices in OPS[n["op"]]["choices"].items():
            value=p.get(key,0 if choices=="number" else choices[0])
            if choices=="number":
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or abs(value)>1e6: raise ValueError("Constante fora da faixa ±1.000.000.")
            elif value not in choices: raise ValueError("Parâmetro de bloco inválido.")
            n["params"][key]=value
        for axis in ("x","y"):
            v=n.get(axis,0)
            n[axis]=max(0,min(20000,float(v))) if isinstance(v,(int,float)) and math.isfinite(v) else 0
    incoming={}
    for e in edges:
        if not isinstance(e,dict) or e.get("from") not in seen or e.get("to") not in seen: raise ValueError("Conexão aponta para um bloco inexistente.")
        src=next(n for n in nodes if n["id"]==e["from"]); dst=next(n for n in nodes if n["id"]==e["to"])
        if e.get("out") not in OPS[src["op"]]["outputs"] or e.get("input") not in OPS[dst["op"]]["inputs"]: raise ValueError("Porta inexistente.")
        key=(e["to"],e["input"])
        if key in incoming: raise ValueError("Uma entrada recebe no máximo uma conexão.")
        incoming[key]=e
    inp=raw.get("inputs",{})
    w=inp.get("weights",[]); x=inp.get("activations",[])
    if not isinstance(w,list) or len(w)!=64 or any(type(v)!=int or not -8<=v<=7 for v in w): raise ValueError("Forneça exatamente 64 pesos inteiros entre −8 e 7.")
    if not isinstance(x,list) or len(x)!=64 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1e6 for v in x): raise ValueError("Forneça 64 ativações finitas na faixa ±1.000.000.")
    inputs={"weights":w,"activations":[f32(v) for v in x]}
    for key,default,limit in (("weightScale",1.0,1e6),("atol",1e-5,1e6),("rtol",1e-5,1.0)):
        v=inp.get(key,default)
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>limit or (key!="weightScale" and v<0): raise ValueError("Escala ou tolerância inválida.")
        inputs[key]=f32(v) if key=="weightScale" else float(v)
    inputs["referenceMode"]=inp.get("referenceMode","mathematical")
    inputs["validationPolicy"]=inp.get("validationPolicy","tolerance")
    if inputs["referenceMode"] not in REFERENCES: raise ValueError("Gabarito desconhecido.")
    if inputs["validationPolicy"] not in ("bits","tolerance"): raise ValueError("Política de validação desconhecida.")
    simulation=raw.get("simulation",{})
    if not isinstance(simulation,dict): raise ValueError("Modelo de clock inválido.")
    ghz=simulation.get("clockGHz",2.5); cost=simulation.get("cyclesPerBatch",1)
    if isinstance(ghz,bool) or not isinstance(ghz,(float,int)) or not math.isfinite(ghz) or not .1<=ghz<=6: raise ValueError("O clock didático deve estar entre 0,1 e 6 GHz.")
    if type(cost)!=int or not 1<=cost<=1000: raise ValueError("O custo didático deve estar entre 1 e 1000 ciclos por lote.")
    return dict(version=2,nodes=nodes,edges=edges,inputs=inputs,simulation={"clockGHz":ghz,"cyclesPerBatch":cost})


def required_features(node):
    name=node["op"]; p=node["params"]
    if name in ("float","mul","add","fma_lanes","dot_unsigned","lowbits","highbits","maddubs","maddwd","merge"):
        if p["isa"]=="sse" and name!="float": return ["sse2"]
        if p["isa"]=="avx" and name!="float": return ["avx"]
        return ISA[p["isa"]]
    if name=="f16c": return ["sse4_1","avx","f16c"]
    if name=="bits32": return ["sse4_1","avx"]
    if name in ("dot_abs","dot_lookup"): return ["ssse3"]
    if name=="dot_int16": return ["sse2"]
    return []


def add_origins(node,args,outputs):
    """Correspondência de posições e parcelas; não inclui dependências indiretas por escalas."""
    name=node["op"]
    def origins(port):
        value=args[port]
        return value.origins or [[] for _ in value.values]
    def union(groups): return sorted({i for group in groups for i in group})
    for value in outputs.values(): value.origins=[[] for _ in value.values]
    result=outputs["value"]
    if name=="weights":
        result.origins=[[2*i,2*i+1] if node["params"]["format"]=="pairs" else [i,i+32] for i in range(32)]
    elif name=="activations": result.origins=[[i] for i in range(64)]
    elif name=="repack": result.origins=[[i,i+32] for i in range(32)]
    elif name in ("unpack","decode"): result.origins=[[i] for i in range(64)]
    elif name in ("lowbits","highbits"):
        side=0 if name=="lowbits" else 1
        result.origins=[[group[side]] for group in origins("value")]
    elif name in ("concat","interleave"):
        a,b=origins("a"),origins("b")
        result.origins=a+b if name=="concat" else [group for pair in zip(a,b) for group in pair]
    elif name in ("mul","add"):
        a,b=origins("a"),origins("b")
        result.origins=[union([a[0 if len(a)==1 else i],b[0 if len(b)==1 else i]]) for i in range(len(result.values))]
    elif name=="quantize":
        result.origins=origins("value")
        group=64 if node["params"]["group"]=="row" else 32
        outputs["sum"].origins=[union(result.origins[i:i+group]) for i in range(0,64,group)]
    elif name in ("dot_unsigned","maddubs","dot_abs","dot_int16","dot_lookup","dot_halves"):
        both=[union(pair) for pair in zip(origins("weights"),origins("x"))]
        if name=="dot_unsigned": result.origins=[union(both[4*i:4*i+4]+both[32+4*i:36+4*i]) for i in range(8)]
        elif name=="maddubs": result.origins=[union(both[i:i+2]) for i in range(0,64,2)]
        elif name=="dot_halves": result.origins=[union(both[:32]),union(both[32:])]
        else: result.origins=[union(both)]
    elif name=="fma_lanes":
        w,x=origins("weights"),origins("x")
        result.origins=[union([group for i in range(l,64,8) for group in (w[i],x[i])]) for l in range(8)]
    elif name=="maddwd":
        source=origins("value");result.origins=[union(source[i:i+2]) for i in range(0,32,2)]
    elif name=="merge":
        source=origins("value");result.origins=[union([source[i],source[i+8]]) for i in range(8)]
    elif name in ("sum","finish32"): result.origins=[union(origins("value"))]
    elif "value" in args: result.origins=origins("value")


def analyze(raw, cpu, include_code=True):
    graph=clean_graph(raw); nodes={n["id"]:n for n in graph["nodes"]}
    incoming={(e["to"],e["input"]):e for e in graph["edges"]}
    values={}; errors={}; order=[]; visiting=set(); dependencies={}
    def visit(nid):
        if nid in visiting: raise ValueError("Ciclo detectado: o circuito precisa seguir da entrada até a saída.")
        if nid in values: return values[nid]
        if nid in errors: raise ValueError(errors[nid])
        visiting.add(nid); n=nodes[nid]; args={}; deps=set()
        try:
            for port,types in OPS[n["op"]]["inputs"].items():
                edge=incoming.get((nid,port))
                if not edge: raise ValueError("Conecte a entrada “"+port+"”.")
                result=visit(edge["from"])[edge["out"]]
                if result.kind not in types: raise ValueError("Entrada “"+port+"”: esperado "+" / ".join(types)+"; recebido "+result.kind+".")
                args[port]=result; deps.add(edge["from"]); deps.update(dependencies[edge["from"]])
            output=evaluate(n["op"],n["params"],args,graph["inputs"])
            if any(not math.isfinite(v) for a in output.values() for v in a.values): raise ValueError("A operação produziu um valor não finito.")
            for value in output.values():
                if value.kind in ("i8","i16","i32"):
                    bits={"i8":8,"i16":16,"i32":32}[value.kind]
                    if any(not -(1<<(bits-1))<=v<(1<<(bits-1)) for v in value.values): raise ValueError("Transbordamento: o resultado não cabe em "+value.kind+".")
            add_origins(n,args,output)
            values[nid]=output; dependencies[nid]=deps; order.append(nid)
            return output
        except (ValueError,OverflowError,struct.error) as exc:
            errors[nid]=str(exc); raise ValueError(str(exc)) from exc
        finally: visiting.discard(nid)
    for nid in nodes:
        try: visit(nid)
        except ValueError: pass
    outputs=[n["id"] for n in graph["nodes"] if n["op"]=="output"]
    valid=len(outputs)==1 and outputs[0] in values
    reachable=dependencies[outputs[0]]|{outputs[0]} if valid else set()
    active_order=[i for i in order if i in reachable]
    features=sorted({f for i in active_order for f in required_features(nodes[i])})
    missing=[f for f in features if f not in cpu["flags"]]
    inputs=graph["inputs"]
    answer=oracle(inputs)
    reference=answer["expected"]
    actual=values[outputs[0]]["value"].values[0] if valid else None
    delta=abs(actual-reference) if valid else None
    tolerance=inputs["atol"]+inputs["rtol"]*abs(reference)
    comparison=compare(actual,reference,inputs) if valid else None
    result=dict(graph=graph,nodes={nid:dict(outputs={k:v.public() for k,v in values.get(nid,{}).items()},error=errors.get(nid),active=nid in reachable) for nid in nodes},
        order=active_order or order,valid=valid,required=features,missing=missing,reference=reference,actual=actual,delta=delta,
        tolerance=tolerance,passed=bool(comparison and comparison["passed"]),
        message="Circuito pronto." if valid else ("Mantenha exatamente um bloco de saída." if len(outputs)!=1 else errors.get(outputs[0],"Conecte a saída.")))
    result["oracle"]=answer
    result["comparison"]=comparison
    result["issues"]=[{"node":nid,"message":error} for nid,error in errors.items()]
    result["unused"]=[nid for nid in nodes if nid not in reachable]
    result["quantization"]=[]
    for nid,node in nodes.items():
        if node["op"]!="quantize" or nid not in values: continue
        edge=incoming[(nid,"value")]
        original=values[edge["from"]][edge["out"]].values
        q=values[nid]["value"].values; scales=values[nid]["scale"].values
        group=64 if node["params"]["group"]=="row" else 32
        rebuilt=[f32(value*scales[i//group]) for i,value in enumerate(q)]
        differences=[abs(a-b) for a,b in zip(original,rebuilt)]
        result["quantization"].append({"id":nid,"reconstructed":rebuilt,"original":original,"maxError":max(differences),"rmsError":math.sqrt(sum(d*d for d in differences)/64)})
    if valid and include_code: result["code"]=generate_code(graph,values,active_order,incoming)
    else: result["code"]="/* Conecte um circuito válido a uma única saída para gerar o programa C. */\n"
    return enrich(result,OPS) if include_code else result


def cfloat(value):
    text=format(f32(value),".9g")
    if "." not in text and "e" not in text: text+=".0"
    return text+"f"


def generate_code(graph, values, order, incoming):
    lines=["#define _GNU_SOURCE", "/* Gerado pelo SIMD Lab. Um produto escalar, um grupo de 64 pesos.",
           " * Compilar: gcc -std=c11 -O2 -ffp-contract=off -fno-tree-vectorize circuito.c -lm -o circuito",
           " * Funções target(...) exigem as extensões indicadas antes de serem executadas.",
           " * Verifique a CPU antes de executar em outro computador. */",Path(__file__).with_name("native.h").read_text(),
           "\n/* ===== CIRCUITO: cada trecho corresponde a um bloco visual ===== */",
           "static __attribute__((noinline, noipa)) float circuit(int trace, const int8_t *input_weights, const float *input_activations, float input_scale) {"]
    nodes={n["id"]:n for n in graph["nodes"]}; inputs=graph["inputs"]
    for nid in order:
        n=nodes[nid]; opname=n["op"]; p=n["params"]; outputs=values[nid]
        arg={port: e["from"]+"_"+e["out"] for (dest,port),e in incoming.items() if dest==nid}
        source={port:values[e["from"]][e["out"]] for (dest,port),e in incoming.items() if dest==nid}
        y=nid+"_value"; v=arg.get("value"); size=len(outputs["value"].values)
        lines.append("\n    /* "+nid+" · "+OPS[opname]["title"]+" */")
        for port,data in outputs.items():
            name=nid+"_"+port
            literal=opname == "constant"
            init="{"+", ".join(cfloat(x) if data.kind=="f32" else str(x) for x in data.values)+"}" if literal else "{0}"
            lines.append(f"    {CTYPE[data.kind]} {name}[{len(data.values)}] = {init};")
        a=lambda port:arg[port]
        if opname=="weights":
            if p["format"]=="pairs": lines.append(f"    for(int i=0;i<32;i++){y}[i]=(uint8_t)((input_weights[2*i]&15)|((input_weights[2*i+1]&15)<<4));")
            else: lines.append(f"    for(int i=0;i<32;i++){y}[i]=(uint8_t)((input_weights[i]+8)|((input_weights[i+32]+8)<<4));")
        elif opname=="activations": lines.append(f"    memcpy({y},input_activations,64*sizeof(float));")
        elif opname=="scale": lines.append(f"    {y}[0]=input_scale;")
        elif opname=="unpack":
            lines.append(f"    for(int i=0;i<64;i++){{int code=({v}[i/2]>>((i&1)*4))&15; {y}[i]=(int8_t)(code>=8?code-16:code);}}")
        elif opname=="repack":
            lines += [f"    for(int i=0;i<32;i++){{int lo=({v}[i/2]>>((i&1)*4))&15; int hi=({v}[(i+32)/2]>>(((i+32)&1)*4))&15; {y}[i]=(uint8_t)((lo^8)|((hi^8)<<4));}}"]
        elif opname=="decode": lines.append(f"    for(int i=0;i<32;i++){{{y}[i]={v}[i]&15;{y}[i+32]={v}[i]>>4;}}")
        elif opname in ("lowbits","highbits"):
            high=1 if opname=="highbits" else 0
            if p["isa"]=="scalar": lines.append(f"    for(int i=0;i<32;i++){y}[i]=({v}[i]>>{4 if high else 0})&15;")
            else: lines.append(f"    split_{p['isa']}({v},{y},{high});")
        elif opname=="concat": lines.append(f"    memcpy({y},{a('a')},32);memcpy({y}+32,{a('b')},32);")
        elif opname=="interleave": lines.append(f"    for(int i=0;i<32;i++){{{y}[2*i]={a('a')}[i];{y}[2*i+1]={a('b')}[i];}}")
        elif opname=="sign4": lines.append(f"    for(int i=0;i<{size};i++){y}[i]=(int8_t)({v}[i]>=8?{v}[i]-16:{v}[i]);")
        elif opname=="center": lines.append(f"    for(int i=0;i<{size};i++){y}[i]=(int8_t)({v}[i]-8);")
        elif opname=="int16": lines.append(f"    for(int i=0;i<{size};i++){y}[i]=(int16_t){v}[i];")
        elif opname=="float":
            if p["isa"]=="scalar": lines.append(f"    for(int i=0;i<{size};i++){y}[i]=(float){v}[i];")
            else: lines.append(f"    to_float_{p['isa']}({v},{KINDS[source['value'].kind]},{y},{size});")
        elif opname in ("f16c","bits32"): lines.append(f"    decode_{opname}({v},{y},{size});")
        elif opname=="quantize": lines.append(f"    quantize({v},{y},{nid}_scale,{nid}_sum,64,{64 if p['group']=='row' else 32});")
        elif opname in ("mul","add"): lines.append(f"    binary_{p['isa']}({a('a')},{len(source['a'].values)},{a('b')},{len(source['b'].values)},{y},{size},{1 if opname=='add' else 0});")
        elif opname=="fma_lanes": lines.append(f"    fma_lanes_{'avx' if p['isa']=='fma' else 'scalar'}({a('weights')},{a('x')},{y},64);")
        elif opname=="fma_scale": lines.append(f"    for(int i=0;i<{size};i++){y}[i]=fmaf({v}[i],{a('scale')}[0],0.f);")
        elif opname=="maddubs":
            if p["isa"]=="scalar": lines.append(f"    for(int i=0;i<32;i++){y}[i]=(int16_t)((int){a('weights')}[2*i]*{a('x')}[2*i]+(int){a('weights')}[2*i+1]*{a('x')}[2*i+1]);")
            else: lines.append(f"    maddubs_{p['isa']}({a('weights')},{a('x')},{y});")
        elif opname=="maddwd":
            if p["isa"]=="scalar": lines.append(f"    for(int i=0;i<16;i++){y}[i]=(int32_t){v}[2*i]+{v}[2*i+1];")
            else: lines.append(f"    maddwd_{p['isa']}({v},{y});")
        elif opname=="merge":
            if p["isa"]=="scalar": lines.append(f"    for(int i=0;i<8;i++){y}[i]={v}[i]+{v}[i+8];")
            else: lines.append(f"    merge_{p['isa']}({v},{y});")
        elif opname=="dot_unsigned":
            if p["isa"]=="scalar": lines.append(f"    for(int k=0;k<32;k++){{{y}[k/4]+=(int32_t){a('weights')}[k]*{a('x')}[k];{y}[k/4]+=(int32_t){a('weights')}[k+32]*{a('x')}[k+32];}}")
            else: lines.append(f"    dot_unsigned_{p['isa']}({a('weights')},{a('x')},{y});")
        elif opname in ("dot_abs","dot_int16","dot_lookup"):
            function={"dot_abs":"dot_signed_abs","dot_int16":"dot_signed16","dot_lookup":"dot_lookup"}[opname]
            lines.append(f"    {y}[0]={function}({a('weights')},{a('x')},64);")
        elif opname=="dot_halves": lines.append(f"    for(int i=0;i<64;i++){y}[i/32]+=(int32_t){a('weights')}[i]*{a('x')}[i];")
        elif opname=="finish32": lines.append(f"    {y}[0]=fmaf(fmaf((float){v}[1],{a('scale')}[1],(float){v}[0]*{a('scale')}[0]),input_scale,0.f);")
        elif opname=="sum":
            count=len(source["value"].values)
            if source["value"].kind=="f32": lines.append(f"    {y}[0]=reduce_float({v},{count},{['sequential','main_sse','tree'].index(p['order'])});")
            else: lines.append(f"    for(int i=0;i<{count};i++){y}[0]+={v}[i];")
        elif opname=="bias_int": lines.append(f"    {y}[0]={v}[0]-8*{a('sum')}[0];")
        elif opname=="bias_float": lines.append(f"    {y}[0]={v}[0]-8.f*fmaf({a('scale')}[0],(float){a('sum')}[0],0.f);")
        elif opname=="output": lines.append(f"    {y}[0]={v}[0];")
    lines.append('\n    if (trace) {\n    printf("{\\"values\\":{");')
    first=True
    for nid in order:
        for port,data in values[nid].items():
            key=nid+"."+port; name=nid+"_"+port
            prefix="" if first else ","; first=False
            lines.append('    printf("'+prefix+'\\"'+key+'\\":");')
            lines.append(f"    print_f32({name},{len(data.values)});" if data.kind=="f32" else f"    print_integer({name},{KINDS[data.kind]},{len(data.values)});")
    output=next(nid for nid in order if nodes[nid]["op"]=="output")
    lines.extend(['    printf("}}\\n");',"    }",f"    return {output}_value[0];","}"])
    lines.append(Path(__file__).with_name("benchmark.h").read_text())
    lines.extend([
        "\nint main(int argc, char **argv) {",
        "    int8_t weights[64] = {"+", ".join(map(str,inputs["weights"]))+"};",
        "    float activations[64] = {"+", ".join(map(cfloat,inputs["activations"]))+"};",
        f"    float scale = {cfloat(inputs['weightScale'])};",
        '    if (argc > 1 && strcmp(argv[1], "--stdin") == 0) {',
        "        if (fread(weights, 1, 64, stdin) != 64 || fread(activations, sizeof(float), 64, stdin) != 64 || fread(&scale, sizeof(float), 1, stdin) != 1) return 2;",
        "        circuit(1, weights, activations, scale);",
        "        return 0;",
        "    }",
        "    circuit(1, weights, activations, scale);",
        "    if (argc == 5) {",
        "        int repetitions = atoi(argv[1]), samples = atoi(argv[2]), core = atoi(argv[3]), warmup = atoi(argv[4]);",
        "        if (repetitions < 100 || repetitions > 100000 || samples < 3 || samples > 11 || warmup < 0 || warmup > 10000 || core < -1 || core >= CPU_SETSIZE) return 3;",
        "        lab_benchmark(repetitions, samples, warmup, core, weights, activations, scale);",
        "    }",
        "    return 0;", "}",
    ])
    return "\n".join(lines)+"\n"
