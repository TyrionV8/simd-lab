export const $ = id => document.getElementById(id);
export const escapeHtml = value => String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
export const labels = {
  value:"valores", a:"entrada A", b:"entrada B", weights:"pesos", x:"ativações", scale:"escala", sum:"soma",
  scalar:"Escalar", sse:"SSE / 128 bits", avx:"AVX / 256 bits", avx2:"AVX2 / 256 bits", ssse3:"SSSE3 / 128 bits", fma:"AVX + FMA",
  row:"Por linha de 64", "32":"Por grupo de 32", pairs:"Pares assinados", planar:"Planar com viés +8",
  sequential:"Sequencial", main_sse:"Oito vias da main/SSE", tree:"Árvore da dev", isa:"Instruções", format:"Formato",
  group:"Escala das ativações", order:"Ordem das somas"
};
export const types = {
  p4s:{bits:4,name:"int4 / pares",family:"inteiro com sinal",packed:true},
  p4u:{bits:4,name:"int4 / planar",family:"inteiro codificado com viés +8",packed:true},
  u8:{bits:8,name:"uint8",family:"inteiro sem sinal"},
  i8:{bits:8,name:"int8",family:"inteiro com sinal"},
  i16:{bits:16,name:"int16",family:"inteiro com sinal"},
  i32:{bits:32,name:"int32",family:"inteiro com sinal"},
  f32:{bits:32,name:"FP32",family:"floating point · ponto flutuante"}
};

export function number(value) {
  if (value === null || value === undefined) return "—";
  return Object.is(value,-0) ? "−0" : String(value);
}

export function metric(value,unit="") {
  if(value===null||value===undefined)return "Indisponível";
  const text=new Intl.NumberFormat("pt-BR",{maximumFractionDigits:3,maximumSignificantDigits:7}).format(value);
  return text+(unit?" "+unit:"");
}

export function serialize(value,space) {
  const marker='\u0000SIMD_NEGATIVE_ZERO\u0000';
  return JSON.stringify(value,(_,item)=>Object.is(item,-0)?marker:item,space).replaceAll('"\\u0000SIMD_NEGATIVE_ZERO\\u0000"','-0.0');
}

export function exactFloat(value) {
  const view=new DataView(new ArrayBuffer(4));view.setFloat32(0,value,true);const raw=view.getUint32(0,true);
  const sign=raw>>>31?'-':'',exponent=(raw>>>23)&255,fraction=raw&0x7fffff;
  if(exponent===255)return sign+(fraction?'NaN':'Infinity');
  const mantissa=BigInt(exponent?fraction+0x800000:fraction),power=exponent?exponent-150:-149;
  if(!mantissa)return sign+'0';
  if(power>=0)return sign+String(mantissa*(2n**BigInt(power)));
  const places=-power,digits=String(mantissa*(5n**BigInt(places))).padStart(places+1,'0');
  return sign+(digits.substring(0,digits.length-places)+'.'+digits.substring(digits.length-places)).replace(/0+$/,'').replace(/\.$/,'');
}

export function descriptor(value,bits=128) {
  const type=types[value.kind],count=type.packed?value.count*2:value.count;
  return {...type,count,lanes:Math.max(1,bits/type.bits),groups:Math.ceil(count*type.bits/bits),bytes:value.bytes,
    summary:`${count} números × ${type.bits} bits · ${type.family}`};
}

export function zoomAnchor(oldZoom,newZoom,scrollLeft,scrollTop,x,y) {
  return {left:(scrollLeft+x)/oldZoom*newZoom-x,top:(scrollTop+y)/oldZoom*newZoom-y};
}

export function parseValues(text,decimalComma=false) {
  let source=text.trim();
  if(source.startsWith("[")) {
    const result=JSON.parse(source);
    if(!Array.isArray(result)||result.some(v=>typeof v!=="number"||!Number.isFinite(v)))throw new Error("O JSON deve conter somente números finitos.");
    return result;
  }
  const tokens=source.split(decimalComma?/[;\s]+/:/[,;\s]+/).filter(Boolean);
  return tokens.map((token,index)=>{
    const value=Number(decimalComma?token.replace(",","."):token);
    if(!Number.isFinite(value))throw new Error(`Valor inválido na posição ${index}: ${token}`);
    return value;
  });
}

export function valueText(value,index) {
  return value.decimal?.[index] ?? number(value.values[index]);
}

export function floatText(value) {
  if(Object.is(value,-0))return '-0';
  const [mantissa,exponent]=Math.fround(value).toPrecision(9).split('e');
  return (mantissa.includes('.')?mantissa.replace(/0+$/,'').replace(/\.$/,''):mantissa)+(exponent===undefined?'':'e'+exponent);
}

