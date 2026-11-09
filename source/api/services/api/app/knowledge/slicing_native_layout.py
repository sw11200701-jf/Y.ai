"""Native positions and complete native tables, without image text inference."""
from copy import deepcopy
from math import hypot
from .slicing_contracts import fail

CONTRACT='native-attachment-context-v1'
TABLE_CONTRACT='complete-native-table-delivery-v1'

def augment_ppt(registry,content):
    from .slicing_parser import _archive,_xml,NS
    rectangles={};crops={}
    def transform(node):
        for path in ('p:spPr/a:xfrm','p:grpSpPr/a:xfrm','p:xfrm'):
            value=node.find(path,NS)
            if value is not None:return value
        return None
    def point(node,tag,keys):
        value=node.find('a:'+tag,NS) if node is not None else None
        return tuple(int(value.get(key,'0')) for key in keys) if value is not None else None
    def walk(parent,part,matrix=(1.,1.,0.,0.),usable=True):
        sx,sy,tx,ty=matrix
        for node in parent:
            kind=node.tag.rsplit('}',1)[-1]
            if kind not in ('sp','pic','graphicFrame','grpSp','cxnSp'):continue
            own=next((n for n in node if n.tag.rsplit('}',1)[-1].startswith('nv')),None)
            identity=own.find('p:cNvPr',NS) if own is not None else None
            if identity is None:continue
            key=(part,identity.get('id'));value=transform(node)
            off=point(value,'off',('x','y'));ext=point(value,'ext',('cx','cy'))
            exact=usable and (value is None or not any(value.get(k) not in (None,'0','false') for k in ('rot','flipH','flipV')))
            if off and ext and exact:rectangles[key]=[tx+sx*off[0],ty+sy*off[1],sx*ext[0],sy*ext[1]]
            crop=node.find('.//a:srcRect',NS)
            if crop is not None:crops[key]={k:int(crop.get(k,'0')) for k in ('l','t','r','b')}
            if kind=='grpSp':
                child_off=point(value,'chOff',('x','y'));child_ext=point(value,'chExt',('cx','cy'))
                if off and ext and child_off and child_ext and all(child_ext):
                    nx,ny=ext[0]/child_ext[0],ext[1]/child_ext[1]
                    walk(node,part,(sx*nx,sy*ny,tx+sx*(off[0]-nx*child_off[0]),ty+sy*(off[1]-ny*child_off[1])),exact)
                else:walk(node,part,matrix,False)
    parts={s['locator']['part'] for s in registry['sources'].values() if s['locator'].get('kind')=='slide_shape'}
    with _archive(content) as archive:
        for part in sorted(parts):
            tree=_xml(archive,part).find('p:cSld/p:spTree',NS)
            if tree is not None:walk(tree,part)
    for source in registry['sources'].values():
        loc=source['locator'];rect=rectangles.get((loc.get('part'),loc.get('native_shape_id')))
        if rect is not None:source['metadata']['native_rect_emu']=rect
    def distance(a,b):
        gap=hypot(max(0,a[0]-b[0]-b[2],b[0]-a[0]-a[2]),max(0,a[1]-b[1]-b[3],b[1]-a[1]-a[3]))
        centers=hypot(a[0]+a[2]/2-b[0]-b[2]/2,a[1]+a[3]/2-b[1]-b[3]/2)
        return gap+.05*centers
    for occurrence in registry['occurrences'].values():
        loc=occurrence['locator'];key=(loc.get('part'),loc.get('native_shape_id'));rect=rectangles.get(key)
        hints={'slide':loc.get('slide')}
        if rect is not None:
            hints['native_rect_emu']=rect
            nearby=[(distance(rect,s['metadata']['native_rect_emu']),sid) for sid,s in registry['sources'].items()
                if s['locator'].get('slide')==loc.get('slide') and s['text'].strip() and not s['text'].strip().isdigit() and 'native_rect_emu' in s['metadata']]
            hints['nearest_source_ids']=[sid for _,sid in sorted(nearby)[:3]]
        if key in crops:hints['native_crop_ltrb_100000']=crops[key]
        occurrence['native_context']=hints

def check_attachment_context(value,sources,occurrences):
    mismatches=[]
    for item in value['items']:
        pages={sources[sid]['locator'].get('slide') for sid in item['source_ids'] if sources[sid]['locator'].get('slide') is not None}
        for oid in item.get('attachment_occurrence_ids',[]):
            occurrence=occurrences[oid];page=occurrence['locator'].get('slide')
            if page is not None and page not in pages:
                mismatches.append({'knowledge_id':item['knowledge_id'],'occurrence_id':oid,'actual_slide':page,'supported_slides':sorted(pages),
                    'occurrence_ids':[key for key,row in occurrences.items() if row['locator'].get('slide') in pages],
                    'reason':'附件来自正文所引原生来源之外的幻灯片；不得把相反情形或重复原图的另一次出现错挂。请对照原页标签、坐标和nearest_source_ids选择正确出现，并保持正文与场景一致。'})
    if mismatches:fail('原图附件与业务知识的原生页不一致',details={'attachment_context_mismatches':mismatches})

def with_complete_tables(plan,item):
    if plan.get('native_table_delivery_contract')!=TABLE_CONTRACT:return item
    result=deepcopy(item)
    for sid in item['source_ids']:
        rows=plan['registry']['sources'][sid]['metadata'].get('native_table_rows')
        if not rows:continue
        # Deterministic original cells enter the body BEFORE independent review.
        # This adds no business inference and preserves every blank and sign.
        rendered='\n'.join(' | '.join(str(cell).replace('|','\\|').replace('\n',' / ') if str(cell) else '（空）' for cell in row) for row in rows)
        block='原文表格（按原行列；（空）表示原单元格为空）：\n'+rendered
        if block not in result['content']:result['content']+='\n\n'+block
    return result
