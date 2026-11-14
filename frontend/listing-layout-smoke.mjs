import {chromium,expect} from '@playwright/test';
const root=new URL('../',import.meta.url).pathname;
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1600,height:1050},locale:'zh-CN',permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage(),errors=[],writes=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url())});
 async function openList(){await page.goto('http://127.0.0.1:8787');await page.locator('button.nav-item').filter({hasText:'Listing'}).click();await expect(page.locator('.listing-product')).toHaveCount(5);await expect(page.locator('.listing-table .el-loading-mask')).toBeHidden()}
 await openList();
 const all=await (await page.request.get('http://127.0.0.1:8787/api/listings')).json();
 for(let i=0;i<all.listings.length;i++){
  const row=all.listings[i],cell=page.locator('.listing-product').nth(i);
  for(const [label,value] of [['SKU',row.sku],['ASIN',row.asin],['FNSKU',row.fnsku]]){
   if(!value)throw new Error('Missing '+label);
   await cell.getByRole('button',{name:'复制 '+label,exact:true}).click();
   await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(value);
  }
 }
 const header=page.locator('.listing-table th').first();await header.scrollIntoViewIfNeeded();let rect=await header.boundingBox();const original=rect.width;
 await page.locator('.el-message').last().waitFor({state:'hidden'});
 await page.mouse.move(rect.x+rect.width-3,rect.y+rect.height/2);await page.mouse.down();await page.mouse.move(rect.x+rect.width+117,rect.y+rect.height/2,{steps:10});await page.mouse.up();
 await expect.poll(async()=>(await header.boundingBox()).width).toBeGreaterThan(original+90);
 const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem('erp.listing.column-widths.v1')).product);
 await openList();await expect.poll(async()=>(await page.locator('.listing-table th').first().boundingBox()).width).toBeCloseTo(stored,0);
 await page.getByRole('button',{name:'重置列宽',exact:true}).click();await expect.poll(()=>page.evaluate(()=>localStorage.getItem('erp.listing.column-widths.v1'))).toBeNull();
 await page.waitForTimeout(600);await page.screenshot({path:root+'docs/listing-layout-list.png',fullPage:true});
 for(const row of all.listings){
  await page.getByRole('button',{name:row.sku,exact:true}).click();
  await expect(page.locator('.identity-summary')).toContainText(row.fnsku);
  await expect(page.locator('[data-field="brand"]')).toBeVisible();
  const detail=await (await page.request.get('http://127.0.0.1:8787/api/listings/'+row.id)).json();
  await expect(page.locator('.identity-summary')).toContainText(detail.catalog_data.summaries[0].browseClassification.displayName);
  if(detail.common_fields.some(f=>f.key==='recommended_browse_nodes'))await expect(page.locator('[data-field="recommended_browse_nodes"]')).toBeVisible();
  if(detail.common_fields.some(f=>f.key==='title_differentiation'))await expect(page.locator('[data-field="title_differentiation"]')).toBeVisible();
  await page.getByRole('tab',{name:'品类属性',exact:true}).click();
  await expect(page.getByRole('button',{name:/产品识别 ·/})).toHaveCount(0);
  await page.getByRole('tab',{name:'基本信息',exact:true}).click();
  if(row.id===1){await page.waitForTimeout(400);await page.screenshot({path:root+'docs/listing-layout-detail.png',fullPage:true})}
  await page.getByRole('button',{name:'返回列表',exact:true}).click();
 }
 await page.setViewportSize({width:1024,height:768});await page.waitForTimeout(300);
 await expect(page.locator('.listing-product').first().getByRole('button',{name:'复制 FNSKU',exact:true})).toBeVisible();
 await page.screenshot({path:root+'docs/listing-layout-1024.png',fullPage:true});
 if(errors.length||writes.length)throw new Error(JSON.stringify({errors,writes}));
 console.log(JSON.stringify({checks:['15 clipboard values','drag resize','persist after reload','reset widths','5 identity groups in basic info','FNSKU and copy in detail','1024 layout'],pageErrors:errors,networkWrites:writes}));
}finally{await browser.close()}
