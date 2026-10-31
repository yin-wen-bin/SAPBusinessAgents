"""Lossless strict-output transport. Business schemas and evidence remain authoritative.

Maps use explicit key/value entries; unconstrained JSON leaves use exact JSON text.
This is a declared codec, never a repair of malformed model output.
"""
from __future__ import annotations

import copy
import json
import math
from jsonschema import Draft202012Validator
from .runtime_contract import RuntimeContractError

VERSION = 'sapba.strict-output/1'


def failure(code, path, constraint):
    error = RuntimeContractError(code)
    error.detail = {'phase': 'output_contract', 'validation_issues': [
        {'code': code, 'path': path or '/', 'constraint': constraint}]}
    return error


def preflight(schema):
    """Reject invalid strict object definitions before any SDK connection."""
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise failure('runtime_output_contract_invalid', '/', 'json_schema') from None
    def walk(node, path=''):
        if not isinstance(node, dict):
            return
        if node.get('type') == 'object':
            props = node.get('properties')
            if not isinstance(props, dict):
                raise failure('runtime_output_contract_invalid', path, 'properties_required')
            if node.get('additionalProperties') is not False:
                raise failure('runtime_output_contract_invalid', path, 'closed_object_required')
            if set(node.get('required', [])) != set(props):
                raise failure('runtime_output_contract_invalid', path, 'all_properties_required')
        if node.get('type') == 'array' and not isinstance(node.get('items'), dict):
            raise failure('runtime_output_contract_invalid', path, 'items_required')
        for key in ('properties', '$defs'):
            for name, value in node.get(key, {}).items():
                walk(value, path+'/'+key+'/'+name)
        if 'items' in node:
            walk(node['items'], path+'/items')
        for key in ('anyOf', 'oneOf', 'allOf'):
            for index, value in enumerate(node.get(key, [])):
                walk(value, path+'/'+key+'/'+str(index))
    walk(schema)


def exact_json(text):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate_key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('non_finite_json')
    result=json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
    def finite(value):
        if isinstance(value,float) and not math.isfinite(value):raise ValueError('non_finite_json')
        if isinstance(value,dict):
            for item in value.values():finite(item)
        elif isinstance(value,list):
            for item in value:finite(item)
    finite(result)
    return result


