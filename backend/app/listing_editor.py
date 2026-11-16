"""First-batch attributes. Product-template driven editors; no inferred compliance facts."""
import copy,html,re,math
from jsonschema import Draft201909Validator,validators,ValidationError
from .settings import MARKETS
from .listing_amazon import ATTRIBUTE_LANGUAGES
FIELDS={
'item_name':'标题','title_differentiation':'亮点','bullet_point':'五点','product_description':'长描述','generic_keyword':'关键词','color':'颜色','material':'材质','model_number':'型号','model_name':'型号名称','item_dimensions':'产品长宽高','item_length_width_height':'产品长宽高','item_length_width':'产品长宽（品类尺寸）','item_width_height':'产品宽高（品类尺寸）','item_display_dimensions':'产品展示尺寸','item_weight':'产品重量（不含包装）','item_package_dimensions':'运输包装长宽高','item_package_weight':'运输包装重量','unit_count':'单位计数及类型','included_components':'包装组成','country_of_origin':'产地','batteries_required':'是否需要电池','supplier_declared_dg_hz_regulation':'危险商品规管','gpsr_safety_attestation':'GPSR 安全认证','dsa_responsible_party_address':'负责人的电子邮件或电子地址','gpsr_manufacturer_reference':'GPSR 制造商参考'}
COMPLIANCE={'country_of_origin','batteries_required','supplier_declared_dg_hz_regulation','gpsr_safety_attestation','dsa_responsible_party_address','gpsr_manufacturer_reference'}
DIMENSION_FIELDS={'item_dimensions','item_length_width_height','item_length_width','item_width_height','item_display_dimensions'}
METADATA={'marketplace_id','language_tag'}
class EditError(Exception):pass
class EditConflict(EditError):pass

def resolve(node,root):
    n=copy.deepcopy(node)
    if '$ref' in n:
        ref=n.pop('$ref')
        if not ref.startswith('#/'):raise EditError('模板包含不支持的外部引用，请先同步模板。')
        target=root
        for part in ref[2:].split('/'):target=target[part.replace('~1','/').replace('~0','~')]
        n={**resolve(target,root),**n}
    return n

def expand(node,root,depth=0):
    if depth>12:raise EditError('模板结构过深，暂不支持编辑。')
    n=resolve(node,root)
    if 'properties' in n:n['properties']={k:expand(v,root,depth+1) for k,v in n['properties'].items()}
    if 'items' in n and isinstance(n['items'],dict):n['items']=expand(n['items'],root,depth+1)
    for key in ('anyOf','oneOf','allOf'):
        if key in n:n[key]=[expand(v,root,depth+1) for v in n[key]]
    return n

def supported(n):
    if n.get('readOnly') or n.get('editable') is False:return False
    kind=n.get('type')
    if kind=='object':return all(supported(v) for k,v in n.get('properties',{}).items() if k not in METADATA and not v.get('hidden'))
    if kind=='array':return supported(n.get('items',{}))
    # Suggested anyOf enum + free string remains a text field; ambiguous branches stay read-only.
    if 'oneOf' in n:return False
    return kind in ('string','number','integer','boolean')

def blank(n,market):
    kind=n.get('type')
    if kind=='object':
        result={}
        for k,v in n.get('properties',{}).items():
            if k=='marketplace_id':result[k]=MARKETS[market]
            elif k=='language_tag':result[k]=ATTRIBUTE_LANGUAGES[market]
            elif not v.get('hidden'):result[k]=blank(v,market)
        return result
    if kind=='array':return [blank(n.get('items',{}),market)]
    return '' if kind=='string' else None

def scope(entry,market):
    return isinstance(entry,dict) and entry.get('marketplace_id',MARKETS[market])==MARKETS[market] and entry.get('language_tag',ATTRIBUTE_LANGUAGES[market])==ATTRIBUTE_LANGUAGES[market]

