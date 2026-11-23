// SP keyword limits: https://advertising.amazon.com/library/guides/targeting-with-sponsored-products
// Check known unsupported characters; do not reject ordinary Unicode letters or apostrophes.
export function keywordError(text:string):string{
 const value=String(text??'').trim().replace(/\s+/g,' ')
 if(!value)return '关键词不能为空'
 const bad=[...new Set([...value].filter(c=>/[\p{Sc}\p{So}\p{Cc}\p{Cf}?/"&\\+<>()#@^!*]/u.test(c)))]
 if(bad.length)return '包含不支持的字符：'+bad.map(c=>/[\p{Cc}\p{Cf}]/u.test(c)?'不可见字符':c).join('、')
 if([...value].length>80)return `关键词最多80个字符，当前${[...value].length}个`
 if(value.split(' ').length>10)return `关键词最多10个单词，当前${value.split(' ').length}个`
 return ''
}
