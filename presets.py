"""Exemplos dos núcleos de um bloco de 64 pesos; não são cópias do motor inteiro."""

PRESETS = [
    ("main", "main · int8 expandido + SSE", "Histórico", "Pares int4 → int8 expandido → FP32 → produtos SSE e redução em oito vias. Um grupo, com a ordem do núcleo SSE antigo."),
    ("dev_f32", "dev · int4 / FP32 escalar", "Dev", "Pesos planares compactos e 64 chamadas fmaf distribuídas em oito vias. Representa a referência FP32 usada sem AVX2/FMA."),
    ("dev_i8", "dev · int4 / int8 escalar", "Dev", "Quantização por linha, produtos inteiros em oito vias, escala, redução e correção após a escala. Representa a referência i8 da dev."),
    ("dev_avx2_f32", "dev · AVX2 + FMA / FP32", "Dev", "Mesmo fluxo FP32 com conversões AVX2 e acumulação FMA vetorial. O Xeon E5 v2 pode simular, mas não executar este circuito."),
    ("dev_avx2_i8", "dev · AVX2 / int8", "Dev", "Produto inteiro AVX2 com a finalização da referência i8. As escalas usam fmaf; esta demonstração separa explicitamente as etapas."),
    ("dev_a32", "dev · referência A32", "Dev", "Ativações quantizadas por 32 valores e dois produtos exatos. Mantém a ordem fmaf da referência. Não representa o arranjo matricial R16 de várias saídas."),
    ("sse_int4", "Proposta · int4 + SSE", "Propostas", "Extrai pesos compactos perto do cálculo e reutiliza a ordem em oito vias do caminho SSE antigo."),
    ("avx_int4", "Proposta · SSE4.1 + AVX", "Propostas", "Prepara inteiros em 128 bits, une dois grupos FP32 e multiplica oito valores com AVX; sem FMA de hardware."),
    ("ssse3_exact", "Proposta · SSSE3 / contrato i8", "Propostas", "Produtos inteiros nativos de 128 bits. Mantém fmaf, as oito vias e a correção da referência i8. Pode ser comparado bit a bit com dev/i8 para um grupo."),
    ("ssse3_fast", "Proposta · SSSE3 / correção inteira", "Propostas", "Soma e corrige os produtos em int32 antes das escalas. É exato na parte inteira, mas não garante os mesmos arredondamentos do contrato i8."),
    ("signed_abs", "Proposta · SSSE3 / sinal", "Propostas", "Valor absoluto dos pesos e ajuste do sinal da ativação. Dispensa a correção por +8 na parte inteira."),
    ("int16", "Proposta · SSE2 / int16", "Propostas", "Expande os dois operandos para int16 e usa PMADDWD. Mais preparação, sem correção de viés."),
    ("f16c", "Proposta · F16C + AVX", "Propostas", "Constrói uma representação FP16 intermediária exata dos códigos, converte oito FP32 e calcula com AVX."),
    ("bits32", "Proposta · bits FP32 + AVX", "Propostas", "Usa a representação de 8388608+código e uma subtração exata para preparar os pesos FP32."),
    ("lookup", "Experimental · tabela SSSE3", "Propostas", "Tabela de 16 somas de quatro ativações, seleção por planos de bits e PSHUFB. Didático; não é o T-MAC otimizado."),
]


