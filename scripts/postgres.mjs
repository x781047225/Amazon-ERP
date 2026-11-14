import EmbeddedPostgres from 'embedded-postgres';
import fs from 'node:fs';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..');
const config=JSON.parse(fs.readFileSync(path.join(root,'config/local.json'),'utf8'));
const pg=new EmbeddedPostgres({databaseDir:path.join(root,'data/postgres'),user:'erp',password:config.db_password,port:config.pg_port,persistent:true,authMethod:'scram-sha-256',initdbFlags:['--encoding=UTF8','--locale=C'],postgresFlags:['-h','127.0.0.1','-k',path.join(root,'data'),'-c','timezone=UTC'],onLog:m=>console.log(String(m)),onError:m=>console.error(String(m))});
if(!fs.existsSync(path.join(root,'data/postgres/PG_VERSION')))await pg.initialise();
await pg.start();
const client=pg.getPgClient();await client.connect();
const found=await client.query("SELECT 1 FROM pg_database WHERE datname='amazon_erp'");
if(!found.rows.length)await client.query('CREATE DATABASE amazon_erp');
await client.end();
console.log('ERP_POSTGRES_READY');
let stopping=false;
async function stop(){if(stopping)return;stopping=true;await pg.stop();process.exit(0)}
process.on('SIGTERM',stop);process.on('SIGINT',stop);
setInterval(()=>{},60000);
