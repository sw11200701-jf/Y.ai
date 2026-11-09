"""Module-owned proposals for engineering's canonical registry. No runtime registry mutation."""
def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}

STRING = {"type": "string", "minLength": 1}
FILE_ID = {"type": "string", "pattern": "^document_[a-f0-9]{32}$"}
FILE_IDS = {"type": "array", "items": FILE_ID, "minItems": 1, "maxItems": 20, "uniqueItems": True}
OBJECT = {"type": "object"}
ARRAY = {"type": "array"}
NODE_SCHEMAS = {
    "document_extractor": {
        "config_schema": obj({"chunk_size": {"type": "integer", "minimum": 200, "maximum": 2000},
                              "chunk_overlap": {"type": "integer", "minimum": 0, "maximum": 200}}),
        "input_schema": obj({"file_id": FILE_ID}, ["file_id"]),
        "output_schema": {"type": "object", "required": ["file_id", "chunks", "source_refs", "extraction_method"],
                          "properties": {"file_id": FILE_ID, "chunks": ARRAY, "chunk_count": {"type": "integer"}, "source_refs": ARRAY,
                                         "extraction_method": {"enum": ["text", "text_layer"]}}},
        "status": "supported", "reason": "受控file_id文本提取；PDF按capabilities依赖状态；OCR未实现"},
    "knowledge_retrieval": {
        "config_schema": obj({"top_k": {"type": "integer", "minimum": 1, "maximum": 20}}),
        "input_schema": {**obj({"query": {"type": "string", "minLength": 1, "maxLength": 500},
                                 "file_ids": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 20, "uniqueItems": True},
                                 "knowledge_base_ids": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 20, "uniqueItems": True},
                                 "version_ids": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 20, "uniqueItems": True}}, ["query"]),
                         "anyOf": [{"required": ["file_ids"]}, {"required": ["knowledge_base_ids"]}],
                         "dependentRequired": {"version_ids": ["knowledge_base_ids"]}},
        "output_schema": {"type": "object", "required": ["method", "hits", "source_refs"],
                          "properties": {"method": {"const": "keyword_overlap_v1"}, "hits": ARRAY, "source_refs": ARRAY}},
        "status": "supported", "reason": "真实关键词覆盖/词频排序，非向量检索；仅显式file_ids"},
    "query": {"config_schema": obj({}), "input_schema": obj({"query": OBJECT, "sources": {"type": "array", "minItems": 1, "maxItems": 16}}, ["query"]),
              "output_schema": {"type": "object", "required": ["facts", "source_refs"]}},
    "calculate": {"config_schema": obj({}), "input_schema": obj({"facts": ARRAY, "query": OBJECT}, ["facts", "query"]),
                  "output_schema": {"type": "object", "required": ["dataset", "dataset_id", "snapshot_id"]}},
    "chart": {"config_schema": obj({"series": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 5, "uniqueItems": True}}),
              "input_schema": obj({"dataset": OBJECT}, ["dataset"]), "output_schema": {"type": "object", "required": ["chart_spec"]}},
    "report": {"config_schema": obj({}), "input_schema": obj({"dataset": OBJECT}, ["dataset"]),
               "output_schema": {"type": "object", "required": ["artifacts", "artifact_ids", "dataset_id"]}},
}
