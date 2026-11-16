"""Belgian attribute language regression. All Amazon traffic is mocked."""
from copy import deepcopy
import json
import pytest
from unittest.mock import MagicMock
from app import listing_amazon as amazon, listing_editor as editor, listing_submit as submit
from app.settings import MARKETS
from test_listing import payload, seed, read
from test_listing_submit import rich, Redis, body


def belgian():
    data=payload()
    data['summaries'][0]['marketplaceId']=MARKETS['BE']
    for values in data['attributes'].values():
        for value in values:
            value.update(language_tag='fr_BE',marketplace_id=MARKETS['BE'])
    data['attributes']['item_name'].append({'value':'French original','language_tag':'fr_FR','marketplace_id':MARKETS['BE']})
    tpl=rich();tpl['locale']='fr_FR'
    tpl['schema']['properties']['item_name']['maxItems']=2
    return data,tpl


def test_belgian_existing_values_and_blank_metadata():
    data,tpl=belgian();pk=seed(data,tpl,market='BE')
    fields={f['key']:f for f in read(pk)['edit_fields']}
    assert fields['item_name']['value']==[data['attributes']['item_name'][0]]
    assert fields['title_differentiation']['value'][0]['language_tag']=='fr_BE'
    row,template=submit.context(pk)
    proposed=editor.compile_changes(row,template,{'item_name':[{'value':'New title'}]})[0]
    assert proposed['item_name'][0]['language_tag']=='fr_BE'
    with pytest.raises(editor.EditError):
        editor.compile_changes(row,template,{'item_name':[{'value':'New title','language_tag':'fr_FR'}]})
    assert amazon.LOCALES['BE']=='fr_FR'
    assert amazon.ATTRIBUTE_LANGUAGES['BE']=='fr_BE'


@pytest.mark.parametrize('legacy',[False,True])
def test_belgian_preview_preserves_other_languages_and_rejects_old_preview(monkeypatch,legacy):
    data,tpl=belgian();pk=seed(data,tpl,market='BE');cache=Redis()
    monkeypatch.setattr(submit,'redis',cache)
    fetch=MagicMock(return_value=deepcopy(data))
    monkeypatch.setattr(amazon,'listing',fetch)
    definition=MagicMock(return_value=tpl)
    monkeypatch.setattr(amazon,'definition',definition)
    transport=MagicMock(side_effect=lambda *a,preview=False:{'status':'VALID' if preview else 'ACCEPTED','issues':[]})
    monkeypatch.setattr(submit,'patch_listing',transport)
    preview=submit.prepare(pk,body(pk))
    definition.assert_not_called() # presentation locale remains valid
    patch=transport.call_args.args[4][0]
    assert patch['value']==[data['attributes']['item_name'][1],{'value':'New title','marketplace_id':MARKETS['BE'],'language_tag':'fr_BE'}]
    key=submit.PREFIX+preview['preview_id'];cached=json.loads(cache.get(key))
    assert cached['attribute_language']=='fr_BE'
    if legacy:
        cached.pop('attribute_language');cache.set(key,json.dumps(cached));fetch.reset_mock()
        with pytest.raises(editor.EditConflict,match='重新预览'):
            submit.submit(pk,preview['preview_id'])
        assert transport.call_count==1 and cache.get(key) is None
        fetch.assert_not_called()
    else:
        assert submit.submit(pk,preview['preview_id'])['status']=='accepted'
        assert transport.call_count==2


@pytest.mark.parametrize('path,label',[('/catalog/2022-04-01/items/TEST','目录 ASIN'),('/listings/2021-08-01/items/SELLER/TEST','店铺 SKU')])
def test_not_found_diagnostic_is_specific_and_not_retried(monkeypatch,path,label):
    session=MagicMock();session.get.return_value.ok=False;session.get.return_value.status_code=404
    limiter=MagicMock();limiter.eval.return_value=0
    monkeypatch.setattr(amazon,'STORES',{'Demo':{'proxy_url':''}})
    monkeypatch.setattr(amazon,'redis',limiter)
    monkeypatch.setattr(amazon,'token',lambda *a:'dummy')
    monkeypatch.setattr(amazon,'build_request_proxies',lambda *a:{})
    monkeypatch.setattr(amazon.requests,'Session',lambda:session)
    with pytest.raises(amazon.AmazonError,match=label+'读取返回 HTTP 404'):
        amazon.get_json('Demo',path)
    assert session.get.call_count==1
