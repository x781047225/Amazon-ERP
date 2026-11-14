from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel,Field,ConfigDict

def web_link(value):
 if value:
  u=urlsplit(value)
  if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise ValueError('请输入有效的 http/https 链接')
 return value
class CustomsInfo(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 name_cn:str=Field(default='',max_length=500)
 name_en:str=Field(default='',max_length=500)
 material_cn:str=Field(default='',max_length=500)
 material_en:str=Field(default='',max_length=500)
 usage_cn:str=Field(default='',max_length=500)
 usage_en:str=Field(default='',max_length=500)
 brand_type:str=Field(default='',max_length=100)
 export_preference:str=Field(default='',max_length=100)
 internal_code:str=Field(default='',max_length=100)
 hs_code:str=Field(default='',max_length=30,pattern=r'^[0-9. ]*$')
 model:str=Field(default='',max_length=200)
 origin:str=Field(default='',max_length=100)
 domestic_source:str=Field(default='',max_length=200)
 unit:str=Field(default='',max_length=50)
 exemption:str=Field(default='',max_length=200)
 other_elements:str=Field(default='',max_length=2000)
 tax_rate:Decimal|None=Field(default=None,ge=0,le=100,decimal_places=4)
 unit_price:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=4)
 currency:Literal['CNY','EUR','GBP']='CNY'
