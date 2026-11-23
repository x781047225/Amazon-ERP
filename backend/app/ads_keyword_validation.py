"""Known SP keyword syntax errors, checked before any Amazon request.
Source: https://advertising.amazon.com/library/guides/targeting-with-sponsored-products
"""
import unicodedata
from fastapi import HTTPException

BLOCKED=set('? /"&\\+<>()#@^!*'.replace(' ',''))
def keyword_error(text):
 value=' '.join(str(text or '').split())
 if not value:return '关键词不能为空'
 bad=list(dict.fromkeys(c for c in value if c in BLOCKED or unicodedata.category(c) in {'Sc','So','Cc','Cf'}))
 if bad:return '包含不支持的字符：'+'、'.join('不可见字符' if unicodedata.category(c) in {'Cc','Cf'} else c for c in bad)
 if len(value)>80:return f'关键词最多80个字符，当前{len(value)}个'
 if len(value.split())>10:return f'关键词最多10个单词，当前{len(value.split())}个'
 return ''
def validate_keyword(text):
 error=keyword_error(text)
 if error:raise HTTPException(422,f'关键词「{text}」：{error}')
