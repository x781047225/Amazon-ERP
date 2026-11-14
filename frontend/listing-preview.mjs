import {chromium,expect} from '@playwright/test';
const root=new URL('../',import.meta.url).pathname;
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const page=await browser.newPage({viewport:{width:1600,height:1050},locale:'zh-CN'});
 await page.goto('http://127.0.0.1:8787');
 await page.locator('button.nav-item').filter({hasText:'Listing'}).click();
 await expect(page.locator('.listing-product')).toHaveCount(5);
 await page.locator('.listing-thumb img').first().waitFor();
 await page.waitForFunction(()=>[...document.querySelectorAll('.listing-thumb img')].length===5&&[...document.querySelectorAll('.listing-thumb img')].every(i=>i.complete),{timeout:20000});
 await page.waitForTimeout(600);
 await page.screenshot({path:root+'docs/listing-center.png',fullPage:true});
 await page.getByRole('button',{name:'DEMO-SKU-3172',exact:true}).click();
 await expect(page.locator('[data-field="item_name"] input')).toBeVisible();
 await page.waitForTimeout(600);
 await page.screenshot({path:root+'docs/listing-detail.png',fullPage:true});
 await page.getByRole('tab',{name:'品类属性',exact:true}).click();
 await page.getByRole('button',{name:/产品详情 ·/}).click();
 await page.waitForTimeout(400);
 await page.screenshot({path:root+'docs/listing-type-attributes.png',fullPage:true});
 console.log('Clean Listing previews saved');
}finally{await browser.close()}
