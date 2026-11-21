"""Trusted bounded PDF extraction entrypoint, baked into the document image."""
import json
from pathlib import Path
import sys
from pypdf import PdfReader


def main():
    max_pages=int(sys.argv[1])
    reader=PdfReader('/workspace/input.pdf',strict=True)
    if reader.is_encrypted:
        raise ValueError('Encrypted PDFs are unsupported')
    if len(reader.pages)>max_pages:
        raise ValueError('PDF page limit exceeded')
    pages=[]
    parts=[]
    cursor=0
    for index,page in enumerate(reader.pages,1):
        if '/Contents' in page and len(page.get_contents().get_data())>10*1024*1024:
            raise ValueError('Decompressed page content exceeds limit')
        text=page.extract_text() or ''
        if len(text)>100000 or cursor+len(text)>1000000:
            raise ValueError('Extracted text exceeds limit')
        pages.append({'page':index,'start':cursor,'end':cursor+len(text),'characters':len(text)})
        parts.append(text+'\n')
        cursor+=len(text)+1
    quality='no_text' if sum(p['characters'] for p in pages)<20 else ('partial_text' if any(p['characters']<20 for p in pages) else 'text')
    Path('/workspace/extracted.json').write_text(json.dumps({'pages':pages,'text':''.join(parts),'quality':quality},ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'quality':quality,'page_count':len(pages),'characters':cursor}))


if __name__=='__main__':
    main()
