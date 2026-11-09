"""Explicit frozen public resolver. No credential, model HTTP, OS DNS changes.

Default system selection preserves the existing address policy. The trusted
mode is opt-in by exact startup policy hash; it never accepts a non-global IP.
"""
from dataclasses import dataclass
from copy import deepcopy
import asyncio,hashlib,ipaddress,json,os,re
import httpx
from app.errors import AppError

MODE='alidns-doh-v1'
SYSTEM={'contract':'model-public-resolver-v1','mode':'system','resolution_budget_seconds':0}
TRUSTED={'contract':'model-public-resolver-v1','mode':MODE,'resolver_uri':'https://dns.alidns.com/resolve',
         'bootstrap_address':'223.5.5.5','tls_server_name':'dns.alidns.com','query_types':[1,28],
         'resolution_budget_seconds':3,'response_bytes_per_query':16384,'max_records':32,'max_cname_hops':8,
         'max_ttl_seconds':300,'redirects':False,'trust_env':False,'verify_tls':True,'require_all_global':True,'response_content_types':['application/json','application/dns-json']}
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def reject(code):raise AppError(code,'模型公共解析策略未通过，未打开供应商连接',403)

@dataclass(frozen=True)
class Policy:
    mode:str
    sha256:str
    def public(self):return {'policy':deepcopy(TRUSTED if self.mode==MODE else SYSTEM),'sha256':self.sha256}
    @property
    def resolution_budget_seconds(self):return 3 if self.mode==MODE else 0

def policy(mode='system',expected_sha256=''):
    if mode not in ('system',MODE):reject('MODEL_RESOLVER_POLICY_INVALID')
    pin=digest(TRUSTED if mode==MODE else SYSTEM)
    if expected_sha256!=pin and (mode!='system' or expected_sha256):reject('MODEL_RESOLVER_POLICY_INVALID')
    return Policy(mode,pin)

# Read only these two NONSECRET startup keys once, never mutable request fields.
POLICY=policy(os.environ.get('FINANCE_MODEL_PUBLIC_RESOLVER_MODE','system'),os.environ.get('FINANCE_MODEL_PUBLIC_RESOLVER_SHA256',''))

def name(value):
    if not isinstance(value,str) or len(value)>254:reject('MODEL_RESOLVER_DNS_NAME_INVALID')
    value=value.removesuffix('.').lower()
    try:value=value.encode('idna').decode('ascii')
    except (UnicodeError,ValueError):reject('MODEL_RESOLVER_DNS_NAME_INVALID')
    if len(value)>253 or '.' not in value or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',part) for part in value.split('.')):reject('MODEL_RESOLVER_DNS_NAME_INVALID')
    try:ipaddress.ip_address(value)
    except ValueError:return value
    reject('MODEL_RESOLVER_DNS_NAME_INVALID')

def public_ip(value,version):
    if type(value) is not str or '%' in value:reject('MODEL_RESOLVER_DNS_ANSWER_INVALID')
    try:ip=ipaddress.ip_address(value)
    except (ValueError,TypeError):reject('MODEL_RESOLVER_DNS_ANSWER_INVALID')
    if ip.version!=version:reject('MODEL_RESOLVER_DNS_ANSWER_INVALID')
    # Stricter trusted-public mode: no private flag can relax this predicate.
    if (not ip.is_global or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved
            or str(ip) in {'100.100.100.200','fd00:ec2::254'}):reject('CONNECTION_BLOCKED')
    if ip.version==6 and (ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None
                         or ip in ipaddress.ip_network('64:ff9b::/96')):reject('CONNECTION_BLOCKED')
    return str(ip)

