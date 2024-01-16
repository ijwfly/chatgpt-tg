import logging
from typing import List

from pydantic import Field

import settings
from app.bot.utils import send_telegram_message
from app.functions.base import OpenAIFunction, OpenAIFunctionParams
from app.storage.vectara import VectaraCorpusClient


# more results from vectara are useful for lexical interpolation
VECTARA_NUM_RESULTS = 10
VECTOR_SEARCH_NUM_RESULTS = 5


logger = logging.getLogger(__name__)


class VectorSearchParams(OpenAIFunctionParams):
    documents_ids: List[str] = Field(..., description='list of document ids to search in corpus')
    query: str = Field(..., description='query to search in corpus')


class VectorSearch(OpenAIFunction):
    PARAMS_SCHEMA = VectorSearchParams

    @staticmethod
    async def get_search_results(query: str, documents_ids: List[str]):
        corpus_client = VectaraCorpusClient(settings.VECTARA_API_KEY, settings.VECTARA_CUSTOMER_ID, settings.VECTARA_CORPUS_ID)
        filters = [f"doc.document_id = '{doc_id}'" for doc_id in documents_ids]
        filters = ' OR '.join(filters)
        return await corpus_client.query_corpus(query, num_results=VECTARA_NUM_RESULTS, metadata_filters=filters)

    async def run(self, params: VectorSearchParams):
        if not params.query.strip():
            logger.error("Model is trying to search for an empty query: %s", params.query)
            await send_telegram_message(self.message, "Model is trying to do vector search for an empty query. Stopping.")
            return None
        search_results = await self.get_search_results(params.query, params.documents_ids)
        search_results = search_results[:VECTOR_SEARCH_NUM_RESULTS]
        texts = [r.text for r in search_results]
        result = '\n>\n'.join(texts)
        return result

    @classmethod
    def get_name(cls) -> str:
        return "vector_search"

    @classmethod
    def get_description(cls) -> str:
        return "Search info in documents corpus by vector similarity"

    @classmethod
    def get_system_prompt_addition(cls):
        return 'You are an assistant tasked with helping user in working with documents. Your role involves extracting relevant information from provided documents to answer their questions. If the documents lack the necessary information, you must inform the office worker of this and then offer an answer based on common sense and your existing knowledge base if you have one.'