class NativeContract:
    def __init__(self, schema):
        self.canonical = copy.deepcopy(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception:
            raise failure('runtime_output_contract_invalid', '/', 'json_schema') from None
        self.mapping_paths = []
        self.node = self._build(schema, '')
        self.schema = self.node['wire']
        preflight(self.schema)

    def _build(self, source, path):
        if not isinstance(source, dict) or '$ref' in source:
            raise failure('runtime_output_contract_invalid', path, 'expanded_schema_required')
        node = {'canonical': source, 'wire': copy.deepcopy(source), 'kind': 'plain', 'path': path}
        if not source or not any(k in source for k in ('type', 'anyOf', 'enum', 'const')):
            node.update(kind='json', wire={'type':'string', 'description':
                'Exact JSON serialization of this value, including quotes for strings. No markdown or coercion.'})
            self.mapping_paths.append(path)
        elif 'anyOf' in source:
            node['kind'] = 'union'
            node['branches'] = [self._build(s, path+'/anyOf/'+str(i)) for i,s in enumerate(source['anyOf'])]
            node['wire']['anyOf'] = [s['wire'] for s in node['branches']]
        elif source.get('type') == 'object':
            props = source.get('properties', {})
            extra = source.get('additionalProperties', True)
            if extra is not False:
                # Mixed named/open objects cannot silently lose either part.
                if props:
                    node.update(kind='json', wire={'type':'string','description':'Exact JSON serialization of the complete object.'})
                else:
                    value = self._build(extra if isinstance(extra,dict) else {}, path+'/additionalProperties')
                    node.update(kind='map', value=value, wire={'type':'array','items':{
                        'type':'object','additionalProperties':False,'required':['key','value'],
                        'properties':{'key':{'type':'string'},'value':value['wire']}},
                        'description':'Lossless map entries. Keys must be unique; preserve every key and value. Empty map is [].'})
                    for a,b in (('minProperties','minItems'),('maxProperties','maxItems')):
                        if a in source:node['wire'][b]=source[a]
                self.mapping_paths.append(path)
            else:
                node['kind']='object';node['fields']={k:self._build(v,path+'/properties/'+k) for k,v in props.items()}
                node['wire']['properties']={k:v['wire'] for k,v in node['fields'].items()}
        elif source.get('type') == 'array':
            node['kind']='array';node['item']=self._build(source.get('items',{}),path+'/items')
            node['wire']['items']=node['item']['wire']
        return node

    def _move(self, node, value, encode):
        kind=node['kind']
        if kind=='json':
            try:
                if not encode:return exact_json(value)
                encoded=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))
                if exact_json(encoded)!=value:raise ValueError('not_lossless_json')
                return encoded
            except (ValueError,TypeError,RecursionError):
                raise failure('runtime_structured_output_invalid',node['path'],'exact_json_required') from None
        if kind=='map':
            if encode:return [{'key':k,'value':self._move(node['value'],v,True)} for k,v in value.items()]
            result={}
            for entry in value:
                if entry['key'] in result:
                    raise failure('runtime_structured_output_invalid',node['path'],'duplicate_map_key')
                result[entry['key']]=self._move(node['value'],entry['value'],False)
            return result
        if kind=='union':
            candidates=[]
            for branch in node['branches']:
                try:
                    if not Draft202012Validator(branch['canonical'] if encode else branch['wire']).is_valid(value):continue
                    candidate=self._move(branch,value,encode)
                    if not encode and not Draft202012Validator(branch['canonical']).is_valid(candidate):continue
                    candidates.append(candidate)
                except RuntimeContractError:continue
            if not candidates or any(v!=candidates[0] for v in candidates[1:]):
                raise failure('runtime_structured_output_invalid',node['path'],'unambiguous_branch_required')
            return candidates[0]
        if kind=='object':return {k:self._move(node['fields'][k],v,encode) for k,v in value.items()}
        if kind=='array':return [self._move(node['item'],v,encode) for v in value]
        return copy.deepcopy(value)

    def encode(self, value):
        if not Draft202012Validator(self.canonical).is_valid(value):
            raise failure('runtime_structured_output_invalid','/','canonical_schema_mismatch')
        result=self._move(self.node,value,True)
        if not Draft202012Validator(self.schema).is_valid(result):
            raise failure('runtime_output_contract_invalid','/','encoded_schema_mismatch')
        return result

    def decode(self, value):
        if not Draft202012Validator(self.schema).is_valid(value):
            raise failure('runtime_structured_output_invalid','/','transport_shape')
        result=self._move(self.node,value,False)
        if not Draft202012Validator(self.canonical).is_valid(result):
            raise failure('runtime_structured_output_invalid','/','canonical_schema_mismatch')
        return result

    def read(self, text):
        try:value=exact_json(text)
        except (TypeError,ValueError,RecursionError):
            raise failure('runtime_structured_output_invalid','/','exact_json_required') from None
        return self.decode(value)

    def instructions(self):
        if not self.mapping_paths:return ''
        return ('\nNative final-output transport '+VERSION+': dynamic maps in the declared native Schema are arrays '
            'of unique {key,value} entries, not JSON objects. Unconstrained JSON leaves use exact JSON strings '
            'as described by the Schema. This applies ONLY to the native final envelope, not report-tool inputs. '
            'After report validation, copy native_validated_report unchanged into presentation. '
            'It carries ALL the same bindings and proof values as validated_report. Do not drop fields, '
            'guess defaults, reinterpret values or stringify the whole presentation. Business validation remains unchanged.')
