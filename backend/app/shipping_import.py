"""Excel shipping-plan import; no Amazon writes and no implicit Listing edits."""
from io import BytesIO
from pathlib import Path
from hashlib import sha256
import base64,json,re,zipfile
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill,Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from fastapi import HTTPException
from sqlalchemy import select,text
from .db import Session
from .models import Listing,ProductLink,AccessAudit
from .warehouse_models import Warehouse
from .shipping_models import ShippingTask as Task
from .auth import require_store,require_warehouse
from .shipping_files import numeric

HEADERS=['SKU','FNSKU','计划发货量']
def template():
 w=Workbook();s=w.active;s.title='发货计划';s.append(HEADERS);s.freeze_panes='A2'
 for c,width in zip('ABC',[45,22,18]):s.column_dimensions[c].width=width
 for cell in s[1]:cell.fill=PatternFill('solid',fgColor='E9F2ED');cell.font=Font(bold=True,color='243E34');cell.alignment=Alignment(vertical='center')
 s.row_dimensions[1].height=28
 for row in s.iter_rows(min_row=2,max_row=201,max_col=3):
  row[0].number_format='@';row[1].number_format='@';row[2].number_format='0'
 v=DataValidation(type='whole',operator='between',formula1=1,formula2=100000,allow_blank=True);v.showErrorMessage=True;v.error='请填写1至100000的整数';s.add_data_validation(v);v.add('C2:C201')
 help=w.create_sheet('填写说明');help.column_dimensions['A'].width=110
 for line in ['先在上传窗口选择店铺、国家、渠道、发货仓库；每个文件生成一张计划。','在“发货计划”表填写SKU、FNSKU、计划发货量，最多200行。','SKU须属于所选店铺、国家且已关联有效标品；请保持SKU大小写和文本格式。','FNSKU可留空，系统按SKU带出；填写后会与已有FNSKU核对。系统暂无FNSKU时可填写10位大写字母/数字。','计划发货量为1至100000的整数，不支持公式。同一SKU的多行会合并数量，合计不得超过100000。','上传后先校验预览，存在错误不会生成计划。默认不锁定库存，可在窗口选择校验并锁定。']:help.append([line])
 b=BytesIO();w.save(b);return b.getvalue()

def parse(raw):
 try:
  with zipfile.ZipFile(BytesIO(raw)) as z:
   if len(z.infolist())>1000 or sum(i.file_size for i in z.infolist())>20000000:raise ValueError('文件内容过大，请使用模板并限制在200行内')
  w=load_workbook(BytesIO(raw),read_only=True,data_only=False,keep_links=False)
 except ValueError:raise
 except Exception:raise ValueError('无法读取xlsx文件，请使用下载的模板')
 try:
  if '发货计划' not in w.sheetnames:raise ValueError('缺少“发货计划”工作表，请使用下载的模板')
  s=w['发货计划']
  if (s.max_row or 0)>2001 or (s.max_column or 0)>20:raise ValueError('文件行列过多，请使用模板并限制在200行内')
  if [s.cell(1,c).value for c in range(1,4)]!=HEADERS:raise ValueError('模板表头须依次为SKU、FNSKU、计划发货量')
  rows=[]
  for row_number,cells in enumerate(s.iter_rows(min_row=2),2):
   if not any(c.value is not None and str(c.value).strip() for c in cells):continue
   if len(rows)>=200:raise ValueError('每次最多上传200行商品')
   row={'row':row_number,'sku':'','fnsku':'','quantity':None,'error':''}
   try:
    if any(c.data_type=='f' for c in cells):raise ValueError('请填写实际值，不支持公式')
    if any(c.value is not None for c in cells[3:]):raise ValueError('模板仅支持SKU、FNSKU、计划发货量三列')
    sku,fn,qty=[c.value for c in cells[:3]]
    if not isinstance(sku,str) or not sku.strip() or len(sku.strip())>255:raise ValueError('SKU须为非空文本，最长255个字符')
    row['sku']=sku.strip();row['fnsku']=str(fn or '').strip()
    if row['fnsku'] and not re.fullmatch('[A-Z0-9]{10}',row['fnsku']):raise ValueError('FNSKU须为10位大写字母或数字')
    row['quantity']=numeric(qty,'计划发货量',whole=True)
    if not 1<=row['quantity']<=100000:raise ValueError('计划发货量须为1至100000的整数')
   except ValueError as e:row['error']=str(e)
   rows.append(row)
  if not rows:raise ValueError('文件没有商品，请填写后再上传')
  return rows
 finally:w.close()

