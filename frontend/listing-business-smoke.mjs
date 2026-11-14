import {chromium,expect} from '@playwright/test';
const root=new URL('../',import.meta.url).pathname;
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
const page=await browser.newPage({viewport:{width:1920,height:1080},locale:'zh-CN'}),errors=[];let draftId=null;
page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto('http://127.0.0.1:8787');await page.locator('button.nav-item').filter({hasText:'Listing'}).click();await expect(page.locator('.listing-product')).toHaveCount(5);await expect(page.locator('.listing-table .el-loading-mask')).toBeHidden();
 const rows=(await (await page.request.get('http://127.0.0.1:8787/api/listings')).json()).listings;
 if(rows.some(r=>r.inventory.available===null||r.inventory.inbound===null))throw new Error('Inventory sample absent');
 await expect(page.locator('.listing-table')).toContainText('售价｜促销价');await expect(page.locator('.listing-table')).toContainText('昨天｜7｜14｜30天销量');
 const height=await page.locator('.listing-table .el-table__body tbody tr').first().evaluate(e=>e.getBoundingClientRect().height);
 if(height>130)throw new Error('Rows are not compact: '+height);
 await page.waitForTimeout(400);await page.screenshot({path:root+'docs/listing-business-list.png',fullPage:true});
 const candidate=rows.find(r=>r.pricing.editable&&!Object.keys(r.pricing.draft?.changes||{}).length&&r.pricing.sale_status==='none');
 if(!candidate)throw new Error('No safe sample without user price draft');
 const index=rows.findIndex(r=>r.id===candidate.id),row=page.locator('.listing-table .el-table__body tbody tr').nth(index);
 await row.getByRole('button',{name:'调价',exact:true}).click();const dialog=page.getByRole('dialog',{name:'调价',exact:true});await expect(dialog.getByRole('textbox',{name:'售价',exact:true})).toHaveValue(candidate.pricing.price);
 const initialPrice=candidate.pricing.price;
 await dialog.getByRole('textbox',{name:'促销价',exact:true}).fill('1.00');await dialog.getByRole('button',{name:'保存本地草稿',exact:true}).click();await expect(dialog.getByText('填写促销价时必须填写开始和结束时间',{exact:true})).toBeVisible();
 const start=new Date(Date.now()+2*86400000),end=new Date(Date.now()+4*86400000);start.setHours(9,0,0,0);end.setHours(18,0,0,0);
 const format=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:00`;
 await dialog.getByPlaceholder('开始时间',{exact:true}).fill(format(start));await dialog.getByPlaceholder('开始时间',{exact:true}).press('Tab');
 await dialog.getByPlaceholder('结束时间',{exact:true}).fill(format(end));await dialog.getByPlaceholder('结束时间',{exact:true}).press('Tab');
 await dialog.locator('.price-sku').click();
 await dialog.getByRole('textbox',{name:'售价',exact:true}).fill((Number(initialPrice)+.01).toFixed(2));
 const saveResponse=page.waitForResponse(r=>r.url().endsWith(`/api/listings/${candidate.id}/price-draft`));await dialog.getByRole('button',{name:'保存本地草稿',exact:true}).click();const saved=await (await saveResponse).json();draftId=candidate.id;
 if(saved.draft?.changes.sale_price!=='1.00'||new Date(saved.draft.changes.sale_start).getTime()!==start.getTime()||new Date(saved.draft.changes.sale_end).getTime()!==end.getTime())throw new Error('Promotion date roundtrip failed');
 if(saved.price!==initialPrice||saved.draft.changes.price!==(Number(initialPrice)+.01).toFixed(2))throw new Error('Draft changed Amazon cached price');
 await expect(dialog.getByRole('button',{name:'保存本地草稿',exact:true})).toBeDisabled();await page.screenshot({path:root+'docs/listing-price-dialog.png',fullPage:false});
 await dialog.getByRole('button',{name:'关闭',exact:true}).click();await row.getByRole('button',{name:'调价',exact:true}).click();await expect(dialog.getByRole('textbox',{name:'售价',exact:true})).toHaveValue((Number(initialPrice)+.01).toFixed(2));
 await dialog.getByRole('button',{name:'放弃调价草稿',exact:true}).click();await page.getByRole('button',{name:'放弃草稿',exact:true}).click();await expect(dialog.getByRole('textbox',{name:'售价',exact:true})).toHaveValue(initialPrice);draftId=null;
 await dialog.getByRole('button',{name:'关闭',exact:true}).click();await page.setViewportSize({width:1280,height:900});await page.waitForTimeout(300);await page.screenshot({path:root+'docs/listing-business-1280.png',fullPage:true});
 if(errors.length)throw new Error(JSON.stringify(errors));console.log(JSON.stringify({rowHeight:height,pageErrors:errors,checks:['real inventory','compact rows','price/sales headers','promo dates required','promotion date/time roundtrip','local price draft','cached price unchanged','reopen draft','discard draft','1280 layout']}));
}finally{
 if(draftId){const d=await (await page.request.get(`http://127.0.0.1:8787/api/listings/${draftId}/price`)).json();await page.request.post(`http://127.0.0.1:8787/api/listings/${draftId}/price-draft/discard`,{headers:{'X-ERP-Client':'web'},data:{expected_draft_revision:d.draft.revision}})}
 await browser.close();
}