def parse_answer(value,host,qtype):
    host=name(host)
    if (type(value) is not dict or type(value.get('Status')) is not int or value['Status']!=0
            or value.get('TC') is not False or value.get('CD') is not False):reject('MODEL_RESOLVER_DNS_ANSWER_INVALID')
    questions=value.get('Question')
    if type(questions) is dict:questions=[questions]  # AliDNS documented single-question JSON object
    if (type(questions) is not list or len(questions)!=1 or type(questions[0]) is not dict
            or type(questions[0].get('type')) is not int or questions[0]['type']!=qtype
            or name(questions[0].get('name'))!=host):reject('MODEL_RESOLVER_DNS_QUESTION_MISMATCH')
    rows=value.get('Answer',[])
    if type(rows) is not list or len(rows)>TRUSTED['max_records']:reject('MODEL_RESOLVER_DNS_ANSWER_INVALID')
    aliases={};addresses=[];ttls=[]
    for row in rows:
        if (type(row) is not dict or set(row)!={'name','type','TTL','data'} or type(row['type']) is not int
                or row['type'] not in (5,qtype) or type(row['TTL']) is not int or not 0<=row['TTL']<=2147483647):reject('MODEL_RESOLVER_DNS_ANSWER_INVALID')
        owner=name(row['name']);ttls.append(min(row['TTL'],TRUSTED['max_ttl_seconds']))
        if row['type']==5:
            if owner in aliases:reject('MODEL_RESOLVER_DNS_CNAME_INVALID')
            aliases[owner]=name(row['data'])
        else:addresses.append((owner,public_ip(row['data'],4 if qtype==1 else 6)))
    reachable={host};terminal=host
    while terminal in aliases:
        terminal=aliases[terminal]
        if terminal in reachable or len(reachable)>TRUSTED['max_cname_hops']:reject('MODEL_RESOLVER_DNS_CNAME_INVALID')
        reachable.add(terminal)
    if any(owner not in reachable for owner in aliases) or any(owner!=terminal for owner,_ in addresses):reject('MODEL_RESOLVER_DNS_CNAME_INVALID')
    return {'addresses':[ip for _,ip in addresses],'ttl_seconds':min(ttls) if ttls else 0,'cname_hops':len(reachable)-1}

async def query(client,host,qtype):
    try:
        async with client.stream('GET','https://223.5.5.5/resolve',params={'name':host,'type':qtype},
                headers={'Host':TRUSTED['tls_server_name'],'Accept':'application/dns-json'},
                extensions={'sni_hostname':TRUSTED['tls_server_name']}) as response:
            if response.status_code!=200 or response.headers.get('content-type','').split(';')[0] not in TRUSTED['response_content_types']:reject('MODEL_RESOLVER_HTTP_REJECTED')
            data=bytearray()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if len(data)+len(chunk)>TRUSTED['response_bytes_per_query']:reject('MODEL_RESOLVER_RESPONSE_TOO_LARGE')
                data.extend(chunk)
        value=json.loads(data,parse_constant=lambda _:reject('MODEL_RESOLVER_DNS_ANSWER_INVALID'))
        return parse_answer(value,host,qtype)
    except AppError:raise
    except (ValueError,UnicodeError):reject('MODEL_RESOLVER_DNS_ANSWER_INVALID')
    except httpx.HTTPError:reject('MODEL_RESOLVER_TRANSPORT_FAILED')

async def resolve_public(host,*,selected=POLICY):
    if selected.mode!=MODE or selected.sha256!=digest(TRUSTED):reject('MODEL_RESOLVER_POLICY_INVALID')
    host=name(host)
    try:
        async with asyncio.timeout(TRUSTED['resolution_budget_seconds']):
            async with httpx.AsyncClient(trust_env=False,verify=True,follow_redirects=False,timeout=3) as client:
                # Both requests share ONE 3-second wall; no fallback/retry/cache.
                tasks=[asyncio.create_task(query(client,host,t)) for t in (1,28)]
                try:records=await asyncio.gather(*tasks)
                except BaseException:
                    for task in tasks:
                        if not task.done():task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
                    raise
        addresses=sorted({address for record in records for address in record['addresses']},key=lambda x:(ipaddress.ip_address(x).version,int(ipaddress.ip_address(x))))
        if not addresses:reject('MODEL_RESOLVER_NO_PUBLIC_ANSWER')
        return {'hostname':host,'addresses':addresses,'ttl_seconds':min(r['ttl_seconds'] for r in records),'policy_sha256':selected.sha256}
    except AppError:raise
    except TimeoutError:reject('MODEL_RESOLVER_TIMEOUT')
