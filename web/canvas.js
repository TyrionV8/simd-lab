import {escapeHtml,labels,descriptor,vectorMarkup,zoomAnchor} from './model.js';
import {arrangeNodes} from './layout.js';

const NS='http://www.w3.org/2000/svg';
const element=(tag,attributes={})=>{const el=document.createElementNS(NS,tag);Object.entries(attributes).forEach(([k,v])=>el.setAttribute(k,v));return el;};

export class CircuitCanvas {
  constructor(root,callbacks) {
    this.root=root;this.callbacks=callbacks;this.viewport=root.querySelector('#viewport');this.world=root.querySelector('#world');
    this.wrap=root.querySelector('#world-wrap');this.nodesRoot=root.querySelector('#nodes');this.svg=root.querySelector('#wires');
    this.zoom=1;this.graph=null;this.report=null;this.selected=null;this.pending=null;this.pointer=null;this.drag=null;this.pan=null;this.space=false;this.frame=null;this.edges=[];
    this.showData=true;this.representation='number';this.registerBits=128;this.step=null;
    this.viewport.addEventListener('wheel',event=>this.wheel(event),{passive:false});
    this.viewport.addEventListener('pointerdown',event=>this.backgroundDown(event));
    document.addEventListener('pointermove',event=>this.move(event));
    document.addEventListener('pointerup',event=>this.up(event));
    document.addEventListener('keydown',event=>{
      if(event.target.matches('input,textarea,select,button,a,[contenteditable]'))return;
      if(event.code==='Space'){event.preventDefault();this.space=true;this.viewport.classList.add('pan-ready');}
      if(event.key==='Escape')this.cancelConnection();
    });
    document.addEventListener('keyup',event=>{if(event.code==='Space'){this.space=false;this.viewport.classList.remove('pan-ready');}});
    this.viewport.addEventListener('dragover',event=>{if(event.dataTransfer.types.includes('application/x-simd-block')){event.preventDefault();this.viewport.classList.add('dragover');}});
    this.viewport.addEventListener('dragleave',()=>this.viewport.classList.remove('dragover'));
    this.viewport.addEventListener('drop',event=>{
      event.preventDefault();this.viewport.classList.remove('dragover');const op=event.dataTransfer.getData('application/x-simd-block');
      if(op)this.callbacks.add(op,this.toWorld(event.clientX,event.clientY));
    });
    new ResizeObserver(()=>this.requestDraw()).observe(this.viewport);
  }

  toWorld(x,y) {
    const r=this.world.getBoundingClientRect();return {x:(x-r.left)/this.zoom,y:(y-r.top)/this.zoom};
  }

  setDocument(graph,report,ops) {
    this.graph=graph;this.report=report;this.ops=ops;this.render();
  }

