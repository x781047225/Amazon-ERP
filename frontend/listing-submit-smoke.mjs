import {chromium,expect} from '@playwright/test';
const root=new URL('../',import.meta.url).pathname;
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1600,height:1050},locale:'zh-CN'}),page=await context.newPage(),errors=[],posts=[];
 page.on('pageerror',e=>errors.push(e.message));
 const all=await(await page.request.get('http://127.0.0.1:8787/api/listings')).json(),details=[];
 for(const row of all.listings)details.push(await(await page.request.get('http://127.0.0.1:8787/api/listings/'+row.id)).json());
 let preview=null,lastChanges=null,submitted=0;
 // Fail closed: every POST is intercepted. This smoke test cannot send an Amazon write.
 await page.route('**/api/**',async route=>{
  const req=route.request(),url=new URL(req.url());if(req.method()==='GET')return route.continue();
  posts.push(url.pathname);const pk=Number(url.pathname.split('/')[3]),d=details.find(x=>x.id===pk);
  const reply=x=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(x)});
  if(url.pathname.endsWith('/price/refresh'))return reply(d.pricing);
  if(url.pathname.endsWith('/preview')){
   const body=req.postDataJSON();lastChanges=body;
   preview={preview_id:'11111111-1111-4111-8111-111111111111',can_submit:true,expires_at:new Date(Date.now()+900000).toISOString(),store:d.store,marketplace:d.marketplace,sku:d.sku,counts:{},warnings:[],issues:[],diff:[]};
   if(url.pathname.endsWith('/price/preview'))preview.diff=[{key:'purchasable_offer',label:'售价与促销',before:{售价:d.pricing.price},after:{售价:body.price},schema:null}];
   else preview.diff=Object.entries(body.changes).map(([key,value])=>{const f=d.edit_fields.find(x=>x.key===key);return {key,label:f.label,before:f.value,after:value,schema:f.schema}});
   return reply(preview);
  }
  if(url.pathname.endsWith('/submit')){expect(req.postDataJSON().confirmed).toBe(true);submitted++;return reply({id:preview.preview_id,listing_pk:pk,status:'verified',kind:preview.diff[0].key==='purchasable_offer'?'price':'attributes',message:'模拟回读已确认（测试未发送亚马逊）',created_at:new Date().toISOString(),checked_at:new Date().toISOString(),proposed:{},before:{},response:{issues:[]}})}
  throw new Error('Unexpected mutation blocked: '+url.pathname);
 });
 await page.goto('http://127.0.0.1:8787');await page.locator('button.nav-item').filter({hasText:'Listing'}).click();await expect(page.locator('.listing-product')).toHaveCount(5);
 await expect(page.getByRole('combobox',{name:'店铺筛选'})).toBeVisible();await expect(page.getByRole('combobox',{name:'站点筛选'})).toBeVisible();
 expect(await page.locator('.filters').innerText()).toContain('今日销量');expect(await page.locator('.filters').innerText()).toContain('降序');expect(await page.locator('.filters').innerText()).not.toContain('全部产品类型');
 await page.locator('.el-select').filter({has:page.getByRole('combobox',{name:'销量排序方向'})}).click();await page.getByRole('option',{name:'升序',exact:true}).click();await expect.poll(()=>page.locator('.listing-product .order-link').allTextContents()).toEqual((await(await page.request.get('http://127.0.0.1:8787/api/listings?sort_order=asc')).json()).listings.map(x=>x.sku));
 await page.getByRole('button',{name:'重置',exact:true}).click();await page.screenshot({path:root+'docs/listing-submit-list.png',fullPage:true});
 const chosen=details.find(d=>!d.attributes.title_differentiation?.length)||details[0];
 await page.getByRole('button',{name:chosen.sku,exact:true}).click();await expect(page.getByRole('button',{name:'提交预览',exact:true})).toBeDisabled();
 for(const d of details){
  await page.getByRole('button',{name:'返回列表',exact:true}).click();await page.getByRole('button',{name:d.sku,exact:true}).click();
  const enabled=d.edit_fields.filter(f=>f.editable&&f.section==='basic');
  for(const f of enabled)await expect(page.locator(`[data-field="${f.key}"]`)).toBeVisible();
  await expect(page.locator('[data-field="bullet_point"] textarea')).toHaveCount(5);
  await page.getByRole('tab',{name:'安全与合规',exact:true}).click();for(const f of d.edit_fields.filter(f=>f.section==='compliance'))await expect(page.locator(`[data-field="${f.key}"]`)).toBeVisible();
  await page.getByRole('tab',{name:'基本信息',exact:true}).click();
 }
 await page.getByRole('button',{name:'返回列表',exact:true}).click();await page.getByRole('button',{name:chosen.sku,exact:true}).click();
 const highlight=page.locator('[data-field="title_differentiation"] textarea').first(),text='Test highlight retained only in this browser session';await highlight.fill(text);
 await page.getByRole('button',{name:'返回列表',exact:true}).click();await page.getByRole('button',{name:chosen.sku,exact:true}).click();await expect(highlight).toHaveValue(text);
 await page.locator('button.nav-item').filter({hasText:'订单中心'}).click();await page.locator('button.nav-item').filter({hasText:'Listing'}).click();await expect(highlight).toHaveValue(text);
 await page.getByRole('button',{name:'提交预览',exact:true}).click();const dialog=page.getByRole('dialog',{name:'提交预览',exact:true});await expect(dialog).toBeVisible();expect(submitted).toBe(0);expect(Object.keys(lastChanges.changes)).toEqual(['title_differentiation']);await expect(dialog).toContainText(text);
 await page.waitForTimeout(450);await dialog.screenshot({path:root+'docs/listing-submit-preview.png'});await dialog.getByRole('button',{name:'返回编辑',exact:true}).click();await expect(highlight).toHaveValue(text);
 await page.getByRole('button',{name:'提交预览',exact:true}).click();await dialog.getByRole('button',{name:'确认提交',exact:true}).click();await expect(dialog).toContainText('模拟回读已确认');expect(submitted).toBe(1);await dialog.getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByRole('button',{name:'返回列表',exact:true}).click();await page.getByRole('button',{name:'调价',exact:true}).first().click();const price=page.getByRole('dialog',{name:'调价',exact:true});await expect(price.getByLabel('售价',{exact:true})).toBeEnabled();
 await price.getByLabel('售价',{exact:true}).fill('20');await price.getByLabel('促销价',{exact:true}).fill('10');
 // Remove dates in case this SKU already has a promotion.
 const range=price.locator('.el-date-editor');await range.hover();const clear=range.locator('.el-range__close-icon');if(await clear.isVisible())await clear.click();
 await price.getByRole('button',{name:'提交调价',exact:true}).click();await expect(price).toContainText('必须填写开始和结束时间');
 await price.getByLabel('促销价',{exact:true}).fill('');await price.getByRole('button',{name:'提交调价',exact:true}).click();await expect(dialog).toBeVisible();expect(submitted).toBe(1);await dialog.getByRole('button',{name:'确认提交',exact:true}).click();await expect(dialog).toContainText('模拟回读已确认');expect(submitted).toBe(2);
 await dialog.getByRole('button',{name:'关闭',exact:true}).click();await price.getByRole('button',{name:'关闭',exact:true}).click();
 await page.setViewportSize({width:1280,height:850});await page.screenshot({path:root+'docs/listing-submit-1280.png',fullPage:true});
 expect(errors).toEqual([]);expect(posts.some(x=>x.includes('draft'))).toBe(false);
 console.log(JSON.stringify({checks:['filters and sort','5 templates and empty highlight','five bullets','structured and compliance fields','front edits retained across navigation','changed fields only','preview does not submit','explicit confirmation','price dates required','price direct confirmation','1280 layout'],mockedPosts:posts,pageErrors:errors}));
}finally{await browser.close()}
