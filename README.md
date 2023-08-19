# chatgpt-tg

This GitHub repository contains the implementation of a telegram bot, designed to facilitate seamless interaction with GPT-3.5 and GPT-4, state-of-the-art language models by OpenAI.

🔑 **Key Features**

1. **Dynamic Dialog Management**: The bot automatically manages the context of the conversation, eliminating the need for the user to manually reset the context using the /reset command. You still can reset dialog manually if needed.
2. **Automatic Context Summarization**: In case the context size exceeds the model's maximum limit, the bot automatically summarizes the context to ensure the continuity of the conversation.
3. **Functions Support**: You can embed functions within the bot. This allows the GPT to invoke these functions when needed, based on the context. The description of the function and its parameters are extracted from the function's docstring. See the `app/context/function_manager.py` file for more details.
4. **Sub-dialogue Mechanism**: "Chat Thread Isolation" feature, where if a message is replied to within the bot, only the corresponding message chain is considered as context. This adds an extra level of context control for the users.
5. **Voice Recognition**: The bot is capable of transcribing voice messages, allowing users to use speech as context or prompt for ChatGPT.
6. **API Usage Tracking**: The bot includes a function that tracks and provides information about the current month's usage of the OpenAI API. This allows users to monitor and manage their API usage costs.
7. **Model Support**: The bot supports both gpt-3.5-turbo and gpt-4 models with the capability to switch between them on-the-fly.
8. **Customizable System Prompts**: Enables the user to initiate conversations with custom system prompts to shape the bot's behavior.
9. **Context Window Size Customization**: The bot provides a feature to customize the maximum context window size. This allows users to set the context size for gpt-3.5-turbo and gpt-4 models individually, enabling more granular control over usage costs. This feature is particularly useful for managing API usage and optimizing the balance between cost and performance.
10. **Access Control**: The bot includes a feature for access control. Each user is assigned a role (stranger, basic, advanced, admin), and depending on the role, they gain access to the bot. Role management is carried out through a messaging mechanism, with inline buttons sent to the admin for role changes.

🔧 **Installation**

To get this bot up and running, follow these steps:

1. Set the `TELEGRAM_BOT_TOKEN` and `OPENAI_TOKEN` variables in the `settings.py` file.
2. Run `docker-compose up -d` in the root directory of the project.

🤖 **Commands**
```
/reset - reset current dialog
/gpt3 - set model to gpt-3.5-turbo
/gpt4 - set model to gpt-4
/settings - open settings menu
/usage - show usage for current month
```
These commands will provide additional interaction control for the bot users.
