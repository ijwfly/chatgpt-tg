# chatgpt-tg

This GitHub repository contains the implementation of a telegram bot, designed to facilitate seamless interaction with GPT-3.5 and GPT-4, a state-of-the-art language models by OpenAI.

**Key Features**

1. **Model Support**: The bot supports both gpt-3.5-turbo and gpt-4 models with the capability to switch between them on-the-fly.
2. **Customizable System Prompts**: Enables the user to initiate conversations with custom system prompts to shape the bot's behavior.
3. **Conversation Context Preservation**: The bot retains the context of the ongoing dialogue to provide relevant and cohesive responses.
4. **Sub-dialogue Mechanism**: "Chat Thread Isolation" feature, where if a message is replied to within the bot, only the corresponding message chain is considered as context. This adds an extra level of context control for the users.
5. **Voice Recognition**: The bot is capable of transcribing voice messages, allowing users to use speech as context or prompt for ChatGPT.
6. **Functions Support**: You can embed functions within bot. This allows the GPT to invoke these functions when needed, based on the context. The description of the function and its parameters are extracted from the function's docstring.

The purpose of this telegram bot is to create a ChatGpt-like user-friendly platform for interacting with GPT models. The repository is open for exploration, feedback, and contributions.

**Installation**

To get this bot up and running, follow these steps:

1. Set the `TELEGRAM_BOT_TOKEN` and `OPENAI_TOKEN` variables in the `settings.py` file.
2. Run `docker-compose up -d` in the root directory of the project.

In addition, you should configure the following /commands in your bot through BotFather:
```
reset - reset current dialog
gpt3 - set model to gpt-3.5-turbo
gpt4 - set model to gpt-4
usage - show usage for current month
settings - open settings menu
```
These commands will provide additional interaction control for the bot users.
