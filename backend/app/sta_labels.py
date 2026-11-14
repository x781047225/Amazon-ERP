"""Place original Amazon box labels on A4 without rasterizing barcodes."""
from io import BytesIO
from pypdf import PdfReader, PdfWriter, Transformation
from .sta_amazon import StaError

def a4_two_up(raw, expected_count):
 reader=PdfReader(BytesIO(raw))
 if len(reader.pages)!=expected_count:
  raise StaError('亚马逊返回的标签页数与箱数不一致，未生成两张一页文件，请选择原始标签格式')
 source=PdfWriter();source.append(reader)
 writer=PdfWriter();width,height=595.2756,841.8898; margin=18;slot_height=height/2-2*margin
 for index,page in enumerate(source.pages):
  if index%2==0:target=writer.add_blank_page(width,height)
  page.transfer_rotation_to_content()
  box=page.cropbox;w,h=float(box.width),float(box.height)
  if w<=0 or h<=0:raise StaError('亚马逊返回的标签页面尺寸无效')
  normal=min(1,(width-2*margin)/w,slot_height/h)
  rotated=min(1,(width-2*margin)/h,slot_height/w)
  transform=Transformation().translate(-float(box.left),-float(box.bottom))
  if rotated>normal:
   transform=transform.rotate(90).translate(h,0);w,h=h,w;scale=rotated
  else:scale=normal
  x=(width-w*scale)/2
  y=(height/2 if index%2==0 else 0)+margin+(slot_height-h*scale)/2
  target.merge_transformed_page(page,transform.scale(scale).translate(x,y))
 out=BytesIO();writer.write(out);return out.getvalue()
