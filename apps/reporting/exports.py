"""Scoped, consistent CSV/XLSX/PDF exports from the exact displayed projection or frozen snapshot."""
import base64,csv,io,math
from datetime import datetime
from decimal import Decimal
from xml.sax.saxutils import escape
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied,ValidationError
from django.http import HttpResponse,StreamingHttpResponse
from django.utils import timezone
from django.utils.text import slugify
from apps.workdays.reports import safe
from .access import staff

FORMATS=("csv","xlsx","pdf")

def metadata(title,filters,*,status="Live calculated report",version="",source="Authoritative platform records"):
    return {"Report":title,"Reporting period":str(filters.get("start") or "Any")+" through "+str(filters.get("end") or "Any"),
        "Applied filters":"; ".join(k+"="+str(v) for k,v in filters.items() if v not in (None,"")) or "None",
        "Generated at":timezone.now().isoformat(),"Status":status,"Version":str(version or "Not finalized"),"Source":source}

def authorize(actor,*,private=False):
    actor=staff(actor)
    if not private and not actor.has_perm("reporting.export_reports"):
        raise PermissionDenied("Report export permission and all underlying report permissions are required.")
    return actor

def money_display(value):
    return "Unavailable" if value is None else "$"+f"{Decimal(str(value)):,.2f}"

def cell_kind(column,row):
    return ("money" if row.get("unit")=="USD" else "number") if column[2]=="metric" else column[2]

def display(value):
    if value is None:return "Unavailable"
    return str(value)


def export_response(*,actor,format,title,columns,rows,meta,private=False):
    authorize(actor,private=private)
    if format not in FORMATS:raise ValidationError("Choose PDF, Excel or CSV.")
    filename=slugify(title)[:80] or "btw-report"
    headers=[c[1] for c in columns]
    if format=="csv":
        class Echo:
            def write(self,value):return value
        writer=csv.writer(Echo())
        def output():
            yield "\ufeff"
            for key,value in meta.items():yield writer.writerow([safe(key),safe(value)])
            yield writer.writerow([]);yield writer.writerow(headers)
            for row in rows:yield writer.writerow([f"{Decimal(str(row[c[0]])):.2f}" if cell_kind(c,row)=="money" and row.get(c[0]) is not None else display(row.get(c[0])) if cell_kind(c,row)=="number" and row.get(c[0]) is not None else safe(display(row.get(c[0]))) for c in columns])
        response=StreamingHttpResponse(output(),content_type="text/csv; charset=utf-8")
    elif format=="xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font,PatternFill,Alignment
        from openpyxl.utils import get_column_letter
        book=Workbook();sheet=book.active;sheet.title="BTW Report"
        for key,value in meta.items():sheet.append([safe(key),safe(value)])
        sheet.append([]);sheet.append(headers);header_row=sheet.max_row
        for cell in sheet[header_row]:cell.font=Font(bold=True,color="FFFFFF");cell.fill=PatternFill("solid",fgColor="181818")
        for row in rows:
            values=[]
            for column in columns:
                key,label,kind=column;kind=cell_kind(column,row)
                value=row.get(key)
                if value is None:value="Unavailable"
                elif kind in ("money","number"):value=Decimal(str(value))
                elif isinstance(value,str):value=safe(value)
                values.append(value)
            sheet.append(values)
            for index,c in enumerate(columns,1):
                if cell_kind(c,row)=="money" and isinstance(sheet.cell(sheet.max_row,index).value,(Decimal,int,float)):sheet.cell(sheet.max_row,index).number_format='$#,##0.00;[Red]-$#,##0.00'
        sheet.freeze_panes="A"+str(header_row+1);sheet.auto_filter.ref=f"A{header_row}:{get_column_letter(len(columns))}{sheet.max_row}"
        for i,c in enumerate(columns,1):sheet.column_dimensions[get_column_letter(i)].width=28 if c[2]=="text" else 18
        sheet.sheet_properties.pageSetUpPr.fitToPage=True;sheet.page_setup.orientation="landscape";sheet.page_setup.paperSize=sheet.PAPERSIZE_A4;sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
        stream=io.BytesIO();book.save(stream);response=HttpResponse(stream.getvalue(),content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        import cairosvg
        from pypdf import PdfReader,PdfWriter
        # Standard vector PDF pages; no remote URLs, browser state, or external rendering service.
        width,height=1123,794;left=36;usable=width-left*2;colwidth=usable/len(columns)
        meta_lines=[]
        for key,value in meta.items():
            text=key+": "+str(value)
            if len(text)>500:text=text[:440]+"... Full metadata is retained in Excel/CSV."
            meta_lines.extend(text[i:i+145] for i in range(0,len(text),145))
        table_y=max(94+14*len(meta_lines)+18,234)
        page_rows=max(1,int((height-60-(table_y+29))/28))
        writer=PdfWriter();batch=[];page=0
        logo_path=finders.find("internal/images/built-to-work-linear.svg")
        logo="data:image/png;base64,"+base64.b64encode(cairosvg.svg2png(url=logo_path,output_width=800)).decode() if logo_path else ""
        def page_pdf(items,page):
            out=[f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',f'<rect width="{width}" height="72" fill="#181818"/>',f'<image x="36" y="12" width="200" height="25" xlink:href="{logo}"/>',f'<text x="36" y="56" font-family="sans-serif" font-size="13" fill="white">{escape(title[:100])}</text>']
            y=94
            for text in meta_lines:
                out.append(f'<text x="36" y="{y}" font-family="sans-serif" font-size="10">{escape(text)}</text>');y+=14
            y=max(y+18,234)
            for index,c in enumerate(columns):out.append(f'<text x="{left+index*colwidth}" y="{y}" font-family="sans-serif" font-size="9" font-weight="bold">{escape(c[1][:26])}</text>')
            y+=10;out.append(f'<path d="M36 {y}H{width-36}" stroke="#999"/>');y+=19
            for row in items:
                for index,c in enumerate(columns):
                    value=row.get(c[0]);text=("$"+f"{Decimal(str(value)):,.2f}") if value is not None and cell_kind(c,row)=="money" else display(value)
                    # Fixed two-line cells keep every row and distinguish the full source in XLSX/CSV.
                    limit=max(12,int(colwidth/5.2));text=text.replace("\n"," ")
                    for line in range(2):
                        segment=text[line*limit:(line+1)*limit]
                        if line==1 and len(text)>2*limit:segment=segment[:-1]+"…"
                        if segment:out.append(f'<text x="{left+index*colwidth}" y="{y+line*11}" font-family="sans-serif" font-size="9">{escape(segment)}</text>')
                y+=28
            out.append(f'<text x="36" y="770" font-family="sans-serif" font-size="9" fill="#555">BTW · {escape(str(meta.get("Status","")))} · Page {page} · Full source text remains in the matching spreadsheet export.</text></svg>')
            raw=cairosvg.svg2pdf(bytestring="".join(out).encode())
            writer.add_page(PdfReader(io.BytesIO(raw)).pages[0])
        for row in rows:
            batch.append(row)
            if len(batch)==page_rows:page+=1;page_pdf(batch,page);batch=[]
        if batch or not page:page_pdf(batch,page+1)
        stream=io.BytesIO();writer.write(stream);response=HttpResponse(stream.getvalue(),content_type="application/pdf")
    response['Content-Disposition']=f'attachment; filename="{filename}.{format}"'
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response
