const smokePassword=process.env.ERP_SMOKE_PASSWORD;
if(!smokePassword)throw new Error('请通过 ERP_SMOKE_PASSWORD 设置测试账号密码；禁止使用真实生产账号运行 smoke 测试');
import {chromium} from '@playwright/test';
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1500,height:1050}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
let mock=false,job=null;
await page.route('**/api/finance/summary/jobs**',async route=>{
 if(!mock)return route.continue();
 const req=route.request(),path=new URL(req.url()).pathname;
 if(req.method()==='POST') {const b=req.postDataJSON();job={id:'ui-test',store:b.store,marketplace:b.marketplace,start_date:b.start,end_date:b.end,currency:b.currency,status:'running',pages:1,created_at:new Date().toISOString()};return route.fulfill({json:job});}
 if(path.endsWith('/ui-test'))return route.fulfill({json:{...job,result:{currency:'CNY',net_proceeds:'144.00',count:2,fx:{date:'2026-11-05'},totals:[{path:'销售收入',amount:'180.00'},{path:'费用支出',amount:'-36.00'}],sites:[{marketplace:'UK',account:'Standard Orders',original:'8',original_currency:'GBP',amount:'80.00'},{marketplace:'FR',account:'Standard Orders',original:'8',original_currency:'EUR',amount:'64.00'}]}}});
 return route.fulfill({json:job?[job]:[]});
});
try {
 await page.goto('http://127.0.0.1:8787');await page.getByRole('textbox',{name:'密码',exact:true}).fill(smokePassword);await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.getByRole('button',{name:'利润结算',exact:true}).click();await page.getByText('财务汇总',{exact:true}).click();
 await page.getByRole('button',{name:'下载财务汇总',exact:true}).waitFor();await page.waitForTimeout(400);await page.screenshot({path:'../docs/finance-summary/page.png',fullPage:true});
 mock=true;await page.getByRole('button',{name:'下载财务汇总',exact:true}).click();await page.getByText('下载中',{exact:true}).waitFor();
 await page.getByRole('button',{name:/订单中心/}).click();await page.getByRole('button',{name:'利润结算',exact:true}).click();await page.getByText('财务汇总',{exact:true}).click();await page.getByText('下载中',{exact:true}).waitFor();
 job.status='succeeded';await page.getByRole('button',{name:'刷新记录',exact:true}).click();await page.getByRole('link',{name:'下载结果',exact:true}).waitFor();
 await page.getByRole('button',{name:'查看汇总',exact:true}).click();await page.getByText('财务净额 ¥144.00',{exact:true}).waitFor();await page.waitForTimeout(400);await page.screenshot({path:'../docs/finance-summary/preview-test.png',fullPage:true});
 await page.keyboard.press('Escape');
 job.status='failed';job.error='财务汇总权限不足（403），请核对授权';await page.getByRole('button',{name:'刷新记录',exact:true}).click();await page.getByRole('button',{name:'重试',exact:true}).waitFor();
 if(errors.length)throw Error(errors.join(';'));
 console.log(JSON.stringify({ui:'passed',returnToDownloads:true,successPreview:true,failureRetry:true}));
 if(process.env.FINANCE_LIVE_PROBE==='1'){mock=false;
 const live=await page.request.post('http://127.0.0.1:8787/api/finance/summary/jobs',{headers:{'X-ERP-Client':'web'},data:{store:'DemoStoreB',marketplace:'UK',start:'2026-10-01',end:'2026-10-31',currency:'CNY'}});
 if(!live.ok())throw Error(await live.text());console.log(JSON.stringify({ui:'passed',returnToDownloads:true,successPreview:true,failureRetry:true,liveReadOnlyJob:await live.json()}));}
} finally {await browser.close()}
