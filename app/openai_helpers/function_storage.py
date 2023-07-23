import inspect
import json
from typing import Any, Dict
from docstring_parser import parse

TYPE_MAPPING = {
    int: "integer",
    str: "string",
}


class FunctionStorage:
    def __init__(self):
        self.functions = {}

    def register(self, func):
        self.functions[func.__name__] = {
            'obj': func,
            'info': self.extract_function_info(func)
        }
        return func

    @staticmethod
    def extract_function_info(function) -> Dict[str, Any]:
        signature = inspect.signature(function)
        params = []
        for name, param in signature.parameters.items():
            is_required = param.default == inspect.Parameter.empty
            params.append((name, param.annotation, is_required, ""))

        function_info = {
            "name": function.__name__,
            "description": "",
            "parameters": params
        }

        docstring = inspect.getdoc(function)
        if docstring:
            parsed_docstring = parse(docstring)
            function_info["description"] = parsed_docstring.short_description

            for param in parsed_docstring.params:
                for i, (name, type_, is_required, _) in enumerate(params):
                    if param.arg_name == name:
                        params[i] = (name, type_, is_required, param.description)

        return function_info

    def get_openai_prompt(self):
        functions = []
        for function in self.functions.values():
            parameters_dict = {
                "type": "object",
                "properties": {},
                "required": [],
            }
            for name, type, is_required, description in function['info']['parameters']:
                mapped_type = TYPE_MAPPING.get(type)
                if not mapped_type:
                    raise ValueError(f"Unknown type: {type}")
                parameters_dict['properties'][name] = {
                    "type": mapped_type,
                    "description": description or name,
                }
                if is_required:
                    parameters_dict['required'].append(name)
            function_info = {
                'name': function['info']['name'],
                'description': function['info']['description'],
                'parameters': parameters_dict
            }
            functions.append(function_info)

        return functions

    @staticmethod
    def parse_function_args(function_arguments):
        try:
            return json.loads(function_arguments)
        except json.JSONDecodeError:
            return function_arguments

    async def run_function(self, function_name: str, parameters: str):
        function = self.functions[function_name]['obj']
        parsed_parameters = self.parse_function_args(parameters)
        try:
            if isinstance(parsed_parameters, str):
                result = await function(parsed_parameters)
            else:
                result = await function(**parsed_parameters)
            if not result:
                return 'Function returned nothing'
            return str(result)
        except Exception as e:
            return f'Function raised an exception: {e}'
