"""Read-only MCP access to FBA invoices and customs declarations."""
from .integration_products import obj,S,ID,PAGE

FILTER={'q':{**S,'description':'模糊搜索 FBA 货件号'},'store':{**S,'description':'授权店铺名称，省略查询全部授权店铺'},'shipment_id':{**S,'description':'精确匹配 FBA 货件号'},**PAGE}
TOOLS=[
 ('fba_invoices_query','分页查询已保存的 FBA 清关发票，支持店铺及货件号筛选。返回单据 id、货件号、国家、箱数、数量、金额、税额、创建人和时间。只读，不创建或导出文件。','fba.invoice','view','GET','/api/logistics/invoices',obj(FILTER)),
 ('fba_invoice_detail','使用清关发票单据 id 查询当前详情（不是货件数据库 id），返回抬头、装箱商品、申报价格和金额等，口径与清关发票编辑页一致，引用信息可能随当前货件及合规资料更新。shipment 返回关联货件状态、目的仓、日期、数量与物流信息（需 fba.list.view 权限），箱数来自单据。只读，不保存或导出。','fba.invoice','view','GET','/api/logistics/invoices/{id}',obj({'id':ID},['id'])),
 ('fba_declarations_query','分页查询已生成的 FBA 报关单，支持店铺及货件号筛选，返回单据 id、货件号、文件名、创建人和时间。只读。','fba.declaration','view','GET','/api/logistics/declarations',obj(FILTER)),
 ('fba_declaration_detail','使用报关单单据 id 查询生成时保存的抬头和商品明细（不是货件数据库 id），header 包含币种、申报金额等已保存字段，items 包含商品、数量、价格、申报资料；不读取 Excel 二进制，不重算或修改单据；shipment 返回关联货件状态、目的仓、日期、数量与物流信息（需 fba.list.view 权限），箱数来自单据。','fba.declaration','view','GET','/api/logistics/declarations/{id}',obj({'id':ID},['id'])),
]
TOKEN_ROUTES={(method,path.replace('{id}',r'[1-9][0-9]*')) for _,_,_,_,method,path,_ in TOOLS}
