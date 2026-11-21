"""MCP product catalogue tools. All writes re-enter the guarded local REST API."""
from copy import deepcopy
from .products import ProductBody, Bind
from .product_data import SupplierBody, CustomsUpdate
from .customs_catalog import CodeBody
from .customs_profiles import Body as ProfileBody


def obj(properties,required=()):
 return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}

S={'type':'string'}
ID={'type':'integer','minimum':1}
PAGE={'page':ID,'page_size':{'type':'integer','minimum':1,'maximum':100}}
QUERY=obj({'q':S,**PAGE})
VERSION=obj({'revision':ID},['revision'])


def body_schema(model,update=False):
 # Pydantic refs are relative to the schema root, not the nested "body".
 # Inline definitions before embedding so both discovery and validation work.
 root=model.model_json_schema()
 def expand(value):
  if isinstance(value,list):return [expand(v) for v in value]
  if not isinstance(value,dict):return value
  if '$ref' in value:
   target=root
   for part in value['$ref'].removeprefix('#/').split('/'):target=target[part]
   return expand({**target,**{k:v for k,v in value.items() if k!='$ref'}})
  return {k:expand(v) for k,v in value.items() if k!='$defs'}
 schema=expand(root)
 if update:
  schema['properties']['revision']=deepcopy(ID)
  schema['required']=list(dict.fromkeys([*schema.get('required',[]),'revision']))
 return schema


def args(body=None,identified=False):
 props={'id':ID} if identified else {}
 if body is not None:props['body']=body
 return obj(props,list(props))

TOOLS=[]
def tool(name,description,key,action,method,path,schema):
 TOOLS.append((name,description,key,action,method,path,schema))

P='products.products'
LOCAL='仅修改 ERP 本地资料，不提交亚马逊。'
tool('products_query','查询标品：GM 编码、品名、供应商、未关联、无成本、无头程、无供应商、回收站筛选。',P,'view','GET','/api/products',obj({'q':S,'supplier':S,'archived':{'type':'boolean'},'unlinked':{'type':'boolean'},'review':{'type':'boolean'},'no_cost':{'type':'boolean'},'no_freight':{'type':'boolean'},'no_supplier':{'type':'boolean'},**PAGE}))
tool('product_detail','查看标品详情、GM 编码、资料和 revision；编辑前读取。',P,'view','GET','/api/products/{id}',args(identified=True))
tool('product_options','查询标品供应商和分类选项。',P,'view','GET','/api/products/options',obj({}))
tool('product_create','新增标品，GM 编码唯一。'+LOCAL,P,'operate','POST','/api/products',args(body_schema(ProductBody)))
tool('product_update','修改标品；先读详情，传入当前 revision、code、name 及要修改的字段，其他字段保留；customs_profile_id 可关联资料库。'+LOCAL,P,'operate','POST','/api/products/{id}',args(body_schema(ProductBody,True),True))
tool('product_archive','删除标品至回收站，仍有关联 SKU 时拒绝。需当前 revision。'+LOCAL,P,'operate','POST','/api/products/{id}/archive',args(VERSION,True))
tool('product_restore','恢复回收站标品；已合并记录不能恢复。需当前 revision。'+LOCAL,P,'operate','POST','/api/products/{id}/restore',args(VERSION,True))


def crud(prefix,label,key,path,model,delete_note=''):
 tool(prefix+'_query','分页搜索'+label+'。',key,'view','GET',path,QUERY)
 tool(prefix+'_detail','查看'+label+'详情和当前 revision。',key,'view','GET',path+'/{id}',args(identified=True))
 tool(prefix+'_create','新增'+label+'。'+LOCAL,key,'operate','POST',path,args(body_schema(model)))
 tool(prefix+'_update','修改'+label+'；先读详情，传入当前 revision 和必填字段，未指定的可选字段保留。'+LOCAL,key,'operate','POST',path+'/{id}',args(body_schema(model,True),True))
 tool(prefix+'_delete','删除'+label+'，需当前 revision。'+delete_note+LOCAL,key,'operate','POST',path+'/{id}/delete',args(VERSION,True))

crud('supplier','供应商','products.suppliers','/api/product-data/suppliers',SupplierBody,'仍被产品使用时拒绝；改名会更新产品中的供应商名称。')
for kind,label in [('clearance','清关编码'),('declaration','报关编码')]:
 crud(kind+'_code',label,'settings.'+kind,'/api/customs/codes/'+kind,CodeBody)
 # Additional exact field filters are supported by the existing UI/API.
 idx=next(i for i,t in enumerate(TOOLS) if t[0]==kind+'_code_query')
 t=TOOLS[idx];TOOLS[idx]=(*t[:-1],obj({'q':S,'name':S,'hs_code':S,**PAGE}))
CUSTOMS=('products.declaration','products.clearance')
crud('customs_profile','清关＆报关资料',CUSTOMS,'/api/customs/profiles',ProfileBody,'删除会解除资料库关联，产品中已保存的资料仍保留；修改资料库不会覆盖产品已有快照。')
for kind,label in [('declaration','报关'),('clearance','清关')]:
 tool('product_'+kind+'_update','修改指定标品的'+label+'资料快照，需当前 revision；未指定字段保留，共享资料字段按网页规则同步。'+LOCAL,'products.'+kind,'operate','POST','/api/product-data/{id}/'+kind,args(body_schema(CustomsUpdate,True),True))
LINKS=('products.products','listing')
tool('product_listings_query','查询授权店铺内的亚马逊 SKU 与标品关联；按店铺、站点、SKU/ASIN 及 product_id 筛选，返回 listing id、当前 product_id、GM编码和品名。查询是否关联时请传 state=all，省略时默认只查未关联项。',LINKS,'view','GET','/api/products/listings',obj({'q':S,'store':S,'marketplace':S,'state':{'type':'string','enum':['all','unlinked','linked']},'product_id':ID,**PAGE}))
tool('product_listing_link','查看单个授权 Listing 的 SKU 和当前标品关联。',LINKS,'view','GET','/api/products/listing-link/{id}',args(identified=True))
tool('product_links_apply','批量绑定、换绑或解绑亚马逊 SKU 与标品。先查询关联；每项使用 listing_id 和当前 expected_product_id（未绑定为 null）；product_id=null 表示解绑。最多100项，任一越权或关联冲突则全部不保存。'+LOCAL,LINKS,'operate','POST','/api/products/links/apply',args(body_schema(Bind)))

# Restrict bearer REST access to the exact same methods and paths as these tools.
TOKEN_ROUTES={(method,path.replace('{id}',r'[1-9][0-9]*')) for _,_,_,_,method,path,_ in TOOLS}
DESTRUCTIVE={name for name,_,_,action,_,_,_ in TOOLS if action=='operate' and not name.endswith('_create')}
