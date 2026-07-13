from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from cortex.api.chat import ChatRequest, ChatResponse
from cortex.api.external_documents import ExternalDocument

router = APIRouter(prefix="/api/contracts", tags=["Contracts"])


class ReaderContractBundle(BaseModel):
    version: str
    semantics: dict[str, str]
    schemas: dict[str, dict[str, Any]]


@router.get("/reader-qa/v1", response_model=ReaderContractBundle)
def api_reader_qa_contract():
    return {
        "version": "1",
        "semantics": {
            "scope_owner": "DSReaderHelper resolves reader scope to opaque constraints.",
            "omitted_filter": "No retrieval_filter means unrestricted KB retrieval.",
            "empty_filter": "An explicit empty retrieval_filter matches no indexed documents.",
            "ordinal_cap": "max_ordinal is inclusive and enforced before retrieval and expansion.",
            "retrieval_query": "Optional retrieval_query drives rewrite, embedding, BM25, and reranking; query remains the generation instruction.",
            "response_locale": "Optional response_locale is a validated BCP 47 tag applied only to answer generation.",
            "query_only_compatibility": "When retrieval_query is omitted, query intentionally drives both retrieval and generation for generic chat clients.",
            "locale_omission": "When response_locale is omitted, generation applies no additional locale constraint.",
            "metadata_boundary": "Cortex does not interpret Calibre or reader-specific metadata.",
        },
        "schemas": {
            "chat_request": ChatRequest.model_json_schema(),
            "chat_response": ChatResponse.model_json_schema(),
            "external_document": ExternalDocument.model_json_schema(),
        },
    }
