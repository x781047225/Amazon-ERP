const smokePassword=process.env.ERP_SMOKE_PASSWORD;
if(!smokePassword)throw new Error('请通过 ERP_SMOKE_PASSWORD 设置测试账号密码；禁止使用真实生产账号运行 smoke 测试');
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto('http://127.0.0.1:8787');
 await page.getByRole('textbox',{name:'密码',exact:true}).fill(smokePassword);
 await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.getByRole('button',{name:'利润结算',exact:true}).click();
 await page.getByText('财务汇总',{exact:true}).click();
 await page.getByRole('button',{name:'查看汇总',exact:true}).first().click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByText('亚马逊服务费及其他费用 / 广告费用',{exact:true})).toBeAttached();
 await expect(dialog.getByText('亚马逊服务费及其他费用 / 亚马逊物流（FBA）费用',{exact:true})).toBeAttached();
 if((await dialog.innerText()).includes('AMAZON_SERVICE_CHARGES_AND_FEES'))throw Error('English category still displayed');
 await page.screenshot({path:'../docs/finance-summary/chinese-labels.png',fullPage:true});
 await page.keyboard.press('Escape');
 const response=await page.request.get(new URL(await page.getByRole('link',{name:'下载结果',exact:true}).first().getAttribute('href'),page.url()).href);
 if(!response.ok()||!(response.headers()['content-type']||'').includes('spreadsheetml'))throw Error('Download failed');
 if(errors.length)throw Error(errors.join(';'));
 console.log(JSON.stringify({previewChinese:true,downloadOK:true,pageErrors:errors}));
}finally{await browser.close();}
