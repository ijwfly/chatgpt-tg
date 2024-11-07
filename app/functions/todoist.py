from typing import Optional

from pydantic import Field
from todoist_api_python.api_async import TodoistAPIAsync

import settings
from app.functions.base import OpenAIFunction, OpenAIFunctionParams


todoist = TodoistAPIAsync(settings.TODOIST_TOKEN)


class TodoistAddTaskParams(OpenAIFunctionParams):
    content: str = Field(..., description="task title (content)")
    description: Optional[str] = Field(None, description="a description for the task if needed")
    due_string: Optional[str] = Field(None, description='human defined task due date (ex.: "next Monday", "today at 9 pm"')
    due_lang: Optional[str] = Field(None, description="2-letter code specifying language in case due_string is not written in English")
    duration: Optional[int] = Field(None, description="a positive (greater than zero) integer for the amount of `duration_unit` the task will take. If specified you must define a duration_unit field.")
    duration_unit: Optional[str] = Field(None, description="the unit of time that the `duration` field above represents. Must be either `minute` or `day`.")


class TodoistAddTask(OpenAIFunction):
    PARAMS_SCHEMA = TodoistAddTaskParams

    async def run(self, params: TodoistAddTaskParams) -> Optional[str]:
        try:
            added_task = await todoist.add_task(
                content=params.content,
                description=params.description,
                due_string=params.due_string,
                due_lang=params.due_lang,
            )
            return f"Added task: {added_task}"
        except Exception as e:
            return f"Failed to add task: {str(e)}"

    @classmethod
    def get_name(cls) -> str:
        return "create_todoist_task"

    @classmethod
    def get_description(cls) -> str:
        return "Creates a new task in Todoist"

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        return "You have todoist integration. Todoist is integrated to user's calendar. Add tasks to todoist only if you asked to add task or calendar event. Don't ask for optional details, add task with minimum information."
