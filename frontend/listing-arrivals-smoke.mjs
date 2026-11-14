import {chromium,expect} from '@playwright/test';
const root=new URL('../',import.meta.url).pathname;
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
const page=await browser.newPage({viewport:{width:1920,height:1080},locale:'zh-CN'}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto('http://127.0.0.1:8787');await page.locator('button.nav-item').filter({hasText:'Listing'}).click();await expect(page.locator('.listing-product')).toHaveCount(5);await expect(page.locator('.listing-table .el-loading-mask')).toBeHidden();
 await page.getByRole('button',{name:'同步库存',exact:true}).click();await expect(page.getByRole('button',{name:'同步库存',exact:true})).not.toHaveClass(/is-loading/,{timeout:120000});
 await page.getByRole('button',{name:'同步到达时间',exact:true}).click();await expect(page.getByRole('button',{name:'同步到达时间',exact:true})).toHaveClass(/is-loading/);await expect(page.getByRole('button',{name:'同步到达时间',exact:true})).not.toHaveClass(/is-loading/,{timeout:120000});
 const rows=(await (await page.request.get('http://127.0.0.1:8787/api/listings')).json()).listings;
 await expect(page.locator('.listing-table')).toContainText('FBA可售｜接收中｜入库中｜在途');await expect(page.locator('.listing-table')).toContainText('15天｜30天｜60天内到达');
 if(rows.some(r=>r.inventory.processing===null||r.arrivals.status!=='succeeded'))throw new Error('Real inventory or arrival data incomplete');
 const heights=await page.locator('.listing-table .el-table__body tbody tr').evaluateAll(es=>es.map(e=>e.getBoundingClientRect().height));
 if(Math.max(...heights)>95)throw new Error('Row height too large: '+heights);
 await page.waitForTimeout(800);await page.screenshot({path:root+'docs/listing-arrivals-list.png',fullPage:true});
 const candidate=rows.find(r=>r.arrivals.details.length>0);
 await page.getByRole('button',{name:`${candidate.sku} 预计到达明细`,exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'预计到达明细'});await expect(dialog).toContainText(candidate.arrivals.details[0].shipment_id);await expect(dialog).toContainText('三个数字不能相加');await expect(dialog).toContainText('Europe/Berlin');
 await expect(dialog).toBeVisible();await page.waitForTimeout(400);await page.screenshot({path:root+'docs/listing-arrival-detail.png',fullPage:true});await dialog.getByRole('button',{name:'关闭',exact:true}).click();
 // The added column must support the same saved drag widths as existing columns.
 const heading=page.locator('.listing-table th').filter({hasText:'15天｜30天｜60天内到达'});await heading.scrollIntoViewIfNeeded();await page.waitForTimeout(300);const box=await heading.boundingBox();
 await page.mouse.move(box.x+box.width-2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width+35,box.y+box.height/2,{steps:12});await page.mouse.up();
 await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('erp.listing.column-widths.v1')||'{}').arrivals)).toBeGreaterThan(205);
 await page.reload();await page.locator('button.nav-item').filter({hasText:'Listing'}).click();await expect(page.locator('.listing-product')).toHaveCount(5);await page.getByRole('button',{name:'重置列宽',exact:true}).click();
 await page.setViewportSize({width:1280,height:900});await page.waitForTimeout(300);await page.screenshot({path:root+'docs/listing-arrivals-1280.png',fullPage:true});
 if(errors.length)throw new Error(JSON.stringify(errors));
 console.log(JSON.stringify({rowHeights:heights,pageErrors:errors,samples:rows.map(r=>({id:r.id,inventory:[r.inventory.available,r.inventory.receiving,r.inventory.processing,r.inventory.shipped],arrivals:r.arrivals.values,shipments:r.arrivals.details.map(s=>s.shipment_id)})),checks:['inventory refresh button','background arrivals refresh and polling','four inventory stages','real delivery windows','remaining units','detail dialog','resizing persisted','compact rows','1280 layout']}));
}finally{await browser.close()}