export function vectorLayout(value,representation,bits,dense) {
  const perGroup=Math.max(1,bits/(value.elementBytes*8));
  const columns=Math.min(representation==='number'?8:4,perGroup,value.count);
  const lengths=value.values.map((_,i)=>representation==='hex'?value.hex[i].length:valueText(value,i).length);
  const cellWidth=Math.max(30,(String(value.count-1).length+2)*(dense?5.5:6.1)+8,Math.max(...lengths)*7.3+8,types[value.kind].packed?42:0);
  return {perGroup,columns,indexChars:String(value.count-1).length+2,valueChars:Math.max(...lengths),width:Math.ceil(columns*cellWidth+(columns-1)*3+10)};
}

export function vectorMarkup(value,{representation="number",bits=128,dense=false,selectedIndex=-1,compareValues=null}={}) {
  if(!value)return '<p class="muted">Conecte as entradas para obter os valores.</p>';
  const info=descriptor(value,bits);
  const {perGroup,columns,indexChars,valueChars,width}=vectorLayout(value,representation,bits,dense);
  const slots=info.lanes;
  const glyph=Array.from({length:slots},(_,i)=>`<span class="type-slot ${value.kind==='f32'?'floating':'integer'} ${i>=info.count?'unused':''}"><b>${i>=info.count?'livre':info.bits}</b><small>${i>=info.count?'':"bits"}</small></span>`).join("");
  let html=`<div class="vector-summary"><strong>${info.count} ${info.count===1?'número':'números'} de ${info.bits} bits</strong><span>${escapeHtml(info.family)}</span><div class="register-glyph">${glyph}</div><small>Capacidade do grupo: ${info.lanes} × ${info.bits} bits = ${bits} bits · ${info.groups} ${info.groups===1?'grupo':'grupos'} · ${info.bytes} bytes no total</small></div>`;
  if(representation==='bits'&&value.kind==='f32')html+='<div class="bit-legend"><span class="sign-bit">Sinal · 1 bit</span><span class="exponent-bits">Expoente · 8 bits</span><span class="fraction-bits">Fração · 23 bits</span></div>';
  html+=`<div class="vector-values ${dense?'dense':''} ${representation}" style="--group-width:${width}px;--index-width:${indexChars}ch;--value-width:${valueChars}ch">`;
  for(let start=0;start<value.count;start+=perGroup) {
    const end=Math.min(value.count,start+perGroup);
    html+=`<section class="register-group" aria-label="Grupo de ${bits} bits, posições ${start} a ${end-1}"><header><span>${bits} bits</span><span title="Posições ${start} a ${end-1}">[${start}–${end-1}]</span></header><div class="register-cells" style="--columns:${columns}">`;
    for(let i=start;i<end;i++) {
      let content=escapeHtml(valueText(value,i));
      if(representation==='bits') {
        const raw=value.bits[i];
        content=value.kind==='f32'?`<span class="float-bit-fields"><i class="sign-bit" title="Sinal">${raw[0]}</i><i class="exponent-bits" title="8 bits de expoente">${raw.substring(1,9)}</i><i class="fraction-bits" title="23 bits de fração">${raw.substring(9)}</i></span>`:raw.match(/.{1,4}/g).join(" ");
      } else if(representation==='hex')content=escapeHtml(value.hex[i]);
      const origins=value.origins?.[i]||[];
      const mismatch=compareValues && !Object.is(compareValues[i],value.values[i]);
      html+=`<button class="value-cell ${i===selectedIndex?'chosen':''} ${mismatch?'mismatch':''}" data-element="${i}" title="Índice ${i}. Valor ${escapeHtml(valueText(value,i))}. Posições lógicas: ${escapeHtml(origins.join(', '))}"><span class="value-index">[${i}]</span><span class="value-content">${content}</span>`;
      if(info.packed) {
        const byte=value.values[i],low=byte&15,high=byte>>4;
        html+=`<span class="nibble-pair"><i>baixo ${low}</i><i>alto ${high}</i></span>`;
      }
      html+='</button>';
    }
    html+='</div></section>';
  }
  return html+'</div>';
}

export function highlightC(source) {
  const tokens=source.match(/\/\*[\s\S]*?\*\/|\/\/[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:static|const|return|if|else|for|while|int|float|void|sizeof|uint\d+_t|int\d+_t)\b|\b(?:0x[\da-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[fFuUlL]*)\b|[\s\S]/g)||[];
  return tokens.map(token=>{
    const cls=token.startsWith('/*')||token.startsWith('//')?'comment':token.startsWith('"')||token.startsWith("'")?'string':/^(?:static|const|return|if|else|for|while|int|float|void|sizeof|uint\d+_t|int\d+_t)$/.test(token)?'keyword':/^\d/.test(token)?'number':'';
    return cls?`<span class="token-${cls}">${escapeHtml(token)}</span>`:escapeHtml(token);
  }).join("");
}
