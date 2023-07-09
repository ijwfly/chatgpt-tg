import re

import settings

import httpx


FIELDS_TO_EXTRACT = ['Input interpretation', 'Result', 'Results']


async def query_wolframalpha(query: str):
    """
    Query WolframAlpha for factual info and calculations
    :param query: query WolframAlpha, like "weather in Moscow"
    :return:
    """
    url = 'https://www.wolframalpha.com/api/v1/llm-api'
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params={
            'appid': settings.WOLFRAMALPHA_APPID,
            'input': query,
        })
    if resp.status_code != 200:
        raise Exception(f'WolframAlpha returned {resp.status_code} status code with message: {resp.text}')

    if not 'Result' in resp.text and not 'Results' in resp.text:
        return resp.text

    results = []
    for field in FIELDS_TO_EXTRACT:
        pattern = r'({}:\n.+?)\n\n'.format(field)
        results += re.findall(pattern, resp.text, re.DOTALL)

    return '\n'.join(results)
