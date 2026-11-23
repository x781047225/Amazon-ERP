"""MCP access to the existing scoped, durable B2C pricing workflow."""
from .listing_api import QueuedPriceOnly, QueuedPromotion

ID={'type':'integer','minimum':1}
UUID={'type':'string','format':'uuid'}
def obj(properties,required):
 return {'type':'object','properties':properties,'required':required,'additionalProperties':False}
def body_schema(model):return obj({'id':ID,'body':model.model_json_schema()},['id','body'])

TOOLS=[
 ('listing_price_get','查询已同步的售价、促销价、含时区的促销起止时间、币种、价格 fingerprint；price_source=accepted_submission 表示已受理的提交价格，尚未回读证实，不是亚马逊实时价格。调价前核对店铺、站点、SKU 和价格。','listing','view','GET','/api/listings/{id}/price',obj({'id':ID},['id'])),
 ('listing_price_refresh','从亚马逊回读当前商品价格并刷新 ERP 缓存，不修改亚马逊。调价前用返回的 currency、fingerprint 作为 expected_currency、expected_fingerprint；提交后可回读再查任务，亚马逊受理不代表已经生效。','listing','operate','POST','/api/listings/{id}/price/refresh',obj({'id':ID},['id'])),
 ('listing_price_submit','用户明确授权具体商品和售价后，直接提交后台调价任务。只修改普通买家售价，保留促销计划、企业价及其他属性。price 为本币含税金额字符串；先查询价格并传 expected_currency、expected_fingerprint。request_id 使用 UUID，同一次操作重试必须复用原编号；返回 queued/accepted 不等于已生效，用 listing_price_job 查询结果，unknown 不得换编号盲目重试。','listing','operate','POST','/api/listings/{id}/price-only/queue',body_schema(QueuedPriceOnly)),
 ('listing_promotion_submit','用户明确授权具体商品、促销价和起止时间后，直接提交后台促销任务。省略 price 只改促销；仅在同时获准调整售价时填写 price。保留企业价和其他属性。sale_price 为本币含税金额字符串，必须低于售价，sale_start/sale_end 必须含时区且结束时间有效。先查询价格并传 expected_currency、expected_fingerprint。request_id 使用 UUID，重试复用原编号；通过 listing_price_job 查询结果，unknown 不得盲目重试。此功能设置促销售价，不是优惠券或秒杀。','listing','operate','POST','/api/listings/{id}/promotion/queue',body_schema(QueuedPromotion)),
 ('listing_price_job','查询当前商品的调价任务及价格；queued/submitting 为处理中，accepted 仅表示亚马逊受理，verified 表示同步回读一致，rejected 为拒绝，unknown 需回读核对且不可盲目重试，superseded 表示已有后续提交。不会主动回读亚马逊，可先调用 listing_price_refresh。','listing','view','GET','/api/listings/{id}/price/jobs/{submission_id}',obj({'id':ID,'submission_id':UUID},['id','submission_id'])),
]
DESTRUCTIVE={'listing_price_submit','listing_promotion_submit'}
TOKEN_ROUTES={
 ('GET',r'/api/listings/\d+/price'),
 ('GET',r'/api/listings/\d+/price/jobs/[0-9a-f-]+'),
 ('POST',r'/api/listings/\d+/price/refresh'),
 ('POST',r'/api/listings/\d+/price-only/queue'),
 ('POST',r'/api/listings/\d+/promotion/queue'),
}
