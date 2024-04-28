from typing import Any, Dict


class FunctionStorage:
    def __init__(self):
        self.functions = {}

    def register(self, func):
        func_name = func.get_name()
        self.functions[func_name] = {
            'obj': func,
            'info': self.extract_function_info(func),
        }
        return func

    @staticmethod
    def extract_function_info(function) -> Dict[str, Any]:
        return {
            "name": function.get_name(),
            "description": function.get_description(),
            "parameters": function.get_params_schema(),
        }

    def get_functions_info(self):
        functions = []
        for function in self.functions.values():
            function_info = function['info']
            functions.append(function_info)

        return functions

    def get_tools_info(self):
        tools = []
        for function in self.functions.values():
            function_info = {
                "type": "function",
                "function": function['info'],
            }
            tools.append(function_info)

        return tools

    def get_system_prompt_addition(self) -> str:
        result = []
        for function in self.functions.values():
            function_obj = function['obj']
            addition = function_obj.get_system_prompt_addition()
            if addition:
                result.append(addition)
        return '\n' + '\n\n'.join(result)

    def get_function_class(self, function_name: str):
        function_obj = self.functions.get(function_name)
        if not function_obj:
            raise ValueError(f"Unknown function: {function_name}")
        return function_obj['obj']
