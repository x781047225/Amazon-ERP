import {chromium,expect} from '@playwright/test';
const root=new URL('../',import.meta.url).pathname,browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'}),page=await context.newPage(),errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const req=route.request();if(req.method()==='GET')return route.continue();
  if(!req.url().endsWith('/preview'))throw new Error('Unexpected mutation blocked');
  requests.push(req.postDataJSON());return route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({detail:'浏览器检查已拦截，未发送亚马逊。'})});
 });
 const all=await(await page.request.get('http://127.0.0.1:8787/api/listings')).json();
 await page.goto('http://127.0.0.1:8787');await page.locator('button.nav-item').filter({hasText:'Listing'}).click();
 for(const row of all.listings){
  const d=await(await page.request.get('http://127.0.0.1:8787/api/listings/'+row.id)).json();await page.getByRole('button',{name:row.sku,exact:true}).click();
  for(const field of d.edit_fields.filter(f=>f.key.startsWith('item_')&&['item_dimensions','item_length_width_height','item_length_width','item_width_height','item_display_dimensions','item_weight','item_package_dimensions','item_package_weight'].includes(f.key))){
   await expect(page.locator(`[data-field="${field.key}"]`)).toBeVisible();expect(field.editable).toBe(true);
  }
  await page.getByRole('tab',{name:'其他属性',exact:true}).click();await expect(page.getByRole('button',{name:'运输',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'安全与合规',exact:true})).toHaveCount(0);await expect(page.getByRole('tab',{name:'安全与合规',exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'基本信息',exact:true}).click();
  if(row.product_type==='COSTUME_HEADWEAR'){
   const length=page.locator('[data-input-path="item_dimensions.0.length.value"] input'),weight=page.locator('[data-input-path="item_weight.0.value"] input');await length.fill(String(Number(await length.inputValue())+1));await length.blur();await weight.fill('200');await weight.blur();
   await page.locator('[data-input-path="item_weight.0.unit"] .el-select').click();await page.getByRole('option',{name:'克',exact:true}).click();
   await expect(page.locator('[data-field="item_dimensions"]')).toHaveClass(/modified/);await expect(page.locator('[data-field="item_weight"]')).toHaveClass(/modified/);await expect(page.locator('[data-field="item_package_dimensions"]')).not.toHaveClass(/modified/);
   await page.locator('[data-field="item_dimensions"]').scrollIntoViewIfNeeded();await page.screenshot({path:root+'docs/listing-product-measurements.png'});
   await page.getByRole('button',{name:'提交预览',exact:true}).click();const dialog=page.getByRole('dialog',{name:'提交预览',exact:true});await expect(dialog).toContainText('产品长宽高');await expect(dialog).toContainText('产品重量（不含包装）');await expect.poll(()=>requests.length).toBe(1);expect(Object.keys(requests[0].changes).sort()).toEqual(['item_dimensions','item_weight']);await expect(dialog).toContainText('未发送亚马逊');await dialog.getByRole('button',{name:'返回编辑',exact:true}).click();
  }
  await page.getByRole('button',{name:'返回列表',exact:true}).click();
 }
 expect(errors).toEqual([]);console.log(JSON.stringify({checks:['five product templates','product sizes and weight in basic info','package values separate','modified markers','only two requested attributes in preview','transport and safety groups removed from other attributes','dedicated safety tab retained'],pageErrors:errors,amazonWrites:0}));
}finally{await browser.close()}
