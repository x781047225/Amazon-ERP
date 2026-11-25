"""Logistics-channel MCP operations reuse local APIs, validation and audit."""
from .integration_products import obj,args,body_schema,S,ID,PAGE,VERSION
from .logistics import ChannelBody
KEY='logistics.channels'
TOOLS=[
 ('logistics_channel_options','查询物流渠道可用国家、运输方式、包税和尾端派送选项。',KEY,'view','GET','/api/logistics/options',obj({})),
 ('logistics_channels_query','分页查询物流渠道，支持货代名称、国家和渠道筛选。返回当前 revision。',KEY,'view','GET','/api/logistics/channels',obj({'q':S,'country':S,'channel':S,**PAGE,'paginated':{'type':'boolean','const':True}},['paginated'])),
 ('logistics_channel_detail','查看物流渠道完整资料和当前 revision。',KEY,'view','GET','/api/logistics/channels/{id}',args(identified=True)),
 ('logistics_channel_create','新增物流渠道，金额使用指定币种。仅修改 ERP 本地资料，不提交亚马逊。',KEY,'operate','POST','/api/logistics/channels',args(body_schema(ChannelBody))),
 ('logistics_channel_update','修改物流渠道；先读详情，提交当前 revision 和完整渠道字段。仅修改 ERP 本地资料，已有结算快照保持不变。',KEY,'operate','POST','/api/logistics/channels/{id}',args(body_schema(ChannelBody,True),True)),
 ('logistics_channel_delete','删除物流渠道，须提供当前 revision。已有结算保留当时的渠道及费用快照。',KEY,'operate','POST','/api/logistics/channels/{id}/delete',args(VERSION,True)),
]
TOKEN_ROUTES={(m,p.replace('{id}',r'[1-9][0-9]*')) for _,_,_,_,m,p,_ in TOOLS}
DESTRUCTIVE={'logistics_channel_update','logistics_channel_delete'}
