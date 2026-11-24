"""Purchasing and shipping MCP tools reuse the authenticated local APIs."""
from .integration_products import obj,args,body_schema,S,ID,PAGE,VERSION
from .procurement_batches import NewPlanBatch,BatchEdit,BulkNewPlans
from .procurement import Selection,Generate,UpdateDocument,BatchReceipt
from .procurement_flow import NewDocument,DeleteDocument
from .shipping import TaskBody

TOOLS=[]
def tool(name,description,key,action,method,path,schema):
 TOOLS.append((name,description,key,action,method,path,schema))
CODE={'type':'string','minLength':1,'maxLength':40,'pattern':'^[A-Za-z0-9_-]+$'}
def batch_args(body=None):
 props={'code':CODE}
 if body is not None:props['body']=body_schema(body)
 return obj(props,list(props))
PLAN='procurement.plans';ORDER='procurement.orders';SHIP='shipping'
FILTER={'q':S,'store':S,'country':S,'channel':S,'warehouse_id':ID,'status':S,**PAGE}
LOCAL='仅操作 ERP 本地单据，不向亚马逊提交货件。'
tool('planning_products_query','按 GM 编码或品名查找标品，供采购和发货计划选择。',(PLAN,ORDER,SHIP),'view','GET','/api/products',obj({'q':S,**PAGE}))
tool('purchase_options','查询授权店铺、仓库、渠道和采购员选项。',(PLAN,ORDER),'view','GET','/api/procurement/options',obj({}))
tool('purchase_plans_query','查询采购计划，grouped=true 按批次返回，包含备注及明细版本。',PLAN,'view','GET','/api/procurement/plans',obj({**FILTER,'grouped':{'type':'boolean'}}))
tool('purchase_plan_detail','按批次号查看采购计划的全部明细、备注和 revision。',PLAN,'view','GET','/api/procurement/plan-batches/{code}',batch_args())
tool('purchase_plan_create','新建一个标品的多渠道采购计划，支持备注。request_id 为唯一 UUID，同请求重试不重复创建。'+LOCAL,PLAN,'operate','POST','/api/procurement/plan-batches',args(body_schema(NewPlanBatch)))
tool('purchase_plans_bulk_create','批量新增采购计划，items 最多200项，每项包含一个标品及多个渠道，每项单独生成批次号。全部成功或全部回滚；request_id 为唯一 UUID，原请求重试不重复创建。返回 plans 按输入 index 对应批次号及明细 ids。'+LOCAL,PLAN,'operate','POST','/api/procurement/plan-batches/bulk',args(body_schema(BulkNewPlans)))
tool('purchase_plan_update','整批编辑采购计划。先读详情，plans 提交全部 id/revision，items 为完整保留明细（新增明细不传 id）；缺少的明细会被移除。已生成采购单或发货计划时拒绝。'+LOCAL,PLAN,'operate','POST','/api/procurement/plan-batches/{code}/update',batch_args(BatchEdit))
tool('purchase_plan_delete','删除整批采购计划；先读详情，plans 须包含全部 id/revision，已处理或已安排发货时拒绝。'+LOCAL,PLAN,'operate','POST','/api/procurement/plan-batches/{code}/delete',batch_args(Selection))
tool('purchase_plan_logs','查看采购计划操作人、时间和修改日志。',PLAN,'view','GET','/api/procurement/plan-batches/{code}/logs',obj({'code':CODE,**PAGE},['code']))
tool('purchase_plan_order_preview','预览所选计划生成采购单，带出供应商、成本和按北京时间今天加供应商发货周期的预计到货日，未配置则留空。',PLAN,'operate','POST','/api/procurement/preview',args(body_schema(Selection)))
tool('purchase_plan_generate_orders','将已预览计划生成采购单；传全部分组及确认的预计到货日。同供应商共用日期。request_id 保证重试不重复创建。'+LOCAL,PLAN,'operate','POST','/api/procurement/generate',args(body_schema(Generate)))
tool('purchase_orders_query','查询采购单；grouped=true 按整张单据分页，结果 rows 为各商品行，number 相同属于一张单据。',ORDER,'view','GET','/api/procurement/orders',obj({**FILTER,'grouped':{'type':'boolean'}}))
tool('purchase_order_detail','按任一商品行 id 查看整张采购单，返回所有明细 id、revision、到货量。',ORDER,'view','GET','/api/procurement/orders/{id}/detail',args(identified=True))
tool('purchase_order_create','新建采购单，按供应商和到货日期合单；设为待到货并记录本地待到货库存。request_id 为唯一 UUID。'+LOCAL,ORDER,'operate','POST','/api/procurement/flow/orders',args(body_schema(NewDocument)))
tool('purchase_orders_bulk_create','批量新增采购单，rows 最多200条商品明细；相同供应商和预计到货日期合为一张单，不同组合生成多张。全部成功或全部回滚；request_id 为唯一 UUID，原请求重试不重复创建。创建后为待到货并记录待到货库存，返回 orders 含单号和明细 id。'+LOCAL,ORDER,'operate','POST','/api/procurement/flow/orders',args(body_schema(NewDocument)))
tool('purchase_order_update','整单编辑。先读详情，orders 传全部明细及 revision；增加 received 会登记到货并增加本地库存，保持原值则不入库。已到货量不能减少。'+LOCAL,ORDER,'operate','POST','/api/procurement/orders/{id}/document',args(body_schema(UpdateDocument),True))
tool('purchase_orders_receive','登记采购单到货，支持单条或批量，orders 最多200条。先查询详情取得明细 id/revision；quantity 是本次新增到货量，不是累计到货量。自动增加本地库存并更新待到货/已到货状态；不可超收。全部成功或全部回滚，request_id 为唯一 UUID，原请求重试不重复入库。'+LOCAL,ORDER,'operate','POST','/api/procurement/orders/batch-receive',args(body_schema(BatchReceipt)))
tool('purchase_order_delete','删除整张采购单。先读详情，items 须传全部明细 id/revision；已有到货或发货计划则拒绝。删除后撤销待到货库存并恢复来源采购计划。'+LOCAL,ORDER,'operate','POST','/api/procurement/flow/orders/{id}/delete',args(body_schema(DeleteDocument),True))
tool('purchase_order_logs','查看采购单操作人、时间及业务日志。',ORDER,'view','GET','/api/procurement/orders/{id}/logs',obj({'id':ID,**PAGE},['id']))
tool('shipping_plan_options','查询授权店铺、仓库、国家和发货渠道。',SHIP,'view','GET','/api/shipping/options',obj({}))
tool('shipping_plans_query','分页查询发货计划，返回明细、来源及 revision。',SHIP,'view','GET','/api/shipping',obj(FILTER))
tool('shipping_plan_detail','查询发货计划详情和当前 revision。',SHIP,'view','GET','/api/shipping/{id}/detail',args(identified=True))
tool('shipping_plan_create','新增发货计划；lock_inventory 默认 true 会校验并锁定本地库存，false 可先建立计划。request_id 保证同请求重试不重复创建。'+LOCAL,SHIP,'operate','POST','/api/shipping',args(body_schema(TaskBody)))
tool('shipping_plan_update','编辑发货计划，传当前 revision 和完整 items；库存已锁定或已装箱时拒绝，先在 ERP 处理。'+LOCAL,SHIP,'operate','POST','/api/shipping/{id}/change',args(body_schema(TaskBody,True),True))
tool('shipping_plan_delete','删除发货计划，需当前 revision；已装箱等不可删除状态仍由原流程校验，释放本地库存锁定。'+LOCAL,SHIP,'operate','POST','/api/shipping/{id}/delete',args(VERSION,True))
tool('shipping_plan_logs','查询发货计划的操作人、时间与业务日志。',SHIP,'view','GET','/api/shipping/{id}/logs',obj({'id':ID,**PAGE},['id']))
TOKEN_ROUTES={(m,p.replace('{id}',r'[1-9][0-9]*').replace('{code}',r'[A-Za-z0-9_-]{1,40}')) for _,_,_,_,m,p,_ in TOOLS}
DESTRUCTIVE={n for n,_,_,a,_,_,_ in TOOLS if a=='operate' and not n.endswith('_create')}
