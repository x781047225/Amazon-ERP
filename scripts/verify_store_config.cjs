const {chromium}=require('../frontend/node_modules/playwright'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({channel:'chrome',headless:true});try{
const page=await browser.newPage({viewport:{width:1440,height:1050}});const errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));
const admin={id:1,username:'settings-qa',is_admin:true,stores:['Demo'],current_store:'Demo',default_store:'Demo'};
const shop={name:'Demo',seller_id:'seller-demo',client_id:'client-demo',proxy_protocol:'socks5h',proxy_host:'127.0.0.1',proxy_port:1080,proxy_username:'proxy-user',ads_client_id:'ads-client-demo',ads_api_url:'https://advertising-api-eu.amazon.com',authorized:true,ads_authorized:true,proxy_configured:true,configured:{client_secret:true,refresh_token:true,proxy_password:true,ads_client_secret:true,ads_refresh_token:true}};
await page.addInitScript(()=>sessionStorage.setItem('erp:workspace-tabs:settings-qa',JSON.stringify({tabs:[{key:'settings'}],active:'settings'})));
await page.route('**/api/**',async route=>{const req=route.request(),p=new URL(req.url()).pathname;let json={};
if(req.method()==='POST'){posts.push({p,body:req.postDataJSON()});json={ok:true};}
else if(p==='/api/auth/me')json=admin;
else if(p==='/api/options')json={stores:['Demo'],markets:['UK'],order_markets:['UK']};
else if(p==='/api/settings/stores')json=[shop];
else if(p==='/api/warehouse/options')json={warehouses:[]};
else if(p.includes('price'))json={jobs:[],active:[]};
await route.fulfill({json});});
await page.goto('http://127.0.0.1:8787');await page.locator('.el-radio-button').filter({hasText:'店铺授权管理'}).click();await page.getByRole('button',{name:'更新授权',exact:true}).click();
let dialog=page.getByRole('dialog');await dialog.waitFor();
const field=(label)=>dialog.locator('.el-form-item').filter({has:page.locator('.el-form-item__label').filter({hasText:new RegExp('^'+label+'$')})});
assert.equal(await field('client_id').nth(0).locator('input').inputValue(),'client-demo');assert.equal(await field('client_id').nth(1).locator('input').inputValue(),'ads-client-demo');
for(const label of ['client_secret','refresh_token'])for(const input of await field(label).locator('input').all())assert.equal(await input.inputValue(),'');
assert.equal(await field('密码').locator('input').inputValue(),'');assert.equal(await field('账号').locator('input').inputValue(),'proxy-user');
await field('账号').locator('input').fill('changed-user');await page.waitForTimeout(500);await page.screenshot({path:'docs/qa/store-config-editor.png',fullPage:true});await dialog.getByRole('button',{name:'保存',exact:true}).click();await page.waitForTimeout(200);
assert.equal(posts.at(-1).p,'/api/settings/stores/Demo');assert.equal(posts.at(-1).body.proxy_username,'changed-user');assert.equal(posts.at(-1).body.client_secret,'');assert.equal(posts.at(-1).body.ads_client_secret,'');
await page.getByRole('button',{name:'新增店铺',exact:true}).click();dialog=page.getByRole('dialog',{name:'新增店铺'});await dialog.waitFor();await field('店铺名称').locator('input').fill('New Demo');await dialog.getByRole('button',{name:'保存',exact:true}).click();await page.waitForTimeout(200);assert.equal(decodeURIComponent(posts.at(-1).p),'/api/settings/stores/New Demo');assert.deepEqual(errors,[]);
console.log('PASS: database configuration UI, independent ads credentials, proxy fields, masked secrets, edit and add. All API writes mocked.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
