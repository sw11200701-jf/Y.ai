"""Explicit native object order, separate from canonical JSON map ordering."""
from .slicing_contracts import digest,fail
CONTRACT='pinned-native-reading-order-v1'
KINDS=('sources','occurrences','assets')

def pin_reading_order(registry):
    if registry.get('reading_order_contract'):fail('原生读取顺序不能重新固定','SLICING_SCOPE_DENIED')
    order={kind:list(registry[kind]) for kind in KINDS}
    registry.update(reading_order_contract=CONTRACT,reading_order=order,reading_order_sha256=digest(order))

def ordered_identifiers(registry,kind):
    if not registry.get('reading_order_contract'):return list(registry[kind])
    if registry['reading_order_contract']!=CONTRACT:fail('原生读取顺序合同不受支持','SLICING_SCOPE_DENIED')
    order=registry.get('reading_order')
    if not isinstance(order,dict) or set(order)!=set(KINDS) or registry.get('reading_order_sha256')!=digest(order):fail('固定原生读取顺序校验失败','SLICING_SCOPE_DENIED')
    for key in KINDS:
        ids=order[key]
        if not isinstance(ids,list) or any(not isinstance(value,str) for value in ids) or len(ids)!=len(set(ids)) or set(ids)!=set(registry[key]):fail('原生读取顺序不得遗漏、重复或越出已授权原件','SLICING_SCOPE_DENIED')
    return order[kind]
