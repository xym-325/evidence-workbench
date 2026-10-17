// Behavioral tests of the existing script using a minimal DOM (not visual QA).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const snapshot = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const elements = new Map();
function element(id = '') {
  const classes = new Set();
  return {id, value: '', innerHTML: '', textContent: '', checked: false,
    style: {}, dataset: {}, className: '', disabled: false,
    classList: {add: x=>classes.add(x), remove: x=>classes.delete(x), contains: x=>classes.has(x),
      toggle(x, force){ if(force === undefined) force=!classes.has(x); force?classes.add(x):classes.delete(x); }},
    addEventListener(){}, querySelectorAll(){return [];}, querySelector(){return null;},
    appendChild(){}, scrollIntoView(){}, remove(){}, click(){}};
}
for (const [, id] of html.matchAll(/\bid="([^"]+)"/g)) elements.set(id, element(id));
for(const id of ['deskSearch','deskFilter','deskRows','deskStatus','deskCount','deskPrev','deskNext','saveDecisions','synthesisStatus']) elements.set(id,element(id));
const requests=[];
const storage = new Map([['ew_tour','1']]);
const verification = {ok:true,run_id:snapshot.run_id,refs:snapshot.result.references.map(r=>({...r,verdict:'演示未核验',reason:'mock',matched_by:'未查询'})),summary:[{name:'演示未核验',count:snapshot.result.references.length}],offline:true};
const context=vm.createContext({console,URL,Blob,Date,Math,JSON,Set,Map,setTimeout,clearTimeout,
  location:{protocol:'http:'},navigator:{},
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  document:{getElementById:id=>elements.get(id)||null,querySelector:()=>null,querySelectorAll:()=>[],
    addEventListener(){},body:element('body'),createElement:()=>element()},
  window:{},
  fetch:async(url,options)=>{
    requests.push({url,body:options?JSON.parse(options.body):null});
    let data;
    if(url.endsWith('/api/examples')) data={ok:true,examples:[]};
    else if(url.endsWith('/api/example')) data={pico:'fixture',task_yaml:snapshot.task_yaml};
    else if(url.endsWith('/api/cases')) data={ok:true,cases:[]};
    else if(url.endsWith('/api/capabilities')) data={ok:true,synthesis:{available:false,note:'fixture'}};
    else if(url.endsWith('/api/health')) data={ok:true,components:[]};
    else if(url.endsWith('/api/runs')) data={ok:true,runs:[]};
    else if(url.includes('/api/run?id=')) data=snapshot;
    else if(url.endsWith('/api/verify')) data=verification;
    else if(url.endsWith('/api/review')) data=snapshot;
    else data={ok:false,error:'fixture route missing'};
    return {ok:true,json:async()=>data};
  }});
const script=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
vm.runInContext(script,context);
async function run(){
  await new Promise(r=>setTimeout(r,0));
  context.sample=snapshot;
  vm.runInContext('renderReviewResponse(sample)',context);
  assert.equal(vm.runInContext('CURRENT_RUN',context),snapshot.run_id);
  await vm.runInContext('runVerify()',context);
  assert.equal(requests.findLast(r=>r.url.endsWith('/api/verify')).body.run_id,snapshot.run_id);
  assert.ok(!elements.get('compare').innerHTML.includes('AI 编的引用'));
  vm.runInContext('resetResearch()',context);
  assert.equal(vm.runInContext('LAST_VERIFY',context),null);
  assert.equal(vm.runInContext('LAST_RESULT',context),null);
  await vm.runInContext('loadReviewRecord({run_id:sample.run_id})',context);
  assert.equal(vm.runInContext('CURRENT_RUN',context),snapshot.run_id);
  assert.equal(vm.runInContext('LAST_RESULT.topic',context),snapshot.result.topic);
  const score=vm.runInContext('computeTrust({references:[]},null,null).score',context);
  assert.equal(score,0);
  const unsafe=vm.runInContext('fulltextLinks({title:"x",record_url:"javascript:alert(1)"})',context);
  assert.ok(!unsafe.includes('javascript:'));
  assert.ok(!html.includes('id="healthApp"'));
  assert.ok(!html.includes('function renderActions'));
  context.auditSample=JSON.parse(JSON.stringify(snapshot));
  context.auditSample.result.stats={hit:100,retrieved:100,dedup:100,included:0,adopted:0,uncertain:100,excluded:0};
  context.auditSample.result.search_line='openalex：本批获取 50 条；数据库报告总匹配 未知';
  context.auditSample.result.notes=['注意：上方 MeSH 式未用于本次检索'];
  context.auditSample.result.search=[{source:'openalex',status:'已获取',query:'actual keywords <escaped>',years:'2019-2026'}];
  vm.runInContext('renderReviewResponse(auditSample)',context);
  assert.ok(elements.get('tiles2').innerHTML.includes('本批获取'));
  assert.ok(!elements.get('tiles2').innerHTML.includes('>命中<'));
  assert.ok(elements.get('p2sub').textContent.includes('未用于本次检索'));
  assert.ok(elements.get('planBox').innerHTML.includes('actual keywords &lt;escaped&gt;'));
  assert.ok(elements.get('prisma').innerHTML.includes('100 / 0 / 0'));
  assert.ok(elements.get('prisma').innerHTML.includes('非总命中'));
  context.auditSample.result.stats.provisional_adopted=5;
  context.auditSample.result.stats.adopted=5;
  context.auditSample.result.stats.strict_adopted=0;
  vm.runInContext('renderReviewResponse(auditSample)',context);
  assert.ok(elements.get('prisma').innerHTML.includes('95 / 0 / 0'));
  assert.ok(elements.get('prisma').innerHTML.includes('5 条 / 候选 5'));
  assert.ok(elements.get('tiles2').innerHTML.includes('其中待核候选'));
  context.questionMap={mode:'question_map',topic:'Topic <checked>',direct:1,background:0,pending:0,
    items:[{title:'Fixture paper',role:'direct',year:2024,reason:'Matches the question',snippet:'Exact abstract text',record_url:'https://example.org/paper'}],
    conclusion:'A literature map; certainty not assessed',method_note:'Abstract screening'};
  vm.runInContext('LAST_EVIDENCE=questionMap; renderEvidence(questionMap)',context);
  assert.ok(elements.get('evidence').innerHTML.includes('直接相关线索'));
  assert.ok(elements.get('evidence').innerHTML.includes('Matches the question'));
  assert.ok(elements.get('evidence').innerHTML.includes('https://example.org/paper'));
  assert.ok(!elements.get('evidence').innerHTML.includes('支持该说法'));
  assert.ok(!elements.get('evidence').innerHTML.includes('证据不足 / 不确定'));
  const exported=vm.runInContext('buildMarkdown()',context);
  assert.ok(exported.includes('文献与研究问题的关系'));
  assert.ok(!exported.includes('undefined'));
  vm.runInContext('rebuildQuery()',context);
  assert.ok(!elements.get('diag').innerHTML.includes('结构合理'));
  console.log('Frontend behavioral checks passed, including batch/total distinction, actual queries and pending records.');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