def editor_fields(row,template):
    root=template.schema_data if template else {};out=[]
    for key,label in FIELDS.items():
        raw=copy.deepcopy(row.attributes.get(key,[]));prop=root.get('properties',{}).get(key)
        if key in DIMENSION_FIELDS and not prop and not raw:
            if key!='item_dimensions' or any(k in root.get('properties',{}) for k in DIMENSION_FIELDS):continue
        n=expand(prop,root) if prop else None
        editable=bool(n and supported(n) and not row.template_error)
        target=[x for x in raw if scope(x,row.marketplace)] if isinstance(raw,list) else []
        value=copy.deepcopy(target) if target else (blank(n,row.marketplace) if n else [])
        if key=='bullet_point' and n:
            while len(value)<5:value.append(blank(n['items'],row.marketplace))
        out.append({'key':key,'label':label,'section':'compliance' if key in COMPLIANCE else 'basic','editable':editable,'reason':None if editable else '当前品类模板未提供此字段' if not n else '此模板结构或权限暂不支持编辑，请同步后核对','schema':n,'value':value,'raw':raw,'description':n.get('description','') if n else ''})
    return out

def utf8(validator,maximum,instance,schema):
    if isinstance(instance,str) and len(instance.encode('utf-8'))>maximum:yield ValidationError(f'UTF-8 字节数超过 {maximum}')
Validator=validators.extend(Draft201909Validator,{'maxUtf8ByteLength':utf8})

def protect(node,before,after,path=''):
    """Opaque/read-only values survive edits; hidden metadata cannot be changed."""
    if node.get('readOnly') or node.get('editable') is False:
        if before!=after:raise EditError(f'{path} 为只读字段。')
    if isinstance(after,dict):
        props=node.get('properties',{})
        for k,v in after.items():
            if k not in props and (not isinstance(before,dict) or before.get(k)!=v):raise EditError(f'{path}/{k} 不是本次支持的字段。')
            if k in props and k not in METADATA:protect(props[k],before.get(k) if isinstance(before,dict) else None,v,path+'/'+k)
        for k,v in (before.items() if isinstance(before,dict) else []):
            if k not in after and (k not in props or props[k].get('editable') is False or props[k].get('hidden')):raise EditError(f'{path}/{k} 不可删除。')
    elif isinstance(after,list):
        for i,v in enumerate(after):protect(node.get('items',{}),before[i] if isinstance(before,list) and i<len(before) else None,v,path+f'/{i}')
    elif isinstance(after,float) and not math.isfinite(after):raise EditError(f'{path} 必须为有限数值。')

def sanitize_metadata(n,value,market):
    if isinstance(value,dict):
        result={}
        for k,v in value.items():
            if k=='marketplace_id':
                if v!=MARKETS[market]:raise EditError('不能修改站点标识。')
            if k=='language_tag':
                if v!=ATTRIBUTE_LANGUAGES[market]:raise EditError('不能修改语言标识。')
            result[k]=sanitize_metadata(n.get('properties',{}).get(k,{}),v,market)
        for k in n.get('properties',{}):
            if k=='marketplace_id':result[k]=MARKETS[market]
            if k=='language_tag':result[k]=ATTRIBUTE_LANGUAGES[market]
        return result
    if isinstance(value,list):return [sanitize_metadata(n.get('items',{}),v,market) for v in value]
    return value

