import os
import json
from typing import List

import httpx
import pydantic

VECTARA_BASE_URL = 'https://api.vectara.io/v1'


VECTARA_SUPPORTED_EXTENSIONS = ['md', 'pdf', 'doc', 'docx', 'ppt', 'pptx', 'txt', 'html', 'lxml', 'rtf', 'epub', 'eml']


class SearchResult(pydantic.BaseModel):
    text: str
    score: float
    metadata: List[dict]


class VectaraCorpusClient:
    def __init__(self, api_key: str, customer_id: int, corpus_id: int):
        self.api_key = api_key
        self.customer_id = customer_id
        self.corpus_id = corpus_id

    async def upload_document(self, file, *, doc_metadata: dict = None):
        # supported document types: md, pdf, odt, doc, docx, ppt, pptx, txt, html, lxml, rtf, epub, email files RFC 822
        url = f'{VECTARA_BASE_URL}/upload'
        headers = {
            'customer-id': str(self.customer_id),
            'x-api-key': self.api_key,
        }
        params = {
            'c': str(self.customer_id),
            'o': str(self.corpus_id),
        }
        async with httpx.AsyncClient() as client:
            file_name = os.path.basename(file.name)
            file_data = {
                'file': (file_name, file),
            }
            if doc_metadata:
                file_data['doc_metadata'] = (None, json.dumps(doc_metadata), 'application/json')
            resp = await client.post(url, headers=headers, params=params, files=file_data)
            if resp.status_code != 200:
                raise Exception(f'Vectara upload file failed with code {resp.status_code}')
            return resp.json()

    async def query_corpus(self, query: str, *, num_results: int = 10, metadata_filters: str = None):
        url = f'{VECTARA_BASE_URL}/query'
        headers = {
            'customer-id': str(self.customer_id),
            'x-api-key': self.api_key,
        }
        params = {
            'query': [
                {
                    'query': query,
                    'numResults': num_results,
                    "corpusKey": [
                        {
                            "customerId": self.customer_id,
                            "corpusId": self.corpus_id,
                            "lexicalInterpolationConfig": {
                                "lambda": 0.025
                            },
                        }
                    ],
                },
            ],
        }

        if metadata_filters:
            params['query'][0]['corpusKey'][0]['metadataFilter'] = metadata_filters

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=params)
            if resp.status_code != 200:
                raise Exception(f'Vectara query failed with code {resp.status_code}')
            result = resp.json()
            result = result['responseSet'][0]['response']
            return [SearchResult(**r) for r in result]
