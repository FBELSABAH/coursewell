"""Local readers with bounded OCR and explicit per-document review notes."""
from pathlib import Path
import csv
import io
import shutil
import subprocess
import tempfile
import threading
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

SUPPORTED = {'.pdf','.docx','.pptx','.xlsx','.odt','.ods','.odp','.txt','.md','.json','.csv','.tsv','.html','.htm','.png','.jpg','.jpeg','.tif','.tiff','.webp','.eml','.ics','.zip'}
PDF_LOCK = threading.Lock()  # PDFium is not thread-safe.


def ocr(image):
    engine = shutil.which('tesseract') or next((str(p) for p in (Path('/opt/homebrew/bin/tesseract'),Path('/usr/local/bin/tesseract')) if p.is_file()),None)
    if not engine:
        raise RuntimeError('OCR engine unavailable')
    return subprocess.run([engine,str(image),'stdout','-l','eng'],capture_output=True,text=True,check=True,timeout=45).stdout


def pdf_ocr(path, number, target):
    import pypdfium2 as pdfium
    with PDF_LOCK:
        with pdfium.PdfDocument(path) as doc:
            page=doc[number-1]
            try:
                w,h=page.get_size()
                scale=min(2.5,3000/max(w,h))
                bitmap=page.render(scale=scale)
                try:bitmap.to_pil().save(target)
                finally:bitmap.close()
            finally:page.close()
    return ocr(target)


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        if tag in ('p','div','li','tr','br','h1','h2','h3'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
        if tag in ('p','div','li','tr'):self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def read_document(path, native_reader):
    path=Path(path);ext=path.suffix.lower();notes=[];ocr_pages=[]
    if path.stat().st_size>100*1024*1024:
        return {'pages':[],'notes':['File exceeds 100 MB reader limit; needs review.'],'ocr_pages':[]}
    if ext not in SUPPORTED:
        return {'pages':[],'notes':['Unsupported format; needs review.'],'ocr_pages':[]}
    if ext=='.zip':
        pages=[]
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as tmp:
            members=[m for m in archive.infolist() if not m.is_dir()]
            if len(members)>100 or sum(m.file_size for m in members)>100*1024*1024:
                return {'pages':[],'notes':['Archive exceeds 100 files or 100 MB expanded size; needs review.'],'ocr_pages':[]}
            for index,member in enumerate(members):
                suffix=Path(member.filename).suffix.lower()
                if suffix=='.zip':notes.append(member.filename+': nested archive needs review.');continue
                # Generated flat paths prevent zip-slip and never execute archive content.
                target=Path(tmp)/(str(index)+suffix)
                try:
                    target.write_bytes(archive.read(member))
                    document=read_document(target,native_reader)
                    pages.extend((f'{member.filename} / {n}',text) for n,text in document['pages'])
                    notes.extend(member.filename+': '+note for note in document['notes'])
                except Exception:notes.append(member.filename+': could not read archive member.')
    elif ext=='.eml':
        from email import policy
        from email.parser import BytesParser
        message=BytesParser(policy=policy.default).parsebytes(path.read_bytes());pages=[]
        body=message.get_body(preferencelist=('plain','html'))
        if body:
            text=body.get_content()
            if body.get_content_type()=='text/html':
                parser=HTMLText();parser.feed(text);text=''.join(parser.parts)
            pages=[(None,'Subject: '+str(message.get('Subject',''))+'\nDate: '+str(message.get('Date',''))+'\n'+text)]
        with tempfile.TemporaryDirectory() as tmp:
            for index,part in enumerate(message.iter_attachments()):
                if index>=30:notes.append('Email has more than 30 attachments; needs review.');break
                suffix=Path(part.get_filename() or '').suffix.lower()
                if suffix in ('.eml','.zip'):notes.append('Nested email/archive attachment needs review.');continue
                target=Path(tmp)/(str(index)+suffix)
                try:
                    target.write_bytes(part.get_payload(decode=True) or b'')
                    doc=read_document(target,native_reader);pages.extend(doc['pages']);notes.extend(doc['notes'])
                except Exception:notes.append('An email attachment could not be read.')
    elif ext=='.ics':
        pages=[(None,path.read_text(errors='replace'))]
    elif ext in ('.png','.jpg','.jpeg','.tif','.tiff','.webp'):
        from PIL import Image, ImageOps
        pages=[]
        with Image.open(path) as image, tempfile.TemporaryDirectory() as tmp:
            count=getattr(image,'n_frames',1)
            for i in range(min(count,20)):
                image.seek(i);frame=ImageOps.exif_transpose(image).convert('RGB');frame.thumbnail((4000,4000))
                target=Path(tmp)/'page.png';frame.save(target)
                try:pages.append((i+1,ocr(target)));ocr_pages.append(i+1)
                except Exception:pages.append((i+1,''));notes.append(f'Page {i+1}: OCR failed; needs review.')
            if count>20:notes.append('Image has more than 20 frames; remaining frames need review.')
    elif ext=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(path);pages=[]
        if reader.is_encrypted and not reader.decrypt(''):
            return {'pages':[],'notes':['Password-protected PDF; unlock a copy to process it.'],'ocr_pages':[]}
        with tempfile.TemporaryDirectory() as tmp:
            attempted=0
            for i,page in enumerate(reader.pages):
                if i>=500:
                    notes.append('PDF exceeds 500 pages; remaining pages need review.');break
                try:text=page.extract_text() or ''
                except Exception:text=''
                if len(''.join(text.split()))<50:
                    if attempted<100:
                        attempted+=1
                        try:
                            scanned=pdf_ocr(path,i+1,Path(tmp)/'page.png')
                            if len(scanned.strip())>len(text.strip()):text=scanned;ocr_pages.append(i+1)
                        except Exception:notes.append(f'Page {i+1}: OCR unavailable or failed; needs review.')
                    else:notes.append(f'Page {i+1}: OCR limit reached; needs review.')
                pages.append((i+1,text))
    elif ext=='.json':
        pages=[(None,path.read_text(errors='replace'))]
    elif ext in ('.html','.htm'):
        parser=HTMLText();parser.feed(path.read_text(errors='replace'));pages=[(None,''.join(parser.parts))]
    elif ext in ('.csv','.tsv'):
        rows=csv.reader(io.StringIO(path.read_text(errors='replace')),delimiter='\t' if ext=='.tsv' else ',')
        pages=[(None,'\n'.join(' | '.join(row) for row in rows))]
    elif ext in ('.xlsx','.odt','.ods','.odp'):
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist())>100*1024*1024:
                raise ValueError('Expanded document exceeds reader limit')
            if ext=='.xlsx':
                ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                strings=[]
                if 'xl/sharedStrings.xml' in archive.namelist():
                    strings=[''.join(n.itertext()) for n in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si',ns)]
                pages=[]
                for name in sorted(n for n in archive.namelist() if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')):
                    rows=[]
                    for row in ET.fromstring(archive.read(name)).findall('.//s:row',ns):
                        cells=[]
                        for cell in row.findall('s:c',ns):
                            value=cell.findtext('s:v',default='',namespaces=ns)
                            if cell.get('t')=='s' and value:value=strings[int(value)]
                            elif cell.get('t')=='inlineStr':value=''.join(cell.find('s:is',ns).itertext())
                            cells.append(value)
                        rows.append(' | '.join(cells))
                    pages.append((name,'\n'.join(rows)))
                notes.append('Spreadsheet cached values read; formulas are not recalculated and numeric date cells need review.')
            else:
                root=ET.fromstring(archive.read('content.xml'))
                paragraphs=[''.join(n.itertext()) for n in root.iter() if n.tag.endswith('}p') or n.tag.endswith('}h')]
                pages=[(None,'\n'.join(paragraphs))]
    else:
        if ext in ('.docx','.pptx'):
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist())>100*1024*1024:raise ValueError('Expanded document exceeds reader limit')
        if ext == '.docx':
            with zipfile.ZipFile(path) as archive:
                ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                root = ET.fromstring(archive.read('word/document.xml'))
                body = root.find(ns+'body')
                lines = []
                for node in ([] if body is None else body):
                    if node.tag == ns+'tbl':
                        for row in node.findall(ns+'tr'):
                            lines.append(' | '.join(' '.join(t.text or '' for t in cell.iter(ns+'t'))
                                                    for cell in row.findall(ns+'tc')))
                    elif node.tag == ns+'p':
                        lines.append(''.join(t.text or '' for t in node.iter(ns+'t')))
                pages = [(None, '\n'.join(lines))]
                if any(n.startswith('word/media/') for n in archive.namelist()):
                    notes.append('Embedded images may contain additional dates; inspect the original document.')
        else:
            pages=native_reader(path)
    if ocr_pages:notes.append('OCR text can misread dates; OCR-derived events require review.')
    if not pages or sum(len(t.strip()) for _,t in pages)<50:notes.append('Little readable text; needs visual review.')
    elif any(len(t.strip())<25 for _,t in pages):notes.append('Some pages have little text; needs visual review.')
    return {'pages':pages,'notes':notes,'ocr_pages':ocr_pages}
