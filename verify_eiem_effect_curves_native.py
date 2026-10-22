"""Independently decode native compiler output and compare authored curves."""
import json
import math
import struct
import sys
from pathlib import Path

from nativepack.author_source import AuthorCore
from nativepack.serialized_file import SerializedFile
from nativepack.type_tree import Schema, decode

source,author,build=map(Path,sys.argv[1:])
manifest=json.loads((source/'source/manifest.json').read_bytes())
catalog=json.loads((source/'effect.json').read_bytes())
specs={o['identity']:o for o in catalog['objects']}
receipt=json.loads((build/'build.json').read_bytes())
core=AuthorCore(Path(__file__).parent/'nativepack/eff_author_core.dll')
decoded={}

def plain(node):
    value=node.plain()
    def normalize(v):
        if isinstance(v,dict):
            if set(v)=={'Array'}:return normalize(v['Array'])
            return {k:normalize(x) for k,x in v.items()}
        return [normalize(x) for x in v] if isinstance(v,list) else v
    return normalize(value)

for product in receipt['nativeFiles']:
    row=next(r for r in manifest['serializedFiles'] if r['cab'].casefold()==product['cab'].casefold())
    original=(source/row['snapshot']['file']).read_bytes()
    native=SerializedFile(original,row,core.schema(original))
    built=(build/product['file']).read_bytes()
    offset=struct.unpack_from('>q',built,32)[0]
    count=struct.unpack_from('<i',built,native.count_offset)[0]
    assert count==len(native.order),'curve edits changed native object count'
    schemas={t['index']:Schema.compile(t['nodes']) for t in core.schema(built)['types'] if t['nodes']}
    for i in range(count):
        pid,start,size,ti=struct.unpack_from('<qqIi',built,native.table_start+i*24)
        if native.objects[pid]['classId']!=198:continue
        decoded[product['cab'].lower()+':'+str(pid)]=plain(decode(schemas[ti],built[offset+start:offset+start+size]))

def equal(actual,expected,path=''):
    if isinstance(expected,dict):
        assert actual.keys()==expected.keys(),path
        for k,v in expected.items():equal(actual[k],v,path+'/'+k)
    elif isinstance(expected,list):
        assert len(actual)==len(expected),path
        for i,v in enumerate(expected):equal(actual[i],v,path+'/'+str(i))
    elif isinstance(expected,float):assert math.isclose(actual,expected,rel_tol=2e-7,abs_tol=1e-7),(path,actual,expected)
    else:assert actual==expected,(path,actual,expected)

count=0
for patch in json.loads(author.read_bytes())['objects']:
    if specs[patch['identity']]['classId']!=198:continue
    equal(decoded[patch['identity']],patch['data']);count+=1
assert count>0
print('PASS independent native decode: %d ParticleSystem(s), all curve keys/slopes/weights, types and untouched fields'%count)
