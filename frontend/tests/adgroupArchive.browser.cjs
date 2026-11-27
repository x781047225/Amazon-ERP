// Run from repository root with Vite on 127.0.0.1:5174. All business requests are mocked.
const {chromium,expect}=require('@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const page=await browser.newPage({viewport:{width:1600,height:1000}});
 page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(10000);
 const errors=[],calls=[],previews=new Map();let archived=false;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const req=route.request(),p=new URL(req.url()).pathname,b=req.postDataJSON();calls.push({p,b});let d={};
  if(p.endsWith('/profiles'))d={profiles:[{id:'test',account:'Demo',country:'UK',currency:'GBP'}]};
  else if(p.endsWith('/workspace'))d={rows:archived?[]:[{key:'test:adgroup:g1',id:'g1',kind:'adgroup',profile_id:'test',campaign_id:'c1',campaign_name:'模拟活动',name:'模拟广告组',state:'ENABLED',default_bid:.3,currency:'GBP',targeting_type:'AUTO'}],total:archived?0:1,currency:'GBP',metrics_ready:true};
  else if(p.endsWith('/jobs'))d=[];
  else if(p.endsWith('/campaign-refresh'))d={status:'succeeded',updated_at:'2026-11-28T01:00:00',includes_adgroups:true};
  else if(p.endsWith('/edit/preview')){d={id:'preview-'+previews.size,changes:b.changes,before:{state:'ENABLED',name:'模拟广告组',defaultBid:.3}};previews.set(d.id,d)}
  else if(p.endsWith('/edit/confirm')){assert.equal(b.acknowledge_archive,true);archived=true;d={status:'succeeded',message:'模拟归档成功'}}
  else if(p.endsWith('/edit/bulk-submit'))d={id:b.request_id,status:'succeeded',items:b.ids.map(id=>({id,status:'succeeded'}))};
  await route.fulfill({json:d});
 });
 await page.goto('http://127.0.0.1:5174/tests/adgroupArchive.html');
 const batch=page.getByRole('button',{name:'批量调整状态',exact:true});await expect(batch).toBeVisible();await expect(batch).toBeDisabled();
 await page.getByRole('button',{name:'编辑',exact:true}).click();
 await page.getByLabel('广告组名称',{exact:true}).fill('未提交名称');
 await page.locator('.el-select').filter({has:page.getByRole('combobox',{name:'广告组状态',exact:true})}).click();await page.getByRole('option',{name:'归档',exact:true}).click();
 await expect(page.getByLabel('广告组名称',{exact:true})).toBeDisabled();
 await expect(page.getByLabel('广告组默认竞价',{exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'保存',exact:true}).click();
 assert.deepEqual(calls.filter(c=>c.p.endsWith('/edit/preview')).at(-1).b.changes,{state:'ARCHIVED'});
 await page.getByRole('button',{name:'确认并提交亚马逊',exact:true}).click();
 const box=page.locator('.el-message-box');await expect(box).toBeVisible();
 await box.getByRole('button',{name:'取消',exact:true}).click();
 assert.equal(calls.filter(c=>c.p.endsWith('/edit/confirm')).length,0);await expect(box).toBeHidden();console.log('PASS cancellation');
 console.log('Capturing preview');await page.screenshot({path:'docs/qa/adgroup-archive-preview.png',fullPage:true,timeout:10000});console.log('Captured preview');
 await page.getByRole('button',{name:'确认并提交亚马逊',exact:true}).click();
 await box.getByRole('button',{name:'确认归档，无法撤回',exact:true}).click();
 await expect(page.getByText('模拟归档成功',{exact:true})).toBeVisible();
 assert.equal(calls.filter(c=>c.p.endsWith('/edit/confirm')).length,1);
 console.log('PASS single confirm');archived=false;await page.reload();console.log('Reloaded for batch');
 await page.locator('.ads-table .el-table__body-wrapper label.el-checkbox').first().click();await expect(batch).toBeEnabled();console.log('Selected group');await batch.click();
 const dlg=page.getByRole('dialog');await expect(dlg.getByText('已选择 1 个广告组',{exact:true})).toBeVisible();await dlg.locator('label.el-radio').filter({hasText:'归档'}).click();
 await dlg.getByRole('button',{name:'预览修改',exact:true}).click();
 assert.equal(calls.filter(c=>c.p.endsWith('/edit/preview')).at(-1).b.kind,'adgroup');
 await dlg.getByRole('button',{name:'确认提交 1 项',exact:true}).click();
 await expect(box).toBeVisible();await page.screenshot({path:'docs/qa/adgroup-archive-bulk.png',fullPage:true,animations:'disabled'});
 await box.getByRole('button',{name:'取消',exact:true}).click();
 assert.equal(calls.filter(c=>c.p.endsWith('/edit/bulk-submit')).length,0);
 await dlg.getByRole('button',{name:'确认提交 1 项',exact:true}).click();
 await box.getByRole('button',{name:'确认归档，无法撤回',exact:true}).click();
 await expect(page.getByText('批量调整完成',{exact:true})).toBeVisible();
 assert.equal(calls.filter(c=>c.p.endsWith('/edit/bulk-submit')).length,1);
 assert.equal(calls.filter(c=>c.p.endsWith('/edit/bulk-submit'))[0].b.acknowledge_archive,true);

 console.log('ERRORS',errors);assert.deepEqual(errors,[]);
 await browser.close();console.log('PASS: single archive preview, disabled unrelated fields, cancellation without submit, confirmed single submission, batch archive preview, cancellation and acknowledged submission.');
})().catch(e=>{console.error(e);process.exit(1)});
