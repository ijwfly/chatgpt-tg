from app.llm_models import get_model_by_name


class LLMClientFactory:
    _model_clients = {}

    @classmethod
    def get_client(cls, model_name: str):
        if model_name not in cls._model_clients:
            llm_model = get_model_by_name(model_name)
            params = {
                'api_key': llm_model.api_key,
            }
            if llm_model.base_url:
                params['base_url'] = llm_model.base_url
            cls._model_clients[model_name] = llm_model.api_client(**params)
        return cls._model_clients[model_name]
