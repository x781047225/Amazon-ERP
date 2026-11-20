const descriptions:Record<string,string>={
 state:'控制开启或暂停。实际能否投放还受所属活动、广告组、预算和商品资格影响；归档后不可再开启。',
 country:'该广告账户所属的国家站点。',store:'该广告账户所属的店铺。',
 campaign_name:'所属广告活动的名称。',adgroup_name:'所属广告组的名称。',targeting_type:'自动投放由亚马逊匹配；手动投放使用设置的关键词或商品投放项。',
 budget:'广告活动设置的每日预算，以该广告账户币种计；不是当天已花费。广告页每5分钟检查预算和投放状态。修改后需预览并确认提交。',
 default_bid:'广告组默认竞价；未单独设置竞价的投放项使用此值。',bid:'该关键词或商品投放项设置的竞价，以广告账户币种计。',
 top_share:'符合条件的搜索结果顶部（首页）展示机会中，该活动或投放项获得的展示份额；以亚马逊报表返回值为准。',
 top_adjustment:'搜索结果顶部（首页）的竞价加价百分比，用于调整该位置的基础竞价。',
 start_date:'广告活动设置的开始日期。',end_date:'广告活动设置的结束日期；未设置时显示 —。',bidding_strategy:'活动设置的竞价方式，包括动态竞价和固定竞价等。',
 counts:'该广告组下的投放项数量和推广商品数量。',identifiers:'推广商品的 SKU、ASIN 和 FNSKU。未匹配到的标识显示 —。',
 stock_total:'FBA可售 + 接收中 + 入库中；缺少完整库存快照时显示 —。',
 product_name:'本地绑定标品的名称；无标品匹配时留空，不使用亚马逊标题代替。',available:'本店可售库存。英国单独统计；欧洲共享库存只取一份，德国优先，不累加各国重复库存。缺少快照显示 —。',
 image:'该推广商品在本地商品资料中的图片。',match_type:'关键词的广泛、词组、精准匹配，或商品投放的表达式类型。',
 suggestion:'亚马逊返回的建议竞价及最低、最高参考范围；应用只填入输入框，保存或按回车后直接提交，失败回退并提示。',target:'产生该搜索词表现的关键词或商品投放项。',
 impressions:'广告被展示的次数。',clicks:'广告获得的点击次数。',ctr:'点击率 = 点击量 ÷ 曝光量 × 100%。曝光为零或数据不足时显示 —。',
 cost:'所选日期内广告产生的花费，以广告账户币种计。',cpc:'平均点击成本 = 广告花费 ÷ 点击量。点击量为零或数据不足时显示 —。',
 purchases:'广告点击后7天归因窗口内的购买量，采用对应层级报表的汇总值。',sales:'广告点击后7天归因窗口内的销售额，以广告账户币种计。',
 acos:'ACOS = 广告花费 ÷ 归因销售额 × 100%。销售额为零或数据不足时显示 —。',roas:'ROAS = 归因销售额 ÷ 广告花费。花费为零或数据不足时显示 —。',cvr:'转化率 = 归因购买量 ÷ 点击量 × 100%。点击量为零或数据不足时显示 —。',
 actions:'对当前记录执行操作；从搜索词添加否定投放前需预览确认。',
 'extendedData.servingStatus':'亚马逊返回的实际投放状态。绿色表示正常，红色表示预算不足或受限，橙色表示等待或资料问题，灰色表示暂停、结束或未知。',
 'extendedData.servingStatusDetails':'亚马逊返回的投放状态原因明细，可能包含多项原因。',
 'extendedData.creationDateTime':'该广告对象在亚马逊中的创建时间。','extendedData.lastUpdateDateTime':'该广告对象在亚马逊中的最近更新时间。',
 campaignId:'广告活动在亚马逊中的唯一编号。',adGroupId:'广告组在亚马逊中的唯一编号。',adId:'广告在亚马逊中的唯一编号。',keywordId:'关键词在亚马逊中的唯一编号。',targetId:'投放项在亚马逊中的唯一编号。',portfolioId:'广告活动所属广告组合的编号。',
 'budget.budgetType':'亚马逊返回的预算类型。','dynamicBidding.placementBidding':'不同广告位置设置的竞价调整百分比。','dynamicBidding.shopperCohortBidding':'为指定受众设置的竞价加价。',
 'offAmazonSettings.offAmazonBudgetControlStrategy':'活动设置的站外投放预算控制策略。',expression:'该商品投放项的匹配条件。',expressionType:'投放条件的表达式类型。',resolvedExpression:'亚马逊解析后的投放条件。',
 created_at:'本系统记录该次操作的时间。',actor:'在本系统发起该次操作的用户。',changes:'本次修改的字段及修改前后的值。',status:'该次操作的处理结果；受理或提交中不代表已成功生效。',error:'该次操作返回的处理信息或失败原因。',placement:'广告展示位置；同一活动可设置多个位置的竞价调整。',adjustment:'该广告位置的竞价加价百分比。'
}
const aliases:Record<string,string>={servingStatus:'extendedData.servingStatus',creationDate:'extendedData.creationDateTime',lastUpdateDate:'extendedData.lastUpdateDateTime','budget.budget':'budget','dynamicBidding.strategy':'bidding_strategy',startDate:'start_date',endDate:'end_date',targetingType:'targeting_type'}
export function columnDescription(key:string,level='',label=''){
 key=key.replace(/^raw:/,'');key=aliases[key]||key
 if(key==='name')return level==='search'?'买家实际使用的搜索词。报表仅含产生点击的搜索词，曝光可能与活动报表不同。':level==='target'?'设置的关键词或商品投放条件。':level==='negative'?'用于排除不相关流量的否定关键词或商品。':'广告活动或广告组名称；点击名称可在独立页签查看详情。'
 if(descriptions[key])return descriptions[key]
 const match=key.match(/^(purchasesSameSku|purchases|unitsSoldClicks|unitsSoldSameSku|attributedSalesSameSku|sales|acosClicks|roasClicks)(\d+)d$/)
 if(match){const meanings:Record<string,string>={purchasesSameSku:'推广商品本身的归因订单数',purchases:'归因购买量',unitsSoldClicks:'归因销售件数',unitsSoldSameSku:'推广商品本身的归因销售件数',attributedSalesSameSku:'推广商品本身的归因销售额',sales:'归因销售额',acosClicks:'广告花费与归因销售额的比率（ACOS）',roasClicks:'归因销售额与广告花费的比率（ROAS）'};return `广告点击后${match[2]}天归因窗口内的${meanings[match[1]]}。未返回时显示 —。`}
 return `${label||key}：亚马逊返回的 ${key} 字段。以当前账户、日期和报表层级为准；未返回时显示 —。`
}
