const smokePassword=process.env.ERP_SMOKE_PASSWORD;
if(!smokePassword)throw new Error('请通过 ERP_SMOKE_PASSWORD 设置测试账号密码；禁止使用真实生产账号运行 smoke 测试');
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/procurement/**',async route=>{
 if(route.request().method()!=='GET')throw Error('Unexpected procurement write');
 if(/\/(plans|orders)\?/.test(route.request().url()))await new Promise(r=>setTimeout(r,700));
 await route.continue();
});
try{
 await page.goto('http://127.0.0.1:8787');
 await page.getByRole('textbox',{name:'密码',exact:true}).fill(smokePassword);
 await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.getByRole('button',{name:'采购管理',exact:true}).click();
 const panel=page.locator('.procurement-panel');
 for(let n=0;n<3;n++){
  await expect(panel.locator('.el-loading-mask:visible')).toHaveCount(0);
  await panel.getByText('采购单',{exact:true}).click();
  await expect(panel.getByRole('button',{name:'批量到货',exact:true})).toBeVisible();
  await expect(panel.locator('.el-loading-mask:visible')).toHaveCount(0);
  await expect(panel.locator('.el-table__body tbody tr').first()).toBeVisible();
  await panel.getByText('采购计划',{exact:true}).click();
  await expect(panel.getByRole('button',{name:'新增采购计划',exact:true})).toBeVisible();
  await expect(panel.locator('.el-loading-mask:visible')).toHaveCount(0);
 }
 // Switch back before the delayed order response returns; stale data must not replace plans.
 await panel.getByText('采购单',{exact:true}).click();
 await panel.getByText('采购计划',{exact:true}).click();
 await page.waitForTimeout(1200);
 await expect(panel.getByRole('button',{name:'新增采购计划',exact:true})).toBeVisible();
 await expect(panel.locator('.el-table__body tbody tr').first()).toContainText('PP');
 await panel.getByText('采购单',{exact:true}).click();
 await expect(panel.locator('.el-loading-mask:visible')).toHaveCount(0);
 await expect(panel.locator('.el-table__body tbody tr').first()).toContainText('PO');
 await page.screenshot({path:'../docs/procurement/tabs-fixed.png',fullPage:true});
 if(errors.length)throw Error(errors.join(';'));
 console.log(JSON.stringify({passed:true,roundTrips:3,delayedResponseMs:700,rapidSwitch:true,pageErrors:errors,businessWrites:0}));
}finally{await browser.close()}
