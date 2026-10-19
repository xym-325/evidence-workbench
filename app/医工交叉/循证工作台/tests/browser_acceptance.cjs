// Real browser checks against the local service. No model calls are made here.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');
const [base,runId,out]=process.argv.slice(2);
let activeBrowser;
async function main(){
  fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  activeBrowser=browser;
  const context=await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
  const page=await context.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base,{waitUntil:'networkidle'});
  await page.waitForFunction(()=>document.getElementById('pico').value.length>0);
  assert.equal(await page.locator('.workflow-nav').count(),1);
  assert.equal(await page.locator('#synthesisPanel').count(),0);
  assert.equal(await page.getByRole('button',{name:'AI 生成中文综述初稿',exact:true}).count(),0);
  await page.screenshot({path:path.join(out,'01-研究入口.png')});
  await page.evaluate(id=>loadReviewRecord({run_id:id}),runId);
  await page.locator('#resultOverview').waitFor({state:'visible'});
  assert.equal(await page.locator('#resultOverview .metric').count(),5);
  assert.ok(await page.locator('#reviewSections details.sec').count()>0);
  await page.locator('#resultOverview').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,'02-结果概览.png')});
  await page.locator('#deskSearch').fill('SGDA');
  assert.ok(await page.locator('.paper-row').count()>=1);
  assert.ok((await page.locator('#deskRows').innerText()).includes('SGDA'));
  await page.locator('#deskSearch').fill('');
  await page.locator('#literatureDesk').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,'03-文献工作区.png')});
  await page.locator('#reviewSections > details.sec').first().locator(':scope > summary').click();
  await page.locator('#reviewSections').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,'04-原有综述与引用.png')});
  const downloadPromise=page.waitForEvent('download');
  await page.evaluate(()=>downloadReview());
  const download=await downloadPromise;
  const downloaded=path.join(out,'浏览器导出.md');await download.saveAs(downloaded);
  const markdown=fs.readFileSync(downloaded,'utf8');
  assert.ok(markdown.includes('参考文献'));
  assert.ok(!markdown.includes('undefined'));
  const zipPromise=page.waitForEvent('download');
  await page.evaluate(()=>downloadEvidenceBundle());
  const zipped=await zipPromise;const zipPath=path.join(out,'浏览器导出证据包.zip');await zipped.saveAs(zipPath);
  assert.equal(fs.readFileSync(zipPath).subarray(0,2).toString(),'PK');
  // Manual decision saves a child; original draft remains recoverable.
  await page.locator('#deskRows .paper-row').first().locator('[data-decision="excluded"]').click();
  await page.locator('#deskRows input[data-reason]').fill('浏览器验收：人工标为待进一步复查的邻近任务，原稿保留。');
  assert.equal(await page.locator('#saveDecisions').isEnabled(),true);
  await page.locator('#saveDecisions').click();
  await page.waitForFunction(id=>CURRENT_RUN&&CURRENT_RUN!==id,runId);
  const childId=await page.evaluate(()=>CURRENT_RUN);
  const child=await (await page.request.get(base+'/api/run?id='+childId)).json();
  assert.equal(child.result.parent_run_id,runId);
  assert.equal(child.verification,null);
  await page.evaluate(id=>loadReviewRecord({run_id:id}),runId);
  if(await page.locator('#caseButtons button').count()){
    await page.locator('#caseButtons button').first().click();
    await page.locator('.replay-banner').waitFor({state:'visible'});
    assert.ok((await page.locator('.replay-banner').innerText()).includes('未在此次重新查询'));
    await page.locator('#resultOverview').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(out,'06-真实案例回放.png')});
  }
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(out,'05-移动端入口.png')});
  const overflow=await page.evaluate(()=>({body:document.documentElement.scrollWidth,viewport:innerWidth,
    offenders:[...document.querySelectorAll('body *')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.right>innerWidth+1;}).slice(0,12).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right}))}));
  assert.ok(overflow.body<=overflow.viewport+1,JSON.stringify(overflow));
  // Confirm the ordinary UI entry reaches the real asynchronous search path.
  // This run does not call a model and explicitly disables external verification.
  await page.setViewportSize({width:1440,height:1050});
  await page.evaluate(()=>loadDefaultExample());
  await page.evaluate(()=>loadTaskSettings());
  await page.locator('#taskForm').waitFor({state:'visible'});
  await page.locator('#taskYears').fill('2020-2026');
  await page.locator('#taskMax').fill('12');
  await page.evaluate(()=>applyTaskSettings());
  assert.ok((await page.locator('#taskSettingsStatus').innerText()).includes('已更新'));
  await page.locator('#online').uncheck();
  await page.evaluate(()=>runAll());
  const live=await page.evaluate(()=>({id:CURRENT_RUN,stats:LAST_RESULT?.stats,status:document.getElementById('globalStatus').textContent}));
  assert.ok(live.id);
  assert.ok(live.stats.retrieved>0);
  assert.ok(live.stats.adopted>=5&&live.stats.adopted<=20);
  assert.ok(await page.locator('#jobEvents li').count()>0);
  await page.locator('#sqReading details').first().locator('summary').click();
  assert.ok((await page.locator('#sqReading').innerText()).includes('全文'));
  await page.screenshot({path:path.join(out,'07-实时前端检索流程.png')});
  assert.deepEqual(errors,[]);
  const report={ok:true,checks:['入口','结果概览','文献搜索','摘要依据','Markdown下载','ZIP证据包','人工复核新版本','历史恢复','明确标识的真实案例回放','390px无横向溢出','表单修改任务卡','实时前端主流程（关闭外部题录核验）','真实批次进度','全文阅读准备','无页面脚本异常'],run_id:runId,manual_child_run_id:childId,live_run:live,overflow};
  fs.writeFileSync(path.join(out,'浏览器验收.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
  await browser.close();
}
main().catch(async e=>{console.error(e);if(activeBrowser)await activeBrowser.close();process.exitCode=1;});
