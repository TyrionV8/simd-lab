import test from 'node:test';
import assert from 'node:assert/strict';
import {descriptor,vectorMarkup,zoomAnchor,parseValues,serialize,exactFloat,highlightC,floatText,vectorLayout} from '../web/model.js';
import {arrangeNodes} from '../web/layout.js';

test('zoom mantém o ponto sob o cursor',()=>{
  const p=zoomAnchor(.8,1.25,300,100,250,200);
  assert.equal((p.left+250)/1.25,(300+250)/.8);
  assert.equal((p.top+200)/1.25,(100+200)/.8);
});
test('tipo genérico representa números, bits e família',()=>{
  const v=descriptor({kind:'f32',count:64,bytes:256});
  assert.equal(v.lanes,4);assert.equal(v.groups,16);assert.equal(v.summary,'64 números × 32 bits · floating point · ponto flutuante');
  assert.equal(descriptor({kind:'p4u',count:32,bytes:32}).count,64);
});
test('nenhum elemento é omitido na visualização',()=>{
  const value={kind:'f32',count:64,elementBytes:4,bytes:256,values:Array.from({length:64},(_,i)=>i),decimal:Array.from({length:64},(_,i)=>String(i)),bits:Array(64).fill('0'.repeat(32)),hex:Array(64).fill('00 00 00 00')};
  const html=vectorMarkup(value);
  assert.equal((html.match(/data-element=/g)||[]).length,64);
  assert.ok(html.includes('data-element="63"'));assert.ok(!html.includes('…'));
});
test('CSV e vírgulas decimais têm regras explícitas',()=>{
  assert.deepEqual(parseValues('1,2,3\n4'),[1,2,3,4]);
  assert.deepEqual(parseValues('1,25; -2,5',true),[1.25,-2.5]);
  assert.deepEqual(parseValues('[1,2,3]'),[1,2,3]);
  assert.throws(()=>parseValues('1;inválido'));
  assert.throws(()=>parseValues('["1"]'));
});
test('zero negativo é preservado em API, arquivo e armazenamento',()=>{
  const data=JSON.parse(serialize({values:[-0,0,1]}));
  assert.ok(Object.is(data.values[0],-0));assert.ok(!Object.is(data.values[1],-0));
});
test('valor decimal exato é derivado dos bits FP32',()=>{
  assert.equal(exactFloat(Math.fround(.1)),'0.100000001490116119384765625');
  assert.equal(exactFloat(-0),'-0');assert.equal(exactFloat(1),'1');
});
test('código C é destacado sem interpretar HTML',()=>{
  const html=highlightC('float x = 1; /* <script> */');
  assert.ok(html.includes('token-keyword'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(!html.includes('<script>'));
});

test('decimais nativos compactos preservam os mesmos bits FP32',()=>{
  assert.equal(floatText(-8),'-8');assert.equal(floatText(1),'1');assert.equal(floatText(-0),'-0');
  const view=new DataView(new ArrayBuffer(4));
  for(let raw=0;raw<0x100000000;raw+=104729) {
    view.setUint32(0,raw);const value=view.getFloat32(0);
    if(Number.isFinite(value))assert.ok(Object.is(Math.fround(Number(floatText(value))),value));
  }
  for(const raw of [1,0x007fffff,0x00800000,0x7f7fffff,0x80000001,0xff7fffff]) {
    view.setUint32(0,raw);const value=view.getFloat32(0);
    assert.ok(Object.is(Math.fround(Number(floatText(value))),value));
  }
});

test('a grade compacta mantém todos os índices e os limites de cada grupo',()=>{
  const value={kind:'f32',count:64,elementBytes:4,bytes:256,values:Array(64).fill(1)};
  const html=vectorMarkup(value,{dense:true});
  assert.equal((html.match(/data-element=/g)||[]).length,64);
  assert.equal((html.match(/class="register-group"/g)||[]).length,16);
  assert.ok(html.includes('posições 60 a 63'));
  assert.ok(vectorLayout(value,'number',128,true).width<170);
  assert.ok(vectorLayout({...value,values:Array(64).fill(123456789)},'number',128,true).width>300);
  const int8={...value,kind:'i8',elementBytes:1,bytes:64};
  assert.equal(vectorLayout(int8,'number',128,true).columns,8);
  assert.equal(vectorLayout(int8,'bits',128,true).columns,4);
});

test('organização aproxima entradas tardias e respeita o tamanho real de cada bloco',()=>{
  const nodes=['weights','x','scale','unpack','convert','dot','multiply'].map(id=>({id}));
  const edges=[['weights','unpack'],['unpack','convert'],['convert','dot'],['x','dot'],['dot','multiply'],['scale','multiply']].map(([from,to])=>({from,to}));
  const sizes=new Map(nodes.map((node,i)=>[node.id,{width:300+i*10,height:200+i*50}]));
  const positions=arrangeNodes(nodes,edges,sizes);
  for(const edge of edges)assert.ok(positions.get(edge.from).x+sizes.get(edge.from).width<positions.get(edge.to).x);
  assert.equal(positions.get('x').x,positions.get('convert').x);
  assert.equal(positions.get('scale').x,positions.get('dot').x);
  for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++) {
    const a=positions.get(nodes[i].id),b=positions.get(nodes[j].id),sa=sizes.get(nodes[i].id),sb=sizes.get(nodes[j].id);
    assert.ok(a.x+sa.width<=b.x||b.x+sb.width<=a.x||a.y+sa.height<=b.y||b.y+sb.height<=a.y);
  }
});
