const smokePassword=process.env.ERP_SMOKE_PASSWORD;
if(!smokePassword)throw new Error('请通过 ERP_SMOKE_PASSWORD 设置测试账号密码；禁止使用真实生产账号运行 smoke 测试');
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({headless:true,channel:'chrome'}),page=await browser.newPage({viewport:{width:1700,height:1000}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
try{
await page.goto('http://127.0.0.1:8787');await page.getByRole('textbox',{name:'密码',exact:true}).fill(smokePassword);await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('button.nav-item').filter({hasText:'FBA 货件'}).click();
await page.getByPlaceholder('搜索 FBA 号 / 名称 / SKU / FNSKU').fill('FBATEST00005');await page.getByRole('button',{name:'查询',exact:true}).click();
const row=page.locator('.el-table__body tr').filter({hasText:'FBATEST00005'}).first();await expect(row).toContainText('英国');await expect(row).toContainText('2026-11-05');await expect(page.getByText('国家按亚马逊收货仓地址确定；',{exact:false})).toBeVisible();
await expect(page.locator('.el-loading-mask:visible')).toHaveCount(0);await page.screenshot({path:'../docs/shipment-metadata.png',fullPage:true});if(errors.length)throw Error(errors.join(';'));console.log(JSON.stringify({country:'英国',creationDate:'2026-11-05',pageErrors:errors}));
}finally{await browser.close()}
