"""Runtime warehouse document generation. Excel values are data, never executable formulas from uploads."""
from io import BytesIO
import re,zipfile,math
from pathlib import Path
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from .settings import ROOT

def literal(cell,value):
 cell.value=value
 if isinstance(value,str):cell.data_type='s'
def packing_template(t,title,boxes):
 w=Workbook();s=w.active;s.title='装箱清单';end=5+boxes
 s.merge_cells(start_row=1,start_column=1,end_row=1,end_column=end);literal(s.cell(1,1),title)
 headers=['SKU','FNSKU','品名','发货数量','已装箱数']+[f'第{i}箱' for i in range(1,boxes+1)]
 for c,v in enumerate(headers,1):s.cell(2,c,v)
 for r,i in enumerate(t.items,3):
  for c,v in enumerate([i['sku'],i['fnsku'],i['name'],i['quantity']],1):literal(s.cell(r,c),v)
  s.cell(r,5,f'=SUM(F{r}:{get_column_letter(end)}{r})')
 total=3+len(t.items);s.cell(total,3,'合计')
 for c in range(4,end+1):s.cell(total,c,f'=SUM({get_column_letter(c)}3:{get_column_letter(c)}{total-1})')
 for r,label in enumerate(['Weight of box (kg)','Length of box (cm)','Width of box (cm)','Height of box (cm)'],total+1):s.merge_cells(start_row=r,start_column=4,end_row=r,end_column=5);s.cell(r,4,label)
 s.cell(total+6,1,'填写各箱数量及重量、尺寸后上传。可增加“第N箱”列；超出计划的装箱数量会在出库时补齐计划。')
 s.cell(total+7,1,'任务编号');s.cell(total+7,2,f'PL{t.created_at:%y%m%d}{t.id:06d}')
 s.freeze_panes='F3';s.print_title_rows='1:2';s.sheet_properties.pageSetUpPr.fitToPage=True;s.page_setup.orientation='landscape';s.page_setup.paperSize=s.PAPERSIZE_A4;s.page_setup.fitToWidth=1;s.page_setup.fitToHeight=0
 for c,width in enumerate([43,18,44,14,14]+[12]*boxes,1):s.column_dimensions[get_column_letter(c)].width=width
 for row in s.iter_rows(min_row=1,max_row=total+4,max_col=end):
  for cell in row:
   cell.font=Font(name='Arial',size=11,color='243E34');cell.alignment=Alignment(vertical='center',wrap_text=True);cell.border=Border(bottom=Side(style='hair',color='DCE7E2'))
   if cell.row in [1,2,total]:cell.fill=PatternFill('solid',fgColor='E9F2ED');cell.font=Font(name='Arial',size=11,bold=True,color='243E34')
   elif cell.column>=6:cell.font=Font(name='Arial',size=11,color='1565C0')
  s.row_dimensions[row[0].row].height=32
 for dtype,op,f1,f2,ran in [('whole','between',0,100000,f'F3:{get_column_letter(end)}{total-1}'),('decimal','between',0.001,10000,f'F{total+1}:{get_column_letter(end)}{total+4}')]:
  v=DataValidation(type=dtype,operator=op,formula1=f1,formula2=f2,allow_blank=True);v.errorTitle='请检查输入';v.error='数量为非负整数；重量和尺寸须大于0';v.showErrorMessage=True;s.add_data_validation(v);v.add(ran)
 b=BytesIO();w.save(b);return b.getvalue()

def numeric(v,label,whole=False):
 if isinstance(v,bool) or isinstance(v,str) and v.startswith('='):raise ValueError(f'{label}请填写实际数值，不支持公式')
 try:n=float(v)
 except (ValueError,TypeError):raise ValueError(f'{label}不是有效数字')
 if not math.isfinite(n) or n<0 or n>1000000 or whole and n!=int(n):raise ValueError(f'{label}须为有效的非负'+('整数' if whole else '数值'))
 return int(n) if whole else n

