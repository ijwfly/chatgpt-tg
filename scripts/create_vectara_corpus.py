import json
import http.client
from http.client import HTTPSConnection
from urllib.parse import urlencode


def get_access_token_with_client_credentials(client_id, client_secret, auth_url):
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    params = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret
    }

    encoded_params = urlencode(params)

    parsed_url = http.client.urlsplit(auth_url)
    host = parsed_url.hostname
    path = parsed_url.path

    connection = None
    try:
        connection = http.client.HTTPSConnection(host)
        connection.request('POST', path, body=encoded_params, headers=headers)
        response = connection.getresponse()
        response_data = response.read()

        if response.status != 200:
            print(f"Error: Received status code {response.status}")
            print("Response content:")
            print(response_data.decode())
            return None

        response_json = json.loads(response_data.decode())
        access_token = response_json.get('access_token')

        if access_token is None:
            print("Error: 'access_token' not found in the response.")
            print("Response content:")
            print(response_data.decode())
            return None

        return access_token

    finally:
        if connection:
            connection.close()


def create_corpus(customer_id: int, jwt_token: str, corpus_name: str = "chatgpt-tg", corpus_description: str = "chatgpt-tg"):
    headers = {
        'Content-Type': 'application/json',
        'customer-id': str(customer_id),
        'Authorization': f'Bearer {jwt_token}',
    }

    payload = {
        "corpus": {
            "name": corpus_name,
            "description": corpus_description,
            "filterAttributes": [
                {
                    "name": "document_id",
                    "description": "Document ID for chatgpt-tg",
                    "indexed": True,
                    "type": "FILTER_ATTRIBUTE_TYPE__TEXT",
                    "level": "FILTER_ATTRIBUTE_LEVEL__DOCUMENT"
                }
            ]
        }
    }

    json_payload = json.dumps(payload)

    connection = None
    try:
        connection = HTTPSConnection('api.vectara.io')
        connection.request('POST', '/v1/create-corpus', body=json_payload, headers=headers)
        response = connection.getresponse()
        response_data = response.read()

        if response.status != 200:
            print(f"Error: Received status code {response.status}")
            print("Response content:")
            print(response_data.decode())
            return None

        response_json = json.loads(response_data.decode())
        corpus_id = response_json.get('corpusId')

        if corpus_id is None:
            print("Error: 'corpusId' not found in the response.")
            print("Response content:")
            print(response_data.decode())
            return None

        return corpus_id

    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    authentication_url = input("Enter auth URL: ")
    authentication_client_id = input("Enter auth client_id: ")
    authentication_secret = input("Enter auth secret: ")

    jwt = get_access_token_with_client_credentials(authentication_client_id, authentication_secret, authentication_url)

    customer_id = int(input("Enter your customer ID: "))
    corpus_name = input("Enter corpus name (or press Enter for default 'chatgpt-tg'): ")
    corpus_description = input("Enter corpus description (or press Enter for default 'chatgpt-tg'): ")

    corpus_name = corpus_name if corpus_name else "chatgpt-tg"
    corpus_description = corpus_description if corpus_description else "chatgpt-tg"

    corpus_id = create_corpus(customer_id, jwt, corpus_name, corpus_description)
    print(f"Corpus created with ID: {corpus_id}")

    print('Here is your settings, put them in settings.py (do not forget to fill VECTARA_API_KEY):')
    print(f'VECTARA_CUSTOMER_ID = {customer_id}')
    print(f'VECTARA_CORPUS_ID = {corpus_id}')
