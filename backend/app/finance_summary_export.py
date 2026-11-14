"""Excel export uses explicit strings to prevent spreadsheet formula injection."""
from decimal import Decimal
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment
from openpyxl.utils import get_column_letter

def write_workbook(path,meta,result):
    book=Workbook();book.remove(book.active)
    def sheet(name,headers,rows):
        ws=book.create_sheet(name);ws.append(headers)
        for row in rows:
            ws.append(row)
            for cell in ws[ws.max_row]:
                if isinstance(cell.value,str):cell.data_type='s'
                elif isinstance(cell.value,(float,int,Decimal)):cell.number_format='#,##0.00;[Red]-#,##0.00'
        for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='217563')
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for i in range(1,len(headers)+1):ws.column_dimensions[get_column_letter(i)].width=50 if i==1 else 22
        for row in ws:
            for cell in row:cell.alignment=Alignment(vertical='top',wrap_text=True)
        return ws
    curr=result['currency']
    sheet('统一币种汇总',['财务项目','层级',f'金额（{curr}）','原始项目'],[
        ['财务净额',0,Decimal(result['net_proceeds']) if result['net_proceeds'] is not None else '未返回数据','netProceeds'],
        *[[r['path'],r['level'],Decimal(r['amount']),r['code_path']] for r in result['totals']]])
    sheet('站点原币汇总',['站点','账户类型','开始日期','结束日期','原币','原币净额',f'换算净额（{curr}）'],[[r['marketplace'],r['account'],r['start'],r['end'],r['original_currency'],Decimal(r['original']),Decimal(r['amount'])] for r in result['sites']])
    sheet('费用明细',['费用路径','站点','账户类型','层级','原币','原币金额',f'换算金额（{curr}）','原始项目'],[[r['path'],r['marketplace'],r['account'],r['level'],r['original_currency'],Decimal(r['original']),Decimal(r['amount']),r['code_path']] for r in result['details']])
    fx=result['fx']
    sheet('汇率与说明',['项目','内容'],[
        ['店铺',meta['store']],['站点',meta['marketplace'] or '全部适用站点（亚马逊返回）'],['日期范围',meta['start_date']+' 至 '+meta['end_date']],['展示币种',curr],['数据来源','Amazon Finances API listSummary'],['账户类型','全部返回账户，按账户类型保留明细'],['统计口径','按各站点当地日期统计财务活动，不是按订单下单日期统计。'],['净额口径','采用亚马逊 netProceeds；上下级费用存在包含关系，不可将所有行再次求和。净额不等于利润或银行到账。'],['空结果','未返回数据表示接口没有返回汇总，不代表经营金额为零。'],['汇率来源',fx['source']],['ECB汇率日期',fx.get('date') or '无'],['AED/SAR汇率日期',fx.get('aed_date') or '无'],['汇率读取时间',fx.get('fetched_at') or '无'],['汇率说明','采用本次任务读取的最新可用参考汇率；节假日可能沿用上一发布日，不是历史交易日汇率。'],['换算方式','原币金额 ÷ 原币兑欧元基准值 × 目标币兑欧元基准值；汇总后保留两位小数。'],['生成时间',str(meta['created_at'])],['任务号',meta['id']],*[[f'1 EUR = {c}',str(v)] for c,v in fx['rates'].items()]])
    book.save(path)