def parse_packing(raw,items,task_number):
 try:
  with zipfile.ZipFile(BytesIO(raw)) as z:
   if len(z.infolist())>3000 or sum(i.file_size for i in z.infolist())>40000000:raise ValueError('装箱文件过大，请缩小后上传')
  w=load_workbook(BytesIO(raw),data_only=False,read_only=True,keep_links=False)
 except ValueError:raise
 except Exception:raise ValueError('无法读取xlsx文件')
 sheets=[]
 for s in w:
  if s.max_row>5000 or s.max_column>300:continue
  for r,vs in enumerate(s.iter_rows(min_row=1,max_row=min(10,s.max_row),values_only=True),1):
   cols={str(v).strip():c for c,v in enumerate(vs) if v is not None}
   if 'FNSKU' in cols and ('MSKU' in cols or 'SKU' in cols) and '发货数量' in cols:sheets.append((s,r,cols));break
 if len(sheets)!=1:raise ValueError('须有且仅有一张装箱明细表，包含SKU/MSKU、FNSKU和发货数量')
 s,hr,cols=sheets[0];sku_col=cols.get('MSKU',cols.get('SKU'));fn_col=cols['FNSKU'];qty_col=cols['发货数量']
 box_cols={c:int(re.fullmatch(r'第\s*(\d+)\s*箱',name).group(1)) for name,c in cols.items() if re.fullmatch(r'第\s*(\d+)\s*箱',name)}
 if not box_cols or len(box_cols)>200 or len(set(box_cols.values()))!=len(box_cols) or any(n<1 for n in box_cols.values()):raise ValueError('箱号须为不重复的第1箱、第2箱等，最多200箱')
 expected={i['sku']:i for i in items};seen=set();result=[];box_items={n:[] for n in box_cols.values()};metadata={n:{} for n in box_cols.values()}
 allrows=list(s.iter_rows(values_only=True));finished=False
 for idx,values in enumerate(allrows,1):
  vals=list(values)
  def cell(c):return vals[c] if c<len(vals) else None
  # Only exported ERP identifiers are binding; Lingxing external task numbers are retained as reference.
  for c,v in enumerate(vals[:-1]):
   if v=='任务编号' and vals[c+1] and str(vals[c+1])!=task_number:raise ValueError('装箱单的ERP任务编号与当前计划不一致')
  if idx<=hr:continue
  labels=' '.join(str(v).lower() for v in vals if v is not None and not isinstance(v,(int,float)))
  metric=next((key for term,key in [('weight of box','weight_kg'),('length of box','length_cm'),('width of box','width_cm'),('height of box','height_cm')] if term in labels),None)
  if metric:
   for c,n in box_cols.items():
    if cell(c) is not None:metadata[n][metric]=numeric(cell(c),f'第{n}箱{metric}')
   continue
  sku=str(cell(sku_col) or '').strip()
  if any(str(v)=='合计' for v in vals[:qty_col+1]):finished=True;continue
  if finished:continue
  if not sku and not cell(fn_col) and not cell(qty_col):continue
  if sku not in expected:raise ValueError(f'第{idx}行SKU不在当前发货计划中：{sku}')
  if sku in seen:raise ValueError(f'SKU重复：{sku}')
  seen.add(sku);i=expected[sku]
  if str(cell(fn_col) or '').strip()!=i['fnsku']:raise ValueError(f'{sku}的FNSKU与计划不一致')
  if numeric(cell(qty_col),f'{sku}发货数量',True)!=i['quantity']:raise ValueError(f'{sku}的发货数量被修改，请先变更计划再下载装箱单')
  qs={}
  for c,n in box_cols.items():
   q=0 if cell(c) in (None,'') else numeric(cell(c),f'{sku}第{n}箱数量',True)
   if q:qs[str(n)]=q;box_items[n].append({'sku':sku,'fnsku':i['fnsku'],'quantity':q})
  packed=sum(qs.values())
  result.append({'sku':sku,'fnsku':i['fnsku'],'name':i['name'],'quantity':i['quantity'],'packed':packed,'remaining':max(0,i['quantity']-packed),'boxes':qs})
 if seen!=set(expected):raise ValueError('装箱表缺少计划中的SKU：'+', '.join(sorted(set(expected)-seen)[:5]))
 boxes=[]
 for n in sorted(box_items):
  if not box_items[n]:continue
  meta=metadata[n]
  if any(meta.get(k,0)<=0 for k in ['weight_kg','length_cm','width_cm','height_cm']):raise ValueError(f'第{n}箱请填写大于0的重量及长宽高（kg/cm）')
  boxes.append({'number':n,**meta,'items':box_items[n],'quantity':sum(i['quantity'] for i in box_items[n])})
 if not boxes:raise ValueError('没有填写实际装箱数量')
 return {'items':result,'boxes':boxes,'packed':sum(i['packed'] for i in result),'remaining':sum(i['remaining'] for i in result)}