def make_preset(key):
    if key not in {p[0] for p in PRESETS}: raise ValueError("Exemplo desconhecido.")
    nodes=[]; edges=[]
    def add(op, params=None, **bindings):
        nid="n"+str(len(nodes)+1)
        nodes.append(dict(id=nid,op=op,params=params or {},x=0,y=0))
        for port,src in bindings.items():
            sid,out=src if isinstance(src,tuple) else (src,"value")
            edges.append(dict(**{"from":sid},out=out,to=nid,input=port))
        return nid
    historical=key.startswith("dev") or key=="main"
    w=add("weights",{"format":"pairs" if historical else "planar"})
    x=add("activations"); scale=add("scale")
    if key=="main":
        lo=add("lowbits",{"isa":"sse"},value=w)
        hi=add("highbits",{"isa":"sse"},value=w)
        w=add("interleave",a=lo,b=hi)
        w=add("sign4",value=w)
        w=add("float",{"isa":"sse"},value=w)
        y=add("mul",{"isa":"sse"},a=w,b=x)
        y=add("sum",{"order":"main_sse"},value=y)
        y=add("fma_scale",value=y,scale=scale)
    else:
        if historical: w=add("repack",value=w)
        bits_isa="avx2" if "avx2" in key else "scalar" if key.startswith("dev") else "sse"
        lo=add("lowbits",{"isa":bits_isa},value=w)
        hi=add("highbits",{"isa":bits_isa},value=w)
        u=add("concat",a=lo,b=hi)
        if key in ("dev_f32","dev_avx2_f32"):
            w=add("center",value=u)
            w=add("float",{"isa":"avx2" if key=="dev_avx2_f32" else "scalar"},value=w)
            y=add("fma_lanes",{"isa":"fma" if key=="dev_avx2_f32" else "scalar"},weights=w,x=x)
            y=add("fma_scale",value=y,scale=scale)
            y=add("sum",{"order":"tree"},value=y)
        elif key in ("dev_i8","dev_avx2_i8","ssse3_exact","ssse3_fast"):
            q=add("quantize",{"group":"row"},value=x)
            isa={"dev_i8":"scalar","dev_avx2_i8":"avx2","ssse3_exact":"ssse3","ssse3_fast":"ssse3"}[key]
            y=add("maddubs",{"isa":isa},weights=u,x=q)
            pair_isa="avx2" if isa=="avx2" else "scalar" if isa=="scalar" else "sse"
            y=add("maddwd",{"isa":pair_isa},value=y)
            y=add("merge",{"isa":pair_isa},value=y)
            if key=="ssse3_fast":
                y=add("sum",{"order":"sequential"},value=y)
                y=add("bias_int",value=y,sum=(q,"sum"))
                y=add("float",{"isa":"scalar"},value=y)
                y=add("mul",{"isa":"scalar"},a=y,b=scale)
            else:
                y=add("float",{"isa":"avx2" if isa=="avx2" else "sse" if isa=="ssse3" else "scalar"},value=y)
                y=add("fma_scale",value=y,scale=scale)
                y=add("sum",{"order":"tree"},value=y)
                y=add("bias_float",value=y,sum=(q,"sum"),scale=scale)
            y=add("mul",{"isa":"scalar"},a=y,b=(q,"scale"))
        elif key=="dev_a32":
            w=add("center",value=u)
            q=add("quantize",{"group":"32"},value=x)
            y=add("dot_halves",weights=w,x=q)
            y=add("finish32",value=y,scale=(q,"scale"))
        elif key in ("signed_abs","int16","lookup"):
            q=add("quantize",{"group":"row"},value=x)
            if key=="lookup": y=add("dot_lookup",weights=u,x=q)
            else:
                w=add("center",value=u)
                if key=="int16":
                    w=add("int16",value=w); x16=add("int16",value=q)
                    y=add("dot_int16",weights=w,x=x16)
                else: y=add("dot_abs",weights=w,x=q)
            y=add("float",{"isa":"scalar"},value=y)
            y=add("mul",{"isa":"scalar"},a=y,b=scale)
            y=add("mul",{"isa":"scalar"},a=y,b=(q,"scale"))
        else:
            if key in ("f16c","bits32"): w=add(key,value=u)
            else:
                w=add("center",value=u)
                w=add("float",{"isa":"sse" if key=="sse_int4" else "avx"},value=w)
            y=add("mul",{"isa":"sse" if key=="sse_int4" else "avx"},a=w,b=x)
            y=add("sum",{"order":"main_sse"},value=y)
            y=add("fma_scale",value=y,scale=scale)
    add("output",value=y)
    depth={}; counts={}
    for n in nodes:
        parents=[e["from"] for e in edges if e["to"]==n["id"]]
        d=max((depth[p]+1 for p in parents),default=0)
        row=counts.get(d,0); counts[d]=row+1; depth[n["id"]]=d
        n.update(x=48+400*d,y=64+row*248)
    return dict(version=1,nodes=nodes,edges=edges,inputs=dict(
        weights=[(i%16)-8 for i in range(64)],
        activations=[127,-64,32,-16,8,-4,2,-1]*8,
        weightScale=1.0,atol=1e-5,rtol=1e-5))