def run(b):
 from .shipping import COUNTRIES,CHANNELS,actor,shipping_lock,fnsku,item_snapshot,merge_items,reserve_task,audit,number
 require_store(b.store);require_warehouse(b.warehouse_id)
 if b.country not in COUNTRIES or b.channel not in CHANNELS:raise HTTPException(422,'国家或渠道无效')
 try:raw=base64.b64decode(b.content,validate=True)
 except Exception:raise HTTPException(422,'文件内容无效')
 if len(raw)>10000000 or not b.filename.lower().endswith('.xlsx'):raise HTTPException(422,'请上传10MB以内的xlsx发货计划')
 identity={**b.model_dump(mode='json',exclude={'content','confirm','preview_token'}),'digest':sha256(raw).hexdigest()}
 with Session.begin() as db:
  if not db.get(Warehouse,b.warehouse_id):raise HTTPException(422,'发货仓库不存在或无权限')
  if b.confirm:
   shipping_lock(db)
   db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'shipping-create:'+str(b.request_id)})
   old=db.scalar(select(Task).where(Task.request_id==str(b.request_id)))
   if old:
    saved=db.scalar(select(AccessAudit).where(AccessAudit.action=='shipping_create',AccessAudit.target==str(old.id)).order_by(AccessAudit.id.desc()))
    if old.actor!=actor() or old.status=='deleted' or not saved or saved.detail.get('import_request')!=identity:raise HTTPException(409,'请求编号已使用，请重新上传')
    return {'id':old.id,'number':number(old)}
  try:rows=parse(raw)
  except ValueError as e:raise HTTPException(422,str(e))
  snapshots=[]
  for row in rows:
   if row['error']:continue
   try:
    l=db.scalar(select(Listing).where(Listing.store==b.store,Listing.marketplace==b.country,Listing.sku==row['sku']))
    if not l:raise ValueError('所选店铺/国家未找到该SKU，请先同步Listing')
    link=db.get(ProductLink,l.id)
    if not link:raise ValueError('SKU尚未关联标品，请先在Listing关联')
    known=fnsku(l)
    if known and row['fnsku'] and known!=row['fnsku']:raise ValueError('FNSKU与该SKU不一致，请核对')
    resolved=known or row['fnsku']
    if not re.fullmatch('[A-Z0-9]{10}',resolved):raise ValueError('FNSKU缺失，请填写或先同步Listing')
    item=item_snapshot(db,link.product_id,row['quantity'],b.store,b.country,l.id);item['fnsku']=resolved
    row.update(fnsku=resolved,code=item['code'],name=item['name']);snapshots.append(item)
   except (ValueError,HTTPException) as e:row['error']=str(e.detail if isinstance(e,HTTPException) else e)
  items=merge_items(snapshots)
  over={i['sku'] for i in items if i['quantity']>100000}
  for row in rows:
   if row['sku'] in over:row['error']='同一SKU合计计划发货量不得超过100000'
  valid=not any(r['error'] for r in rows)
  token=sha256(json.dumps({'request':identity,'items':items},sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
  preview={'rows':rows,'items':items,'quantity':sum(i['quantity'] for i in items),'item_count':len(items),'can_import':valid,'preview_token':token}
  if not b.confirm:return {'preview':preview}
  if not valid:raise HTTPException(422,'文件存在错误，请重新校验并修正后再生成计划')
  if not b.preview_token or b.preview_token!=token:raise HTTPException(409,'发货资料或商品匹配已变化，请重新校验预览')
  t=Task(request_id=str(b.request_id),store=b.store,country=b.country,channel=b.channel,warehouse_id=b.warehouse_id,ship_date=b.ship_date,lock_inventory=b.lock_inventory,items=items,actor=actor(),note='Excel导入：'+Path(b.filename).name)
  db.add(t);db.flush();reserve_task(db,t)
  audit(db,'shipping_create',t.id,{'source':'excel','filename':Path(b.filename).name,'quantity':preview['quantity'],'import_request':identity})
  return {'id':t.id,'number':number(t)}
