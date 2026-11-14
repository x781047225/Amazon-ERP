import {chromium,expect} from '@playwright/test';
const root=new URL('../',import.meta.url).pathname;
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
const page=await browser.newPage({viewport:{width:1600,height:1050},locale:'zh-CN'});
const errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url())});
let dirtySample=null;
try{
await page.goto('http://127.0.0.1:8787');
await page.locator('button.nav-item').filter({hasText:'Listing'}).click();
await page.getByRole('heading',{name:'Listing',exact:true}).waitFor();
await expect(page.locator('.listing-product')).toHaveCount(5);
await expect(page.locator('.nav-item.disabled').filter({hasText:'商品中心'})).toBeVisible();
await page.screenshot({path:root+'docs/listing-center.png',fullPage:true});
await page.getByPlaceholder('搜索 SKU / ASIN / 标题').fill('DEMO-SKU-3172');
await page.getByRole('button',{name:'查询',exact:true}).click();
await expect(page.locator('.listing-product')).toHaveCount(1);
await page.getByRole('button',{name:'查看详情',exact:true}).click();
await page.getByRole('button',{name:'同步最新信息',exact:true}).waitFor();
const record=await (await page.request.get('http://127.0.0.1:8787/api/listings/2')).json();
if(Object.keys(record.draft?.changes||{}).length)throw new Error('Existing user draft; UI test refuses to overwrite it');
const input=page.locator('[data-field="item_name"] input').first();
const original=await input.inputValue();
await input.fill(original+' ');
await page.locator('button.nav-item').filter({hasText:'FBA 货件'}).click();
await page.getByRole('button',{name:'继续编辑',exact:true}).click();
await expect(input).toHaveValue(original+' ');
await page.getByRole('button',{name:'保存本地草稿',exact:true}).click();dirtySample=2;
await page.getByText('草稿已保存到本地，未提交亚马逊',{exact:true}).waitFor();
await page.getByRole('tab',{name:'修改对比 (1)',exact:true}).click();
await expect(page.locator('.diff-card')).toHaveCount(1);
await expect(page.locator('.diff-title').first()).toContainText('标题');
await page.screenshot({path:root+'docs/listing-diff.png',fullPage:true});
const refreshPromise=page.waitForResponse(r=>r.url().endsWith('/api/listings/2/refresh'),{timeout:180000});
await page.getByRole('button',{name:'同步最新信息',exact:true}).click();
const refreshed=await (await refreshPromise).json();
if(refreshed.title!==original||refreshed.draft?.changes.item_name[0].value!==original+' ')throw new Error('Refresh changed original or lost draft');
await page.getByRole('button',{name:'同步最新信息',exact:true}).waitFor({state:'visible'});
await expect(page.getByRole('button',{name:'同步最新信息',exact:true})).not.toHaveClass(/is-loading/,{timeout:20000});
await page.getByRole('tab',{name:'品类属性',exact:true}).click();
await page.getByRole('button',{name:/产品详情 ·/}).click();
await page.screenshot({path:root+'docs/listing-type-attributes.png',fullPage:true});
await page.getByRole('tab',{name:'基本信息',exact:true}).click();
await page.screenshot({path:root+'docs/listing-detail.png',fullPage:true});
await page.getByRole('tab',{name:'修改对比 (1)',exact:true}).click();
await page.getByRole('button',{name:'放弃本地草稿',exact:true}).click();
await page.getByRole('button',{name:'放弃草稿',exact:true}).click();
await page.getByText('本地草稿已清除',{exact:true}).waitFor();dirtySample=null;
await page.getByRole('button',{name:'返回列表',exact:true}).click();
await page.getByRole('button',{name:'重置',exact:true}).click();
await expect(page.locator('.listing-product')).toHaveCount(5);
await page.setViewportSize({width:1024,height:768});
await page.screenshot({path:root+'docs/listing-center-1024.png',fullPage:true});
const all=await (await page.request.get('http://127.0.0.1:8787/api/listings')).json();
for(const row of all.listings){
 const data=await (await page.request.get('http://127.0.0.1:8787/api/listings/'+row.id)).json();
 if(!data.template||!data.groups.length||data.template_error||data.catalog_error)throw new Error('Sample template missing: '+row.sku);
}
if(new Set(all.listings.map(x=>x.product_type)).size!==5)throw new Error('Samples do not cover five types');
console.log(JSON.stringify({pageErrors:errors,checks:['five real types','local product center retained','search','detail sync button','unsaved leave guard','local field-only draft','read refresh keeps draft','dynamic type fields','draft discard','1024 viewport'],localPostPaths:writes.map(s=>new URL(s).pathname)}));
if(errors.length)throw new Error('Page errors');
}finally{
 if(dirtySample){const d=await (await page.request.get('http://127.0.0.1:8787/api/listings/'+dirtySample)).json();await page.request.post(`http://127.0.0.1:8787/api/listings/${dirtySample}/draft/discard`,{headers:{'X-ERP-Client':'web'},data:{expected_draft_revision:d.draft?.revision||0}});}
 await browser.close();
}
