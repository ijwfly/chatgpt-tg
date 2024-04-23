import openai

from app.llm_models import get_models


class OpenAILLMClient:
    _model_clients = {}

    @classmethod
    def get_client(cls, model_name: str):
        if model_name not in cls._model_clients:
            llm_model = get_models().get(model_name)
            if not llm_model:
                raise ValueError(f"Unknown model: {model_name}")
            params = {
                'api_key': llm_model.api_key,
            }
            if llm_model.base_url:
                params['base_url'] = llm_model.base_url
            cls._model_clients[model_name] = openai.AsyncOpenAI(**params)
        return cls._model_clients[model_name]