  render() {
    if(!this.graph)return;
    this.nodesRoot.innerHTML=this.graph.nodes.map(node=>{
      const op=this.ops[node.op],data=this.report?.nodes[node.id],value=data?.outputs.value;
      const port=(name,direction)=>`<button class="port ${direction}" data-node="${node.id}" data-port="${name}" data-dir="${direction}" aria-label="${direction==='out'?'Saída':'Entrada'} ${escapeHtml(labels[name]||name)} de ${escapeHtml(op.title)}"><i></i><span>${escapeHtml(labels[name]||name)}</span></button>`;
      const input=Object.keys(op.inputs).map(name=>port(name,'in')).join('');
      const output=Object.keys(op.outputs).map(name=>port(name,'out')).join('');
      const hardware=node.params.isa?labels[node.params.isa]:node.op==='f16c'?'F16C + AVX':node.op==='dot_abs'||node.op==='dot_lookup'?'SSSE3':node.op==='dot_int16'?'SSE2':'C escalar';
      const payload=data?.error?`<p class="node-error">${escapeHtml(data.error)}</p>`:value?vectorMarkup(value,{representation:this.representation,bits:this.registerBits,dense:true}):'<p class="muted">Conecte as entradas deste bloco.</p>';
      const implementation=data?.implementation;
      const functions=Object.keys(implementation?.functions||{});
      return `<article class="node ${value?.count===1?'scalar':''}" data-node="${node.id}" style="left:${node.x}px;top:${node.y}px"><header class="node-header"><div><span class="node-id">${node.id}</span><span class="node-isa">${escapeHtml(hardware)}</span></div><h3>${escapeHtml(op.title)}</h3></header><div class="node-ports"><div>${input}</div><div>${output}</div></div><div class="node-payload">${payload}</div><footer class="node-function"><span>${escapeHtml(implementation?.instructions||op.instructions||'Operação C sobre os valores')}</span><button data-code-node="${node.id}" title="Ver função e código deste bloco">${escapeHtml(functions.length?functions.join(' + '):'Ver C do bloco')} ↗</button></footer></article>`;
    }).join('');
    this.nodesRoot.querySelectorAll('.node').forEach(el=>{
      el.addEventListener('click',event=>{
        if(event.target.closest('.port,[data-code-node]')||this.moved)return;
        const index=event.target.closest('[data-element]')?.dataset.element;
        this.callbacks.select({kind:'node',id:el.dataset.node,element:index===undefined?null:Number(index)});
      });
      el.querySelector('.node-header').addEventListener('pointerdown',event=>{
        if(event.button!==0||this.space)return;
        event.preventDefault();event.stopPropagation();const node=this.graph.nodes.find(n=>n.id===el.dataset.node);
        this.callbacks.beginMove?.();this.drag={id:node.id,x:node.x,y:node.y,clientX:event.clientX,clientY:event.clientY};this.moved=false;
        this.callbacks.select({kind:'node',id:node.id});
      });
    });
    this.nodesRoot.querySelectorAll('.port').forEach(port=>{
      port.addEventListener('pointerdown',event=>{if(event.button===0){event.preventDefault();event.stopPropagation();this.portAction(port,event.clientX,event.clientY);}});
      port.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();const r=port.getBoundingClientRect();this.portAction(port,r.x+r.width/2,r.y+r.height/2);}});
    });
    this.nodesRoot.querySelectorAll('[data-code-node]').forEach(button=>button.addEventListener('click',event=>{event.stopPropagation();this.callbacks.code(button.dataset.codeNode);}));
    this.root.querySelector('#empty').hidden=this.graph.nodes.length>0;
    this.resize();this.createWires();this.setSelected(this.selected);this.setStep(this.step);this.applyDataVisibility();
  }

  resize() {
    let width=100,height=100;
    this.graph.nodes.forEach(node=>{const el=this.nodesRoot.querySelector(`.node[data-node="${node.id}"]`);width=Math.max(width,node.x+el.offsetWidth+32);height=Math.max(height,node.y+el.offsetHeight+32);});
    this.width=width;this.height=height;this.world.style.width=width+'px';this.world.style.height=height+'px';this.applyTransform();
  }

  applyTransform() {
    this.world.style.transform=`scale(${this.zoom})`;this.wrap.style.width=this.width*this.zoom+'px';this.wrap.style.height=this.height*this.zoom+'px';
    this.callbacks.zoom(this.zoom);
  }

  setZoom(value,clientX,clientY) {
    const r=this.viewport.getBoundingClientRect(),x=clientX===undefined?r.width/2:clientX-r.left,y=clientY===undefined?r.height/2:clientY-r.top;
    const next=Math.max(.035,Math.min(2,value));const anchor=zoomAnchor(this.zoom,next,this.viewport.scrollLeft,this.viewport.scrollTop,x,y);
    this.zoom=next;this.applyTransform();this.viewport.scrollLeft=anchor.left;this.viewport.scrollTop=anchor.top;this.requestDraw();
  }

  wheel(event) {event.preventDefault();this.viewport.scrollTo({left:this.viewport.scrollLeft,top:this.viewport.scrollTop,behavior:'instant'});this.setZoom(this.zoom*Math.exp(-event.deltaY*.0015),event.clientX,event.clientY);}

  backgroundDown(event) {
    if(event.button!==1&&!this.space&&event.target.closest('.node,.wire-label,.wire-hit'))return;
    if(event.button!==0&&event.button!==1)return;
    event.preventDefault();this.pan={x:event.clientX,y:event.clientY,left:this.viewport.scrollLeft,top:this.viewport.scrollTop};this.viewport.classList.add('panning');
  }

  move(event) {
    if(this.pan){this.viewport.scrollLeft=this.pan.left+this.pan.x-event.clientX;this.viewport.scrollTop=this.pan.top+this.pan.y-event.clientY;}
    if(this.drag){
      const node=this.graph.nodes.find(n=>n.id===this.drag.id),dx=(event.clientX-this.drag.clientX)/this.zoom,dy=(event.clientY-this.drag.clientY)/this.zoom;
      node.x=Math.max(16,this.drag.x+dx);node.y=Math.max(16,this.drag.y+dy);this.moved ||= Math.abs(dx)+Math.abs(dy)>3;
      const el=this.nodesRoot.querySelector(`[data-node="${node.id}"]`);el.style.left=node.x+'px';el.style.top=node.y+'px';this.resize();this.requestDraw();
    }
    if(this.pending){this.pointer={x:event.clientX,y:event.clientY};this.requestDraw();}
  }

  up(event) {
    if(this.drag){this.callbacks.moved(this.moved);this.drag=null;setTimeout(()=>this.moved=false,50);}
    this.pan=null;this.viewport.classList.remove('panning');
    if(this.pending){const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('.port.in');if(target)this.connectTo(target);}
  }

  portAction(port,x,y) {
    if(port.dataset.dir==='in'){if(this.pending)this.connectTo(port);else this.callbacks.select({kind:'node',id:port.dataset.node});return;}
    this.pending={from:port.dataset.node,out:port.dataset.port};this.pointer={x,y};
    this.callbacks.select({kind:'node',id:port.dataset.node,port:port.dataset.port,keepInspectorState:true});this.callbacks.connecting(true);this.nodesRoot.classList.add('connecting');this.requestDraw();
  }

  connectTo(port) {const link={...this.pending,to:port.dataset.node,input:port.dataset.port};this.cancelConnection();this.callbacks.connect(link);}
  cancelConnection(){this.pending=null;this.pointer=null;this.nodesRoot.classList.remove('connecting');this.callbacks.connecting(false);this.requestDraw();}

  createWires() {
    this.svg.replaceChildren();this.edges=[];
    for(const [index,edge] of this.graph.edges.entries()) {
      const path=element('path',{class:'wire'}),hit=element('path',{class:'wire-hit'}),label=element('g',{class:'wire-label',role:'button',tabindex:'0','aria-label':`Dados da conexão ${edge.from}.${edge.out} para ${edge.to}.${edge.input}`});
      const value=this.report?.nodes[edge.from]?.outputs[edge.out];
      const info=value?descriptor(value):null;
      label.append(element('rect',{width:134,height:40,rx:6}));
      const title=element('text',{x:9,y:15}),count=element('text',{x:9,y:31,class:'wire-description'});
      title.textContent=info?`${info.count} × ${info.bits} bits`:'Conexão sem valor';
      count.textContent=info?(info.packed?`${value.count} bytes · ${info.count} pesos`:`${info.name} · ${value.count} valores`):'Conecte as entradas';label.append(title,count);
      const select=()=>this.callbacks.select({kind:'edge',index});hit.addEventListener('click',select);label.addEventListener('click',select);
      label.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select();}});
      this.svg.append(hit,path,label);this.edges.push({edge,path,hit,label});
    }
    this.draft=element('path',{class:'wire-draft'});this.svg.append(this.draft);this.requestDraw();
  }

  point(id,port,direction) {
    const el=this.nodesRoot.querySelector(`.port[data-node="${id}"][data-port="${port}"][data-dir="${direction}"] i`);if(!el)return null;
    const r=el.getBoundingClientRect();return this.toWorld(r.x+r.width/2,r.y+r.height/2);
  }

  path(a,b) {const bend=Math.max(60,Math.abs(b.x-a.x)*.45);return `M ${a.x} ${a.y} C ${a.x+bend} ${a.y}, ${b.x-bend} ${b.y}, ${b.x} ${b.y}`;}
  requestDraw(){if(this.frame)return;this.frame=requestAnimationFrame(()=>{this.frame=null;this.draw();});}
  draw() {
    for(const {edge,path,hit,label} of this.edges) {
      const a=this.point(edge.from,edge.out,'out'),b=this.point(edge.to,edge.input,'in');if(!a||!b)continue;
      const d=this.path(a,b);path.setAttribute('d',d);hit.setAttribute('d',d);label.setAttribute('transform',`translate(${(a.x+b.x)/2-67},${(a.y+b.y)/2-20})`);
    }
    const a=this.pending?this.point(this.pending.from,this.pending.out,'out'):null;
    this.draft?.setAttribute('d',a&&this.pointer?this.path(a,this.toWorld(this.pointer.x,this.pointer.y)):'');
  }

  setSelected(selected) {
    this.selected=selected;const ids=selected?.kind==='node'?[selected.id]:selected?.kind==='edge'?[this.graph.edges[selected.index]?.from,this.graph.edges[selected.index]?.to]:[];
    this.nodesRoot.querySelectorAll('.node').forEach(el=>el.classList.toggle('selected',ids.includes(el.dataset.node)));
    this.edges.forEach(({path,label},i)=>{const active=selected?.kind==='edge'&&selected.index===i;path.classList.toggle('selected',active);label.classList.toggle('selected',active);});
  }

  setStep(step) {
    this.step=step;
    if(!this.report)return;
    const current=step===null?null:this.report.order[step];
    this.nodesRoot.querySelectorAll('.node').forEach(el=>{const index=this.report.order.indexOf(el.dataset.node);el.classList.toggle('pending',step!==null&&index>step);el.classList.toggle('current',el.dataset.node===current);});
    this.edges.forEach(({edge,path})=>path.classList.toggle('active',edge.from===current));
  }

  applyDataVisibility(){this.nodesRoot.classList.toggle('compact',!this.showData);this.resize();this.requestDraw();}
  configure({showData=this.showData,representation=this.representation,registerBits=this.registerBits}){this.showData=showData;this.representation=representation;this.registerBits=registerBits;this.render();}

  focus(id,{smooth=false}={}) {
    const node=this.graph.nodes.find(n=>n.id===id);if(!node)return;
    const width=this.nodesRoot.querySelector(`.node[data-node="${id}"]`).offsetWidth;
    this.viewport.scrollTo({left:Math.max(0,node.x*this.zoom-(this.viewport.clientWidth-width*this.zoom)/2),top:Math.max(0,node.y*this.zoom-35),behavior:smooth&&!matchMedia('(prefers-reduced-motion: reduce)').matches?'smooth':'instant'});
  }

  fit() {this.setZoom(Math.min((this.viewport.clientWidth-30)/this.width,(this.viewport.clientHeight-30)/this.height));this.viewport.scrollTo(0,0);}

  arrange() {
    const sizes=new Map([...this.nodesRoot.children].map(el=>[el.dataset.node,{width:el.offsetWidth,height:el.offsetHeight}]));
    const positions=arrangeNodes(this.graph.nodes,this.graph.edges,sizes);
    for(const node of this.graph.nodes) {
      Object.assign(node,positions.get(node.id));const el=this.nodesRoot.querySelector(`.node[data-node="${node.id}"]`);
      el.style.left=node.x+'px';el.style.top=node.y+'px';
    }
    this.resize();this.requestDraw();this.callbacks.moved(true);
  }
}
