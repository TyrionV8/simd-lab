import {$,escapeHtml as esc,labels,number,metric,descriptor,parseValues,vectorMarkup,highlightC,serialize,exactFloat,floatText} from './model.js';
import {CircuitCanvas} from './canvas.js';

const STORE='simd-lab-circuit-v1';
let meta,graph,report,nativeResult=null,suiteResult=null,presetKey='main',selected=null;
let view='circuit',detail='data',dataSource='simulation',selectedElement=0,codeChoice='all',step=null,playing=null;
let undoStack=[],redoStack=[],moveBefore=null,revision=0,analysisId=0,analysisAbort=null,job=null;
let inputDraft=null,inputDirty=false,pasteTarget='weights',benchmarkHistory=[],currentAssembly={};

async function api(path,body,signal) {
  const response=await fetch(path,body===undefined?{signal}:{method:'POST',headers:{'Content-Type':'application/json'},body:serialize(body),signal});
  const value=await response.json();
  if(!response.ok)throw new Error(value.error||'Falha na operação.');
  return value;
}
function notify(message=''){$('notification').textContent=message;$('notification').hidden=!message;}
function save(){try{localStorage.setItem(STORE,serialize({graph,presetKey,layoutVersion:2}));$('save-status').textContent='Salvo somente neste navegador';}catch{$('save-status').textContent='Use Salvar para guardar o circuito';}}
function download(name,text,type){const url=URL.createObjectURL(new Blob([text],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function nodeById(id){return graph.nodes.find(n=>n.id===id);}
function snapshot(){return structuredClone(graph);}
function remember(before=snapshot()){undoStack.push(before);if(undoStack.length>40)undoStack.shift();redoStack=[];historyControls();}
function historyControls(){$('undo').disabled=!undoStack.length;$('redo').disabled=!redoStack.length;}
function setView(next){view=next;document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===next));document.querySelectorAll('main>.view').forEach(el=>el.hidden=el.id!==next+'-view');if(next==='inputs')renderInputs();if(next==='code')renderCode();if(next==='execution')renderExecution();if(next==='validation')renderValidation();if(next==='clock')renderClock();if(next==='circuit')canvas.requestDraw();}
function stopPlay(){clearInterval(playing);playing=null;$('play').textContent='▶';$('play').setAttribute('aria-label','Reproduzir etapas');}

const canvas=new CircuitCanvas($('canvas-root'),{
  select:selection=>select(selection),
  code:id=>{select({kind:'node',id});detail='operation';codeChoice=id;setView('code');},
  add:(op,position)=>addNode(op,position),
  beginMove:()=>moveBefore=snapshot(),
  moved:changed=>{if(changed&&moveBefore)remember(moveBefore);moveBefore=null;save();},
  connect:edge=>connect(edge),
  connecting:active=>{$('connection-hint').textContent=active?'Escolha a entrada de destino · Esc cancela':'Roda: zoom · arraste o fundo para navegar · saída ● → entrada ○';$('cancel-link').hidden=!active;},
  zoom:value=>$('zoom-label').textContent=Math.round(value*100)+'%'
});

async function refresh({arrange=false,preserveNative=false}={}) {
  const id=++analysisId;revision++;analysisAbort?.abort();analysisAbort=new AbortController();stopPlay();
  if(!preserveNative){nativeResult=null;suiteResult=null;dataSource='simulation';}
  notify();
  try {
    const result=await api('/api/analyze',graph,analysisAbort.signal);if(id!==analysisId)return;
    report=result;graph=result.graph;step=null;
    canvas.setDocument(graph,report,meta.ops);
    if(arrange)canvas.arrange();
    canvas.setSelected(selected);renderStatus();renderInspector();renderValidation();renderExecution();renderClock();renderCode();save();return true;
  }catch(error){if(error.name!=='AbortError')notify(error.message);return false;}
}

async function edit(mutator,options={}) {
  if(job)await cancelJob();const before=snapshot();remember(before);mutator(graph);inputDraft=null;inputDirty=false;const generation=analysisId+1;const ok=await refresh(options);
  if(!ok&&analysisId===generation){graph=before;report.graph=before;if(undoStack.at(-1)===before)undoStack.pop();canvas.setDocument(graph,report,meta.ops);renderStatus();renderInspector();renderValidation();}
}

function renderStatus() {
  if(!report)return;
  $('actual').textContent=number(report.actual);$('expected').textContent=number(report.reference);$('oracle-label').textContent=report.oracle.title;
  $('native-answer').textContent=nativeResult?number(nativeResult.comparison.actual):'Ainda não executado';
  $('native-match').textContent=nativeResult?(nativeResult.matched?`${nativeResult.elementsChecked} elementos conferidos com a simulação`:'Há diferenças entre C e simulação'):'';
  const comparison=report.comparison;
  $('verdict').textContent=!report.valid?'Circuito incompleto':comparison.bitEqual?'Idêntico bit a bit ao gabarito':report.passed?'Dentro da tolerância do gabarito':'Fora do critério do gabarito';
  $('verdict').parentElement.classList.toggle('bad',!report.valid||!report.passed);
  $('error-metrics').innerHTML=comparison?`<span>Erro absoluto ${esc(number(comparison.absolute))}</span><span>${comparison.ulp} ULP</span><span>${comparison.bitEqual?'Bits idênticos':'Bits diferentes'}</span>`:`<span>${esc(report.message)}</span>`;
  $('graph-size').textContent=`${graph.nodes.length} blocos · ${graph.edges.length} conexões`;
  $('step-label').textContent=step===null?'Todas as etapas':step<0?'Antes da primeira etapa':`Etapa ${step+1} de ${report.order.length}`;
  const revealed=step===null?null:new Set(report.order.slice(0,step+1));
  const cursor=revealed?Math.max(0,...report.clock.timeline.filter(n=>revealed.has(n.id)).map(n=>n.finish)):null;
  $('virtual-cycle').textContent=cursor!==null?`Modelo didático · etapas reveladas até o ciclo ${cursor}`:`Modelo didático · ${report.clock.criticalCycles} ciclos no caminho crítico`;
  $('previous').disabled=step===-1||!report.order.length;$('next').disabled=!report.order.length||(step!==null&&step>=report.order.length-1);
  $('run-native').disabled=!report.valid||report.missing.length>0||!!job||!meta.cpu.compiler||inputDirty;
  $('run-validation').disabled=!report.valid||!!job||inputDirty;
  $('hardware-status').innerHTML=report.required.map(f=>`<span class="hardware-tag ${report.missing.includes(f)?'missing':''}">${esc(f)} · ${report.missing.includes(f)?'indisponível':'disponível'}</span>`).join('')||'<span class="hardware-tag">Instruções básicas</span>';
  if(report.missing.length)$('hardware-status').innerHTML+='<p class="muted">A simulação e os casos de teste continuam disponíveis; a execução nativa exige as extensões indicadas.</p>';
  historyControls();
}

function renderPalette() {
  const query=$('search').value.toLocaleLowerCase('pt-BR'),groups={};
  Object.entries(meta.ops).forEach(([key,op])=>{if(query&&![op.title,op.description,op.instructions].join(' ').toLocaleLowerCase('pt-BR').includes(query))return;(groups[op.group]??=[]).push([key,op]);});
  $('palette-list').innerHTML=Object.entries(groups).map(([group,items])=>`<section class="palette-group"><h3>${esc(group)}</h3>${items.map(([key,op])=>`<button class="palette-item" draggable="true" data-op="${key}" title="${esc(op.description)}">${esc(op.title)}</button>`).join('')}</section>`).join('');
  $('palette-list').querySelectorAll('[data-op]').forEach(button=>{button.addEventListener('click',()=>addNode(button.dataset.op));button.addEventListener('dragstart',event=>{event.dataTransfer.setData('application/x-simd-block',button.dataset.op);event.dataTransfer.effectAllowed='copy';});});
}

async function addNode(op,position) {
  if(!meta.ops[op])return;
  let counter=1;while(nodeById('n'+counter))counter++;
  if(!position){position=canvas.toWorld(canvas.viewport.getBoundingClientRect().left+50,canvas.viewport.getBoundingClientRect().top+50);for(let i=0;i<100;i++){const point={x:position.x+(i%2)*560,y:position.y+Math.floor(i/2)*650};if(!graph.nodes.some(n=>Math.abs(n.x-point.x)<420&&Math.abs(n.y-point.y)<600)){position=point;break;}}}
  const params={};Object.entries(meta.ops[op].choices).forEach(([key,choices])=>params[key]=choices==='number'?0:choices[0]);
  const node={id:'n'+counter,op,params,x:Math.max(20,position.x),y:Math.max(20,position.y)};
  selected={kind:'node',id:node.id};await edit(g=>g.nodes.push(node));$('palette').hidden=true;canvas.focus(node.id);select(selected);
}

function reaches(start,target,seen=new Set()){if(start===target)return true;if(seen.has(start))return false;seen.add(start);return graph.edges.filter(e=>e.from===start).some(e=>reaches(e.to,target,seen));}
async function connect(edge) {
  if(reaches(edge.to,edge.from))return notify('A conexão criaria um ciclo. O fluxo precisa chegar à saída sem voltar ao próprio bloco.');
  const kind=report.nodes[edge.from]?.outputs[edge.out]?.kind,expected=meta.ops[nodeById(edge.to).op].inputs[edge.input];
  if(kind&&!expected.includes(kind))return notify(`Tipos incompatíveis: ${kind} → ${expected.join(' / ')}. Adicione uma conversão.`);
  await edit(g=>{g.edges=g.edges.filter(e=>e.to!==edge.to||e.input!==edge.input);g.edges.push(edge);selected={kind:'edge',index:g.edges.length-1};});select(selected);
}

function select(selection) {
  selected=selection;selectedElement=selection?.element??0;
  if(!selection?.keepInspectorState)document.querySelector('.diagram-layout').classList.remove('inspector-hidden');canvas.setSelected(selected);renderInspector();
}
function selectedData() {
  if(!selected)return null;
  const edge=selected.kind==='edge'?graph.edges[selected.index]:null;
  const id=edge?.from||selected.id,node=nodeById(id);if(!node)return null;
  const outputs=report.nodes[id]?.outputs||{},port=edge?.out||selected.port||Object.keys(outputs)[0];
  return {id,node,port,edge,value:outputs[port]};
}

function withNative(value,key) {
  if(!nativeResult?.values[key])return value;
  const values=nativeResult.values[key].map(v=>value.kind==='f32'?Math.fround(v):v);
  const bits=[],hex=[],decimal=[];
  for(const v of values){const buffer=new ArrayBuffer(value.elementBytes),view=new DataView(buffer);if(value.kind==='f32')view.setFloat32(0,v,true);else if(value.elementBytes===4)view.setInt32(0,v,true);else if(value.elementBytes===2)view.setInt16(0,v,true);else view.setUint8(0,v&255);const bytes=[...new Uint8Array(buffer)];hex.push(bytes.map(b=>b.toString(16).padStart(2,'0')).join(' '));bits.push(bytes.toReversed().map(b=>b.toString(2).padStart(8,'0')).join(''));decimal.push(value.kind==='f32'?floatText(v):String(v));}
  return {...value,values,bits,hex,decimal};
}

function elementDetail(value,index) {
  index=Math.min(Math.max(0,index),value.count-1);const bits=value.bits[index],origins=value.origins?.[index]||[];
  let floatInfo='';
  if(value.kind==='f32'){const sign=Number(bits[0]),exponent=parseInt(bits.substring(1,9),2),fraction=parseInt(bits.substring(9),2);floatInfo=`<dt>Campos do floating point</dt><dd>Sinal ${sign} · expoente codificado ${exponent} · fração inteira ${fraction}</dd><dt>Regra de interpretação</dt><dd>${exponent===0?`(−1)^${sign} × (${fraction} / 8388608) × 2^−126`:`(−1)^${sign} × (1 + ${fraction} / 8388608) × 2^(${exponent} − 127)`}</dd>`;}
  return `<div class="element-detail"><h3>Elemento [${index}]</h3><dl><div><dt>Decimal que reconstrói os bits</dt><dd>${esc(value.decimal[index])}</dd></div>${value.kind==='f32'?`<div><dt>Valor decimal exato deste FP32</dt><dd>${exactFloat(value.values[index])}</dd></div>`:''}<div><dt>Bits completos</dt><dd>${bits}</dd></div><div><dt>Bytes na memória · little-endian</dt><dd>${value.hex[index]}</dd></div>${floatInfo}<div><dt>Posições lógicas / parcelas</dt><dd>${origins.length?origins.join(', '):'Escalar sem índice de entrada'}</dd></div></dl></div>`;
}

function renderInspector() {
  document.querySelectorAll('[data-detail]').forEach(b=>b.classList.toggle('active',b.dataset.detail===detail));
  const source=selectedData();
  if(!source){$('inspector-content').innerHTML='<span class="eyebrow">DADOS EM TRÂNSITO</span><h2>Selecione um bloco ou conexão.</h2><p class="muted">Os vetores completos, tipos, funções C e posições ficam aqui.</p>';return;}
  if(detail==='operation')return renderOperation(source);
  const op=meta.ops[source.node.op];
  let html=`<span class="eyebrow">MEU CIRCUITO · ${esc(source.id)}</span><h2>${esc(op.title)}</h2><p class="muted">${esc(source.edge?`${source.edge.from}.${source.edge.out} → ${source.edge.to}.${source.edge.input}`:`Saída ${labels[source.port]||source.port}`)}</p>`;
  if(!source.value){$('inspector-content').innerHTML=html+`<p class="node-error">${esc(report.nodes[source.id]?.error||'Conecte as entradas.')}</p>`;return;}
  const outputs=report.nodes[source.id].outputs;
  html+=`<div class="inspector-controls"><label>Porta de saída<select id="data-port">${Object.keys(outputs).map(port=>`<option value="${port}" ${port===source.port?'selected':''}>${esc(labels[port]||port)}</option>`).join('')}</select></label><label>Origem dos valores<select id="data-source"><option value="simulation">Meu circuito · simulação</option><option value="native" ${!nativeResult?'disabled':''}>C executado no Xeon</option></select></label></div>`;
  const native=withNative(source.value,source.id+'.'+source.port),value=dataSource==='native'&&nativeResult?native:source.value;
  html+=`<p class="data-note">${dataSource==='native'&&nativeResult?'Valores lidos do programa C.':'Valores calculados pelo circuito simulado.'} O gabarito final é uma conta independente, mostrada no topo.</p>`;
  html+=vectorMarkup(value,{representation:$('representation').value,bits:Number($('register-bits').value),selectedIndex:selectedElement,compareValues:nativeResult?(dataSource==='native'?source.value.values:native.values):null});
  html+=elementDetail(value,selectedElement);
  html+='<p class="data-note">Os decimais FP32 têm até 9 dígitos significativos, suficientes para reconstruir os bits. Nenhum elemento do vetor é omitido. As posições indicam correspondência lógica; não incluem dependências indiretas pela escala.</p>';
  if(source.edge)html+='<button id="remove-edge" class="danger">Remover esta conexão</button>';
  $('inspector-content').innerHTML=html;$('data-source').value=dataSource;
  $('data-source').addEventListener('change',()=>{dataSource=$('data-source').value;renderInspector();});
  $('data-port').addEventListener('change',()=>{selected={kind:'node',id:source.id,port:$('data-port').value};selectedElement=0;canvas.setSelected(selected);renderInspector();});
  $('inspector-content').querySelectorAll('[data-element]').forEach(button=>button.addEventListener('click',()=>{selectedElement=Number(button.dataset.element);const scroll=$('inspector').scrollTop;renderInspector();$('inspector').scrollTop=scroll;}));
  $('remove-edge')?.addEventListener('click',()=>edit(g=>{g.edges.splice(selected.index,1);selected={kind:'node',id:source.id,port:source.port};}));
}

function renderOperation(source) {
  const node=source.node,op=meta.ops[node.op],implementation=report.nodes[node.id]?.implementation;
  const params=Object.entries(op.choices).map(([key,choices])=>`<label>${esc(labels[key]||key)}${choices==='number'?`<input data-param="${key}" type="number" step="any" value="${node.params[key]}">`:`<select data-param="${key}">${choices.map(choice=>`<option value="${choice}" ${node.params[key]===choice?'selected':''}>${esc(labels[choice]||choice)}</option>`).join('')}</select>`}</label>`).join('');
  const connections=Object.keys(op.inputs).map(port=>{const index=graph.edges.findIndex(e=>e.to===node.id&&e.input===port),edge=graph.edges[index];return `<div class="connection-row"><span>${esc(labels[port]||port)} ← ${edge?esc(edge.from+'.'+edge.out):'não conectada'}</span>${edge?`<button data-unlink="${index}">Desligar</button>`:''}</div>`;}).join('');
  $('inspector-content').innerHTML=`<span class="eyebrow">${node.id} · OPERAÇÃO</span><h2>${esc(op.title)}</h2><p class="muted">${esc(op.description)}</p><div class="node-params">${params}</div><div class="node-actions"><button id="duplicate">Duplicar</button><button id="remove-node">Remover bloco</button></div>${connections}<h3>Instruções / operações</h3><p class="data-note">${esc(implementation?.instructions||op.instructions||'Laço e operações escalares em C.')}</p><h3>Trecho C deste bloco</h3><pre class="code small">${highlightC(implementation?.snippet||'/* Complete o circuito para gerar o C deste bloco. */')}</pre>${Object.entries(implementation?.functions||{}).map(([name,code])=>`<details class="function-detail" open><summary>Função ${esc(name)}</summary><pre class="code small">${highlightC(code)}</pre></details>`).join('')}<p class="data-note">As instruções exibidas descrevem a implementação solicitada. O assembly na área Xeon real mostra o que o compilador efetivamente gerou.</p>`;
  $('inspector-content').querySelectorAll('[data-param]').forEach(field=>field.addEventListener('change',()=>edit(()=>node.params[field.dataset.param]=field.type==='number'?Number(field.value):field.value)));
  $('duplicate').addEventListener('click',()=>{let id=1;while(nodeById('n'+id))id++;const copy=structuredClone(node);copy.id='n'+id;copy.x+=80;copy.y+=150;edit(g=>{g.nodes.push(copy);selected={kind:'node',id:copy.id};});});
  $('remove-node').addEventListener('click',()=>edit(g=>{g.nodes=g.nodes.filter(n=>n.id!==node.id);g.edges=g.edges.filter(e=>e.from!==node.id&&e.to!==node.id);selected=null;}));
  $('inspector-content').querySelectorAll('[data-unlink]').forEach(b=>b.addEventListener('click',()=>edit(g=>g.edges.splice(Number(b.dataset.unlink),1))));
}

function setStep(next) {
  if(!report?.order.length)return;step=next===null?null:Math.max(-1,Math.min(next,report.order.length-1));canvas.setStep(step);renderStatus();
  if(step!==null&&step>=0){select({kind:'node',id:report.order[step]});canvas.focus(report.order[step],{smooth:true});}
}
function play() {if(playing){stopPlay();return;}if(step===null||step>=report.order.length-1)step=-1;$('play').textContent='Ⅱ';$('play').setAttribute('aria-label','Pausar etapas');playing=setInterval(()=>{if(step>=report.order.length-1){stopPlay();return;}setStep(step+1);},Number($('play-speed').value));}

function renderInputs() {
  if(!inputDraft)inputDraft=structuredClone(graph.inputs);
  for(const key of ['weights','activations']) {
    const id=key==='weights'?'weights-grid':'activations-grid';
    $(id).innerHTML=inputDraft[key].map((value,index)=>`<label class="input-cell"><span>[${index}]</span><input type="text" inputmode="decimal" aria-label="${key==='weights'?'Peso':'Ativação'} ${index}" data-input="${key}" data-index="${index}" value="${esc(number(value))}"></label>`).join('');
    $(id).querySelectorAll('input').forEach(field=>{
      field.addEventListener('input',()=>{const raw=field.value.replace('−','-').replace(',','.');inputDraft[key][Number(field.dataset.index)]=raw.trim()===''?NaN:Number(raw);inputDirty=true;validateInputDraft();});
      field.addEventListener('paste',event=>{const text=event.clipboardData.getData('text');if(!/[\s;\[\]]/.test(text)&&text.split(',').length<3)return;try{const values=parseValues(text),start=Number(field.dataset.index);if(values.length>64-start)throw new Error('A lista ultrapassa a última posição da grade.');event.preventDefault();values.forEach((v,i)=>inputDraft[key][start+i]=v);inputDirty=true;renderInputs();}catch(error){event.preventDefault();notify(error.message);}});
      field.addEventListener('keydown',event=>{const index=Number(field.dataset.index),delta={ArrowDown:8,ArrowUp:-8,ArrowLeft:-1,ArrowRight:1}[event.key];if(delta!==undefined){const next=$(id).querySelector(`[data-index="${index+delta}"]`);if(next){event.preventDefault();next.focus();next.select();}}});
    });
  }
  $('weight-scale').value=inputDraft.weightScale;validateInputDraft();
}
function validateInputDraft() {
  let invalid=false;
  for(const key of ['weights','activations']){
    const issues=[];inputDraft[key].forEach((v,index)=>{const bad=!Number.isFinite(v)||(key==='weights'?(!Number.isInteger(v)||v< -8||v>7):Math.abs(v)>1e6);document.querySelector(`[data-input="${key}"][data-index="${index}"]`)?.closest('.input-cell').classList.toggle('invalid',bad);if(bad)issues.push(`[${index}] ${key==='weights'?'inteiro de −8 a 7':'número finito entre −1000000 e 1000000'}`);});
    $(key+'-errors').textContent=issues.join('\n');invalid ||= issues.length>0;
  }
  if(!Number.isFinite(inputDraft.weightScale)||Math.abs(inputDraft.weightScale)>1e6)invalid=true;
  $('apply-inputs').disabled=invalid;$('input-dirty').textContent=inputDirty?'Edições ainda não aplicadas':'Entradas aplicadas';renderStatus();return !invalid;
}
function generator() {
  const pattern=$('input-pattern').value;let seed=Number($('input-seed').value);if(!Number.isInteger(seed)||seed<0||seed>4294967295)return notify('A semente deve ser um inteiro entre 0 e 4294967295.');seed>>>=0;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
  inputDraft=structuredClone(graph.inputs);
  inputDraft.weights=Array.from({length:64},(_,i)=>pattern==='zero'?0:pattern==='random'?Math.floor(random()*16)-8:pattern==='extreme'?(i%2?-8:7):(i%16)-8);
  inputDraft.activations=Array.from({length:64},(_,i)=>pattern==='zero'?0:pattern==='ones'?1:pattern==='random'?Math.fround((random()*2-1)*100):pattern==='fractional'?Math.fround(Math.sin(i*.73)*13.71):pattern==='extreme'?(i%2?127:-127):[127,-64,32,-16,8,-4,2,-1][i%8]);
  inputDirty=true;renderInputs();
}

function renderCode() {
  if(!report)return;
  $('code-features').textContent=report.valid?'Extensões solicitadas: '+(report.required.join(', ')||'instruções básicas'):'Complete o circuito para gerar o programa.';
  $('code-block-list').innerHTML=graph.nodes.filter(n=>report.nodes[n.id]?.active).map(n=>`<button data-code="${n.id}">${n.id} · ${esc(meta.ops[n.op].title)}</button>`).join('');
  document.querySelectorAll('[data-code]').forEach(button=>{button.classList.toggle('active',button.dataset.code===codeChoice);button.onclick=()=>{codeChoice=button.dataset.code;renderCode();};});
  let source=report.code,title='Programa C completo';
  if(codeChoice==='kernel'){source=report.functions?.circuit||report.code;title='Função circuit · recebe as entradas em tempo de execução';}
  else if(codeChoice!=='all'){const node=nodeById(codeChoice),impl=report.nodes[codeChoice]?.implementation;if(node&&impl){source=impl.snippet+'\n\n'+Object.values(impl.functions).join('\n\n');title=codeChoice+' · '+meta.ops[node.op].title;}}
  $('code-title').textContent=title;$('code').innerHTML=highlightC(source);
}

function options() {return {repetitions:Number($('run-repetitions').value),samples:Number($('run-samples').value),warmup:Number($('run-warmup').value),core:Number($('run-core').value),benchmark:true,suite:$('run-suite').checked,randomCount:Number($('random-count').value),seed:Number($('validation-seed').value)};}
async function startJob(kind) {
  notify();const version=revision;
  try{
    const submitted=await api('/api/jobs/'+kind,{graph,options:options()});job={id:submitted.jobId,kind,version};$('job-progress').hidden=false;renderStatus();
    while(job?.id===submitted.jobId){
      const state=await api('/api/jobs/'+submitted.jobId);if(job?.id!==submitted.jobId)break;$('job-message').textContent=state.message;$('job-percent').value=state.progress;
      if(state.status==='done'){
        if(version===revision){if(kind==='native'){nativeResult=state.result;suiteResult=state.result.suite||null;if(nativeResult.benchmark)benchmarkHistory.unshift({name:meta.presets.find(p=>p.id===presetKey)?.title||'Circuito personalizado',result:nativeResult});benchmarkHistory=benchmarkHistory.slice(0,8);}else suiteResult=state.result;renderStatus();renderInspector();renderValidation();renderExecution();}
        job=null;break;
      }
      if(state.status==='error'||state.status==='cancelled'){notify(state.error||state.message);job=null;break;}
      await new Promise(resolve=>setTimeout(resolve,350));
    }
  }catch(error){notify(error.message);job=null;}
  finally{if(!job)$('job-progress').hidden=true;renderStatus();}
}
async function cancelJob(){if(!job)return;const old=job;job=null;await api('/api/jobs/'+old.id+'/cancel',{});$('job-progress').hidden=true;renderStatus();}

function metricCard(label,value,note=''){return `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;}
function renderExecution() {
  if(!meta)return;
  $('cpu-details').textContent=meta.cpu.name+' · '+meta.cpu.compilerVersion;
  if(!nativeResult){$('native-results').innerHTML='<p class="empty-panel">Execute para obter as medidas internas do C, a comparação com o gabarito e a conferência das etapas.</p>';$('assembly-panel').hidden=true;return;}
  const result=nativeResult,b=result.benchmark,c=result.comparison;
  let html=`<section class="panel"><div class="suite-result-header"><h2>C executado ${c.passed?'atende ao':'não atende ao'} critério do gabarito</h2><span class="${c.passed?'pass':'fail'}">${c.passed?'APROVADO':'DIVERGENTE'}</span></div><p class="muted">${result.matched?`${result.elementsChecked} elementos do C coincidem bit a bit com a simulação do seu circuito.`:'Há diferenças entre o C e a simulação.'} Compilação ${result.cached?'reutilizada do cache':metric(result.compileMs,'ms')}; esse tempo está fora das amostras.</p><code>${esc(result.command)}</code></section>`;
  if(b){html+='<div class="metric-grid">'+metricCard('Tempo mediano por circuito',metric(b.medianNs,'ns'),`${b.repetitions} repetições × ${b.samples.length} amostras`)+metricCard('Ticks TSC por circuito',metric(b.tscTicksPerBlock),"Contador de referência; não são ciclos do núcleo")+metricCard('Ciclos do núcleo · PMU',metric(b.coreCyclesPerBlock),b.coreCyclesPerBlock===null?b.perfReason:'Contador de hardware, normalizado pelo tempo ativo')+metricCard('Variação entre amostras',metric(b.spreadPercent,'%'),`Mínimo ${metric(b.minNs,'ns')} · máximo ${metric(b.maxNs,'ns')}`)+metricCard('Loop vazio por repetição',metric(b.emptyMedianNs,'ns'),'Medido separadamente; não subtraído')+metricCard('Execuções por segundo',metric(b.blocksPerSecond),`Circuito completo · CPU lógica ${b.core}`)+'</div>';
    html+=`<section class="panel"><h2>Amostras completas</h2><div class="table-wrap"><table><thead><tr><th>Amostra</th><th>Tempo total</th><th>ns / circuito</th><th>Tempo de CPU</th><th>Ticks TSC</th><th>Migração</th></tr></thead><tbody>${b.samples.map((s,i)=>`<tr><td>${i+1}</td><td class="numeric">${s.wallNs} ns</td><td class="numeric">${number(s.wallNs/b.repetitions)}</td><td class="numeric">${s.cpuNs} ns</td><td class="numeric">${s.tscTicks}</td><td>${s.migrated?'Detectada':'Não detectada'}</td></tr>`).join('')}</tbody></table></div><p class="model-note">${esc(b.note)}</p>${b.affinityErrno?'<p class="fail">A afinidade solicitada não foi aplicada; confira a CPU e repita.</p>':''}</section>`;}
  if(!result.matched)html+=`<section class="panel"><h2>Diferenças por etapa</h2><pre class="code">${esc(JSON.stringify(result.differences,null,2))}</pre></section>`;
  $('native-results').innerHTML=html;currentAssembly={};let current='';for(const line of result.assembly.split('\n')){const match=line.match(/^[0-9a-f]+ <([^>]+)>:/);if(match){current=match[1];currentAssembly[current]='';}if(current)currentAssembly[current]+=line+'\n';}
  $('assembly-panel').hidden=false;$('assembly-function').innerHTML=Object.keys(currentAssembly).map(name=>`<option>${esc(name)}</option>`).join('');if(currentAssembly.circuit)$('assembly-function').value='circuit';renderAssembly();
  $('benchmark-history').hidden=!benchmarkHistory.length;$('history-table').innerHTML='<div class="table-wrap"><table><thead><tr><th>Circuito</th><th>ns / execução</th><th>TSC / execução</th><th>Repetições</th><th>CPU lógica</th><th>Gabarito</th></tr></thead><tbody>'+benchmarkHistory.map(({name,result:r})=>`<tr><td><details><summary>${esc(name)}</summary><div class="case-detail"><b>Pesos</b><pre>${esc(r.inputs.weights.join(", "))}</pre><b>Ativações</b><pre>${esc(r.inputs.activations.join(", "))}</pre><b>Escala ${esc(number(r.inputs.weightScale))}</b><p>Gabarito: ${esc(meta.references[r.referenceMode].title)}.</p></div></details></td><td class="numeric">${number(r.benchmark.medianNs)}</td><td class="numeric">${number(r.benchmark.tscTicksPerBlock)}</td><td>${r.benchmark.repetitions}</td><td>${r.benchmark.core}</td><td class="${r.comparison.passed?'pass':'fail'}">${r.comparison.passed?'Aprovado':'Divergente'}</td></tr>`).join('')+'</tbody></table></div><p class="model-note">Compare circuitos sob as mesmas entradas, afinidade e carga do computador. Estes resultados são das execuções desta sessão.</p>';
}
function renderAssembly(){$('assembly').textContent=currentAssembly[$('assembly-function').value]||nativeResult?.assembly||'';}

function renderValidation() {
  if(!report)return;
  $('reference-mode').value=graph.inputs.referenceMode;$('validation-policy').value=graph.inputs.validationPolicy;$('atol').value=graph.inputs.atol;$('rtol').value=graph.inputs.rtol;
  $('atol').disabled=graph.inputs.validationPolicy==='bits';$('rtol').disabled=graph.inputs.validationPolicy==='bits';
  $('reference-description').textContent=report.oracle.description;
  const c=report.comparison;
  $('current-validation').innerHTML=c?`<h2>Entrada atual · ${c.passed?'aprovada':'divergente'}</h2><div class="current-metrics">${[['Meu circuito',c.actual],['Gabarito',c.expected],['Erro absoluto',c.absolute],['Erro relativo',c.relative===null?'Não definido: gabarito zero':c.relative],['Distância em ULP',c.ulp],['Bits',c.bitEqual?'Idênticos':'Diferentes'],[c.policy==='bits'?'Critério ativo':'Tolerância aplicada',c.policy==='bits'?'Igualdade dos bits':c.tolerance]].map(([key,val])=>`<div><span>${key}</span><strong>${esc(number(val))}</strong></div>`).join('')}</div><details class="function-detail"><summary>Conta matemática original, sem quantização</summary><p class="exact-value">${esc(report.oracle.exactDecimal)}</p><p class="muted">Arredondada uma vez para FP32: ${esc(number(report.oracle.mathematical))}.</p></details>`:`<h2>Complete seu circuito</h2><p class="fail">${esc(report.message)}</p>`;
  if(report.issues.length)$('current-validation').innerHTML+='<h3>Entradas e operações pendentes</h3>'+report.issues.map(issue=>`<p class="muted">${esc(issue.node)}: ${esc(issue.message)}</p>`).join('');
  if(report.unused.length)$('current-validation').innerHTML+=`<p class="muted">Blocos fora do caminho de saída: ${esc(report.unused.join(', '))}. Eles não participam do C gerado.</p>`;
  if(suiteResult){const s=suiteResult;$('suite-results').innerHTML=`<div class="suite-result-header"><h2>${s.passed} de ${s.total} casos aprovados contra o gabarito</h2><span>${s.native?'Também executados em C':'Simulação dos casos'}</span></div><p class="muted">Semente ${s.seed} · ${esc(meta.references[s.referenceMode].title)} · ${s.policy==='bits'?'bits idênticos':'tolerância configurada'}</p><div class="table-wrap"><table><thead><tr><th>Caso / entradas</th><th>Meu circuito</th><th>Gabarito</th><th>Erro absoluto</th><th>ULP</th><th>Critério</th><th>C × simulação</th></tr></thead><tbody>${s.cases.map((r,i)=>`<tr><td><details><summary>${esc(r.name)}</summary><div class="case-detail"><b>Pesos</b><pre>${esc(r.inputs.weights.join(', '))}</pre><b>Ativações</b><pre>${esc(r.inputs.activations.join(', '))}</pre><b>Escala ${esc(number(r.inputs.weightScale))}</b>${r.error?`<p class="fail">${esc(r.error)}</p>`:''}</div></details></td><td class="numeric">${number(r.comparison?.actual)}</td><td class="numeric">${number(r.comparison?.expected)}</td><td class="numeric">${number(r.comparison?.absolute)}</td><td>${number(r.comparison?.ulp)}</td><td class="${r.passed?'pass':'fail'}">${r.passed?'Passou':'Falhou'}</td><td>${r.nativeMatched===null?'Não executado':r.nativeMatched?'Idênticos':'Diferentes'}</td></tr>`).join('')}</tbody></table></div>`;}
  else $('suite-results').innerHTML='<p class="muted">Valide o circuito com outras entradas. Concordância entre C e simulação não substitui o gabarito.</p>';
  $('quantization-results').hidden=!report.quantization.length;
  $('quantization-results').innerHTML=report.quantization.map(q=>`<h2>Quantização em ${q.id}</h2><p class="muted">Maior erro de reconstrução: ${esc(number(q.maxError))}. Erro quadrático médio (raiz): ${esc(number(q.rmsError))}.</p><details><summary>Todos os valores antes e depois</summary><div class="table-wrap"><table><thead><tr><th>Posição</th><th>Ativação original</th><th>Reconstruída de int8</th><th>Diferença</th></tr></thead><tbody>${q.original.map((v,i)=>`<tr><td>${i}</td><td class="numeric">${number(v)}</td><td class="numeric">${number(q.reconstructed[i])}</td><td class="numeric">${number(q.reconstructed[i]-v)}</td></tr>`).join('')}</tbody></table></div></details>`).join('');
}

function renderClock() {
  if(!report)return;const c=report.clock;$('clock-ghz').value=c.clockGHz;$('clock-cost').value=c.cyclesPerBatch;
  $('clock-summary').innerHTML=metricCard('Caminho crítico',metric(c.criticalCycles,'ciclos'),'Modelo com ramos independentes sobrepostos')+metricCard('Tempo no clock configurado',metric(c.criticalNs,'ns'),`${c.clockGHz} GHz · estimativa didática`)+metricCard('Soma serial dos custos',metric(c.serialCycles,'ciclos'),'Sem sobreposição entre blocos');
  $('clock-timeline').innerHTML='<h2>Escalonamento por dependências</h2><div class="table-wrap"><table><thead><tr><th>Bloco</th><th>Grupos de resultados</th><th>Início</th><th>Fim</th><th>Ciclos</th><th>Linha do tempo didática</th></tr></thead><tbody>'+c.timeline.map(n=>`<tr><td><button data-clock-node="${n.id}">${n.id} · ${esc(n.title)}</button></td><td>${n.batches} grupos de até ${n.width} bits</td><td>${n.start}</td><td>${n.finish}</td><td>${n.cycles}</td><td><div class="timeline-bar"><i style="left:${n.start/c.criticalCycles*100}%;width:${n.cycles/c.criticalCycles*100}%"></i></div></td></tr>`).join('')+'</tbody></table></div>';
  $('clock-timeline').querySelectorAll('[data-clock-node]').forEach(button=>button.addEventListener('click',()=>{setView('circuit');select({kind:'node',id:button.dataset.clockNode});canvas.focus(button.dataset.clockNode);}));
}

async function loadPreset(key) {
  if(job)await cancelJob();remember();graph=await api('/api/preset/'+key);presetKey=key;selected={kind:'node',id:graph.nodes[0].id};inputDraft=null;inputDirty=false;codeChoice='all';detail='data';
  $('preset').value=key;await refresh({arrange:true});canvas.viewport.scrollTo(0,0);setView('circuit');
}

document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.view)));
document.querySelectorAll('[data-detail]').forEach(button=>button.addEventListener('click',()=>{detail=button.dataset.detail;renderInspector();}));
$('preset').addEventListener('change',()=>{if($('preset').value!=='custom')loadPreset($('preset').value).catch(e=>notify(e.message));});
$('toggle-palette').addEventListener('click',()=>$('palette').hidden=!$('palette').hidden);$('empty-add').addEventListener('click',()=>$('palette').hidden=false);$('close-palette').addEventListener('click',()=>$('palette').hidden=true);$('search').addEventListener('input',renderPalette);
$('cancel-link').addEventListener('click',()=>canvas.cancelConnection());
$('arrange').addEventListener('click',()=>{remember();canvas.arrange();});$('fit').addEventListener('click',()=>canvas.fit());
$('zoom-in').addEventListener('click',()=>canvas.setZoom(canvas.zoom*1.15));$('zoom-out').addEventListener('click',()=>canvas.setZoom(canvas.zoom/1.15));$('zoom-reset').addEventListener('click',()=>canvas.setZoom(1));$('focus-selected').addEventListener('click',()=>{const source=selectedData();if(source)canvas.focus(source.id);});
$('show-node-data').addEventListener('change',()=>canvas.configure({showData:$('show-node-data').checked}));
for(const id of ['representation','register-bits'])$(id).addEventListener('change',()=>{canvas.configure({representation:$('representation').value,registerBits:Number($('register-bits').value)});renderInspector();});
$('close-inspector').addEventListener('click',()=>document.querySelector('.diagram-layout').classList.add('inspector-hidden'));
$('expand-inspector').addEventListener('click',()=>{const current=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--inspector-width'));document.documentElement.style.setProperty('--inspector-width',current>450?'400px':Math.min(innerWidth*.65,800)+'px');});
$('inspector-resizer').addEventListener('pointerdown',event=>{event.preventDefault();const start=event.clientX,width=$('inspector').offsetWidth;function move(e){document.documentElement.style.setProperty('--inspector-width',Math.min(innerWidth*.7,Math.max(300,width+start-e.clientX))+'px');}function up(){document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',up);}document.addEventListener('pointermove',move);document.addEventListener('pointerup',up);});
$('restart').addEventListener('click',()=>{stopPlay();setStep(-1);canvas.viewport.scrollTo(0,0);});$('previous').addEventListener('click',()=>{stopPlay();setStep(step===null?report.order.length-2:step-1);});$('next').addEventListener('click',()=>{stopPlay();setStep(step===null?0:step+1);});$('reveal').addEventListener('click',()=>{stopPlay();setStep(null);});$('play').addEventListener('click',play);$('play-speed').addEventListener('change',()=>{if(playing){stopPlay();play();}});
$('apply-inputs').addEventListener('click',async()=>{if(!validateInputDraft())return;const inputs=structuredClone(inputDraft);await edit(g=>g.inputs={...g.inputs,...inputs});inputDirty=false;renderInputs();});$('weight-scale').addEventListener('input',()=>{inputDraft.weightScale=Number($('weight-scale').value);inputDirty=true;validateInputDraft();});$('generate-inputs').addEventListener('click',generator);
document.querySelectorAll('[data-paste]').forEach(button=>button.addEventListener('click',()=>{pasteTarget=button.dataset.paste;$('paste-panel').hidden=false;$('paste-title').textContent='Colar '+(pasteTarget==='weights'?'64 pesos':'64 ativações');$('paste-values').focus();}));$('close-paste').addEventListener('click',()=>$('paste-panel').hidden=true);
function countPaste(){try{$('paste-count').textContent=parseValues($('paste-values').value,$('decimal-comma').checked).length+' valores identificados';}catch(e){$('paste-count').textContent=e.message;}}$('paste-values').addEventListener('input',countPaste);$('decimal-comma').addEventListener('change',countPaste);
$('apply-paste').addEventListener('click',()=>{try{const values=parseValues($('paste-values').value,$('decimal-comma').checked);if(values.length!==64)throw new Error('São necessários exatamente 64 valores. Recebidos: '+values.length);inputDraft[pasteTarget]=values;inputDirty=true;renderInputs();$('paste-panel').hidden=true;}catch(e){notify(e.message);}});
$('apply-validation').addEventListener('click',()=>edit(g=>Object.assign(g.inputs,{referenceMode:$('reference-mode').value,validationPolicy:$('validation-policy').value,atol:Number($('atol').value),rtol:Number($('rtol').value)})));
$('apply-clock').addEventListener('click',()=>edit(g=>g.simulation={clockGHz:Number($('clock-ghz').value),cyclesPerBatch:Number($('clock-cost').value)}));
$('run-native').addEventListener('click',()=>startJob('native'));$('run-validation').addEventListener('click',()=>startJob('validate'));$('cancel-job').addEventListener('click',cancelJob);$('assembly-function').addEventListener('change',renderAssembly);
$('copy-code').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(report.code);$('copy-code').textContent='Copiado';setTimeout(()=>$('copy-code').textContent='Copiar programa',1000);}catch{notify('Use Baixar circuito.c para obter o programa completo.');}});$('download-code').addEventListener('click',()=>download('circuito.c',report.code,'text/x-csrc'));
$('save').addEventListener('click',()=>download('circuito-simd.json',serialize({graph,presetKey,layoutVersion:2},2),'application/json'));$('import').addEventListener('click',()=>$('import-file').click());
$('import-file').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>131072)throw new Error('Arquivo maior que 128 KiB.');const saved=JSON.parse(await file.text()),checked=await api('/api/analyze',saved.graph||saved);remember();graph=checked.graph;presetKey=meta.presets.some(p=>p.id===saved.presetKey)?saved.presetKey:'custom';$('preset').value=presetKey;selected=null;inputDraft=null;inputDirty=false;await refresh({arrange:saved.layoutVersion!==2});setView('circuit');}catch(e){notify(e.message);}finally{event.target.value='';}});
$('new').addEventListener('click',()=>edit(g=>{g.nodes=[];g.edges=[];presetKey='custom';$('preset').value='custom';selected=null;}));
async function undo(redo=false){const from=redo?redoStack:undoStack,to=redo?undoStack:redoStack;if(!from.length)return;if(job)await cancelJob();to.push(snapshot());graph=from.pop();selected=null;inputDraft=null;inputDirty=false;await refresh();}
$('undo').addEventListener('click',()=>undo());$('redo').addEventListener('click',()=>undo(true));document.addEventListener('keydown',event=>{if(event.target.matches('input,textarea,select,[contenteditable]'))return;if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();undo(event.shiftKey);}if(event.key==='Delete'&&selected?.kind==='edge')edit(g=>{g.edges.splice(selected.index,1);selected=null;});});

async function init() {
  try{
    meta=await api('/api/meta');$('machine').textContent=meta.cpu.name.replaceAll('(R)','');
    const groups={};meta.presets.forEach(p=>(groups[p.group]??=[]).push(p));$('preset').innerHTML=Object.entries(groups).map(([group,items])=>`<optgroup label="${esc(group)}">${items.map(p=>`<option value="${p.id}">${esc(p.title)}</option>`).join('')}</optgroup>`).join('')+'<option value="custom">Circuito personalizado</option>';
    $('reference-mode').innerHTML=Object.entries(meta.references).map(([key,r])=>`<option value="${key}">${esc(r.title)}</option>`).join('');$('run-core').innerHTML='<option value="-1">Agendador do sistema</option>'+meta.cpu.cores.map(c=>`<option value="${c}">CPU lógica ${c}</option>`).join('');$('run-core').value=meta.cpu.cores[0];
    renderPalette();let saved;try{saved=JSON.parse(localStorage.getItem(STORE)||'null');}catch{}
    if(saved?.graph){try{const checked=await api('/api/analyze',saved.graph);graph=checked.graph;presetKey=meta.presets.some(p=>p.id===saved.presetKey)?saved.presetKey:'custom';selected=graph.nodes.length?{kind:'node',id:graph.nodes[0].id}:null;$('preset').value=presetKey;await refresh({arrange:saved.layoutVersion!==2});}catch{graph=await api('/api/preset/main');selected={kind:'node',id:graph.nodes[0].id};await refresh({arrange:true});}}
    else{graph=await api('/api/preset/main');selected={kind:'node',id:graph.nodes[0].id};await refresh({arrange:true});}
    const progress=$('job-progress');document.querySelector('main').before(progress);historyControls();
  }catch(error){notify('Não foi possível iniciar o laboratório: '+error.message);}
}
init();
