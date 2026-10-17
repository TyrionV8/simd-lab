import test from 'node:test';
import assert from 'node:assert/strict';
import {descriptor,vectorMarkup,zoomAnchor,parseValues,serialize,exactFloat,highlightC} from '../web/model.js';

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