def labels_pdf(items,profile,country,title,task_number,separators=True):
 from reportlab.pdfgen import canvas
 from reportlab.pdfbase import pdfmetrics
 from reportlab.pdfbase.ttfonts import TTFont
 from reportlab.lib.units import mm
 from reportlab.graphics.barcode import createBarcodeDrawing
 from reportlab.graphics import renderPDF
 from reportlab.lib.utils import ImageReader
 font=ROOT/'assets/shipping/ArialUnicode.ttf'
 if not font.exists():raise ValueError('缺少标签中文字体，请联系管理员补齐打印字体')
 if 'ERPLabel' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('ERPLabel',str(font)))
 if 'ERPLabelBold' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('ERPLabelBold',str(ROOT/'assets/shipping/ArialBold.ttf')))
 def text_width(text,size):
  prefix=next((p for p in ('Manufacturer:','UK | REP','EU | REP') if text.startswith(p)),None)
  if prefix:
   return pdfmetrics.stringWidth(prefix,'ERPLabelBold',size)+pdfmetrics.stringWidth(text[len(prefix):],'ERPLabel',size)
  return pdfmetrics.stringWidth(text,'ERPLabelBold' if text in ('UK | REP','EU | REP') else 'ERPLabel',size)
 def lines(text,size,width):
  rows=[]
  for paragraph in text.split('\n'):
   line=''
   for token in re.findall(r'\S+\s*',paragraph):
    if line and text_width(line+token.rstrip(),size)>width:
     rows.append(line.rstrip());line=''
    for ch in token:
     if text_width(line+ch,size)>width:
      rows.append(line.rstrip());line=''
     line+=ch
   if line:rows.append(line.rstrip())
  return rows
 def fit(sections,width,height):
  for size in [6,5.7,5.4,5.1,4.8,4.5,4.2,4.0]:
   rows=[line for s in sections for line in lines(s,size,width)]
   if len(rows)*size*1.12<=height:return size,rows
  raise ValueError('标签文字过长，50×50mm内无法清晰完整排下，请精简标签地址格式后重试（不会截断内容）')
 b=BytesIO();c=canvas.Canvas(b,pagesize=(100*mm,50*mm),pageCompression=1);c.setTitle(title+' FNSKU标签');col=0
 def newlabel():
  nonlocal col
  if col==2:c.showPage();col=0
  x=col*50*mm;col+=1;return x
 def body(i):
  x=newlabel();left=x+1*mm;width=48*mm
  # The middle 20mm band (40% of each 50mm label) is reserved for barcode and human-readable FNSKU.
  barcode=createBarcodeDrawing('Code128',value=i['fnsku'],barHeight=17*mm,barWidth=.28*mm,humanReadable=False,quiet=True,lquiet=3*mm,rquiet=3*mm)
  if barcode.width>width:raise ValueError('条码超出标签宽度')
  renderPDF.draw(barcode,c,x+(50*mm-barcode.width)/2,13*mm)
  c.setFont('ERPLabel',5.5);c.drawCentredString(x+25*mm,10.5*mm,i['fnsku'])
  rep='UK | REP' if country=='UK' else 'EU | REP'
  sections=['SKU: '+i['sku'],rep+' '+profile['representative_company'],'Address: '+profile['representative_address'],'Email: '+profile['representative_email'],'Manufacturer: '+profile['manufacturer_company'],'Address: '+profile['manufacturer_address'],'Email: '+profile['manufacturer_email']]
  size,ls=fit(sections,width,18*mm);y=49*mm-size;c.setFont('ERPLabel',size)
  # Use spare body height for slightly more breathing room without reducing text size.
  leading=min(size*1.18,18*mm/max(1,len(ls)))
  for line in ls:
   prefix=next((p for p in ('Manufacturer:',rep) if line.startswith(p)),None)
   if prefix:
    c.setFont('ERPLabelBold',size);c.drawString(left,y,prefix)
    c.setFont('ERPLabel',size);c.drawString(left+pdfmetrics.stringWidth(prefix,'ERPLabelBold',size),y,line[len(prefix):])
   else:
    c.setFont('ERPLabelBold' if line==rep else 'ERPLabel',size)
    c.drawString(left,y,line)
   y-=leading
  # Five 8mm squares have 1.25mm gaps and 2.5mm outer margins; preserve image proportions.
  icons=['ukca.jpg','ce.jpg','age14.jpg','recycle.jpg','made-china.jpg'] if profile.get('show_marks',True) else ['made-china.jpg']
  for n,icon in enumerate(icons):
   slot=x+(2.5+n*9.25)*mm if len(icons)==5 else x+21*mm
   c.drawImage(str(ROOT/'assets/shipping'/icon),slot,1*mm,width=8*mm,height=8*mm,preserveAspectRatio=True,anchor='c',mask='auto')

 def separator(i,kind):
  x=newlabel();left=x+2*mm;c.setFont('ERPLabel',10);c.drawString(left,44*mm,kind)
  sections=[title,task_number,'FNSKU: '+i['fnsku'],'SKU: '+i['sku'],'品名: '+i['name'],'数量: '+str(i['quantity'])]
  size,ls=fit(sections,46*mm,36*mm);y=38*mm;c.setFont('ERPLabel',size)
  for line in ls:c.drawString(left,y,line);y-=size*1.12
 for i in items:
  if not i['quantity']:continue
  if separators:separator(i,'[开始]')
  for _ in range(i['quantity']):body(i)
  if separators:separator(i,'[结束]')
 c.save();return b.getvalue()


def packing_from_boxes(t,title,boxes):
 """Round-trip edited data through the same Excel validator used by uploads."""
 w=load_workbook(BytesIO(packing_template(t,title,len(boxes))));s=w.active
 for col,b in enumerate(boxes,6):
  quantities={i['sku']:i['quantity'] for i in b['items']}
  if len(quantities)!=len(b['items']) or set(quantities)-{i['sku'] for i in t.items}:raise ValueError('装箱商品重复或不属于当前计划')
  for row,i in enumerate(t.items,3):s.cell(row,col,quantities.get(i['sku'],0))
  for row,key in enumerate(['weight_kg','length_cm','width_cm','height_cm'],4+len(t.items)):s.cell(row,col,b[key])
 raw=BytesIO();w.save(raw);content=raw.getvalue()
 return content,parse_packing(content,t.items,f'PL{t.created_at:%y%m%d}{t.id:06d}')