def compile_changes(row,template,changes):
    if not template or row.template_error:raise EditError('请先成功同步产品类型模板。')
    fields={f['key']:f for f in editor_fields(row,template)};out={};diff=[];counts={};warnings=[]
    if not changes or len(changes)>len(FIELDS):raise EditError('没有可提交的修改，或字段数量超出范围。')
    for key,values in changes.items():
        f=fields.get(key)
        if not f or not f['editable']:raise EditError(f'{FIELDS.get(key,key)} 不在当前可提交范围。')
        if not isinstance(values,list) or not values:raise EditError(f'{f["label"]} 不能整项删除；请填写有效值。')
        n=f['schema'];new=sanitize_metadata(n,copy.deepcopy(values),row.marketplace)
        old=[x for x in row.attributes.get(key,[]) if scope(x,row.marketplace)]
        if key=='item_display_dimensions':
            # Optional axes left blank in a new entry are not submitted as null measurements.
            for i,entry in enumerate(new):
                prior=old[i] if i<len(old) else {}
                for axis in list(entry):
                    value=entry[axis]
                    if axis not in prior and axis not in METADATA and isinstance(value,dict) and all(v is None or v=='' for v in value.values()):entry.pop(axis)
                if not any(axis not in METADATA for axis in entry):raise EditError('产品展示尺寸请至少填写一个尺寸及单位。')
        protect(n,old,new,key)
        errors=list(Validator(n).iter_errors(new))
        if errors:
            e=errors[0];raise EditError(f'{f["label"]} / {"/".join(map(str,e.absolute_path))}：{e.message}')
        maximum=min(n.get('maxItems',1000),n.get('maxUniqueItems',1000))
        if len(new)>maximum:raise EditError(f'{f["label"]} 最多 {maximum} 条。')
        if key=='bullet_point' and (len(new)!=5 or any(not str(x.get('value','')).strip() for x in new)):raise EditError('五点需填写完整 5 条非空内容。')
        if key in ('item_name','title_differentiation','product_description','generic_keyword') and any(not str(x.get('value','')).strip() for x in new):raise EditError(f'{f["label"]} 不能为空。')
        if key in DIMENSION_FIELDS|{'item_weight','item_package_dimensions','item_package_weight','unit_count'}:
            def positive(v):
                if isinstance(v,dict):
                    for k,z in v.items():
                        if k=='value' and type(z) in (int,float) and z<=0:raise EditError(f'{f["label"]} 必须为正数。')
                        positive(z)
                if isinstance(v,list):
                    for z in v:positive(z)
            positive(new)
        if key=='gpsr_manufacturer_reference' and any(not str(x.get('gpsr_manufacturer_email_address','')).strip() for x in new):raise EditError('请填写制造商的电子邮件或电子地址。')
        if new==old:continue
        # Replace only the selected language/market instances; other instances are not sent.
        out[key]=new;diff.append({'key':key,'label':f['label'],'before':old,'after':new,'schema':n})
        texts=[x.get('value') for x in new if isinstance(x.get('value'),str)]
        if texts:
            visible=[html.unescape(re.sub('<[^>]+>','',t)) if key=='product_description' else t for t in texts]
            counts[key]={'characters':[len(t) for t in visible],'utf8_bytes':[len(t.encode('utf-8')) for t in texts]}
            if key=='item_name' and not 70<=len(visible[0])<=75:warnings.append('标题个人目标为 70–75 字符；当前 '+str(len(visible[0]))+' 字符。')
            if key=='title_differentiation' and not 110<=len(visible[0])<=125:warnings.append('亮点个人目标为 110–125 字符；当前 '+str(len(visible[0]))+' 字符。')
            if key=='bullet_point' and any(len(t)<250 for t in visible):warnings.append('部分五点少于个人写作目标 250 字符。')
            if key=='product_description' and len(visible[0])<3000:warnings.append('长描述可见字符少于个人写作目标 3000 字符。')
    if not out:raise EditError('没有实际变化。')
    # Validate newly introduced cross-field requirements without blocking unrelated legacy defects.
    root=template.schema_data;baseline=row.attributes;combined=copy.deepcopy(baseline)
    for k,v in out.items():combined[k]=[x for x in baseline.get(k,[]) if not scope(x,row.marketplace)]+v
    validator=Validator(root)
    old_errors={(tuple(e.absolute_schema_path),e.message) for e in validator.iter_errors(baseline)}
    added=[e for e in validator.iter_errors(combined) if (tuple(e.absolute_schema_path),e.message) not in old_errors]
    if added:
        message=added[0].message.replace("'batteries_included' is a required property",'选择需要电池后，还必须填写是否随附电池；此关联字段本批尚未开放，请先在亚马逊补充并同步。')
        raise EditError('模板关联校验：'+message)
    return out,diff,counts,warnings
