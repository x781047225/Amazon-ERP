const smokePassword=process.env.ERP_SMOKE_PASSWORD;
if(!smokePassword)throw new Error('请通过 ERP_SMOKE_PASSWORD 设置测试账号密码；禁止使用真实生产账号运行 smoke 测试');
import {chromium} from '@playwright/test';
const browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:1500,height:1000}});let remoteReads=0,posts=0;
await page.route('**/api/listings/*/price/refresh',r=>{remoteReads++;r.abort()});
await page.route('**/api/listings/*/price/preview',r=>{remoteReads++;r.abort()});
await page.route('**/api/listings/*/price/queue',async r=>{posts++;await r.fulfill({status:202,contentType:'application/json',body:JSON.stringify({id:r.request().postDataJSON().request_id,status:'queued'})})});
await page.route('**/api/listings/*/price/jobs/*',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({status:'rejected',message:'测试模拟失败：价格未被接受'})}));
try{
await page.goto('http://127.0.0.1:8787');await page.getByRole('textbox',{name:'密码',exact:true}).fill(smokePassword);await page.getByRole('button',{name:'登录',exact:true}).click();await page.getByRole('button',{name:'Listing',exact:true}).click();
await page.getByRole('button',{name:'调价',exact:true}).first().click();const dialog=page.getByRole('dialog',{name:'调价',exact:true});await dialog.waitFor();
if(await dialog.locator('.el-loading-mask').count())throw Error('spinner exists');
await dialog.getByRole('textbox',{name:'售价',exact:true}).fill('12.34');await dialog.getByRole('button',{name:'提交调价',exact:true}).click();await dialog.waitFor({state:'hidden'});await page.getByText('测试模拟失败：价格未被接受',{exact:true}).waitFor({timeout:12000});
if(remoteReads||posts!==1)throw Error('unexpected requests');console.log(JSON.stringify({localDialog:true,noRemoteRefresh:true,noPreview:true,backgroundSubmit:true,failureNotification:true,amazonWrites:0}));await page.screenshot({path:'../docs/price-async-smoke.png'});
}finally{await browser.close()}
