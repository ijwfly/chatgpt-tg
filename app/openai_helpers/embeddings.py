from dataclasses import dataclass
from typing import List

import numpy as np
import openai

EMBEDDING_MODEL = 'text-embedding-ada-002'


def vector_similarity(x: List[float], y: List[float]) -> float:
    return float(np.dot(np.array(x), np.array(y)))


@dataclass
class EmbeddedText:
    text: str
    embedding: List[float]


async def get_embeddings(strings: List[str]) -> List[EmbeddedText]:
    response = await openai.Embedding.acreate(
        model=EMBEDDING_MODEL,
        input=strings,
    )
    result = []
    for string, embedding_openai in zip(strings, response['data']):
        embedding = embedding_openai.embedding
        result.append(EmbeddedText(string, embedding))
    return result
