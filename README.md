# chatgpt-tg

This GitHub repository contains the implementation of a telegram bot, designed to facilitate seamless interaction with GPT-3.5 and GPT-4, state-of-the-art language models by OpenAI.  

🔥 **GPT-4 Turbo support (with vision)**  
🔥 **Custom OpenAI API compatible endpoints support (see `app/llm_models.py` for example of using WizardLM-2 8x22b via OpenRouter.ai)**  
🔥 **DALL-E 3 Image generation support**

🔑 **Key Features**

1. **Model Support**: all OpenAI models are supported out of the box. Also you can add OpenAI API compatible endpoints by adding them to `app/llm_models.py`
2. **Image Generation**: You can ask bot to generate images using DALL-E 3 model, use bot just like official chatgpt app.
3. **Dynamic Dialog Management**: The bot automatically manages the context of the conversation, eliminating the need for the user to manually reset the context using the /reset command. You still can reset dialog manually if needed.
4. **Automatic Context Summarization**: In case the context size exceeds the model's maximum limit, the bot automatically summarizes the context to ensure the continuity of the conversation.
5. **Function calling support**: You can embed functions within the bot. This allows the GPT to invoke these functions when needed, based on the context. `app/context/function_manager.py` file for more details.
6. **Sub-dialogue Mechanism**: When you reply to a message, the bot only looks at that specific conversation thread, making it easier to manage multiple discussions at once.
7. **Voice Recognition**: The bot is capable of transcribing voice messages, allowing users to use speech as context or prompt for ChatGPT.
8. **API Usage Tracking**: The bot includes a function that tracks and provides information about the usage of the OpenAI API. This allows users to monitor and manage their API usage costs.
9. **Context Window Size Customization**: You can setup maximum context window size for each model in `app/context/context_manager.py` file. When context size exceeds this limit, bot will automatically summarize context.
10. **Access Control**: The bot includes a feature for access control. Each user is assigned a role (stranger, basic, advanced, admin), and depending on the role, they gain access to the bot. Role management is carried out through a messaging mechanism, with inline buttons sent to the admin for role changes.

🔧 **Installation**

To get this bot up and running, follow these steps:

1. Set the `TELEGRAM_BOT_TOKEN` and `OPENAI_TOKEN` variables in the `settings.py` file.
2. Set the `IMAGE_PROXY_URL` to your server IP / hostname in the `settings.py` file.
3. (optional) Set the `USER_ROLE_MANAGER_CHAT_ID` variable in the `settings.py` file to your telegram id. This is required for access control.
4. (optional) Set the `ENABLE_USER_ROLE_MANAGER_CHAT` variable in the `settings.py` file to `True`. This is required for access control.
5. (optional) Set the `USER_ROLE_*` variables in the `settings.py` file to desired roles.
6. Run `docker-compose up -d` in the root directory of the project.

If you've done optional steps, when you send your first message to the bot, you will get a management message with your telegram id and info. You can use this message to setup your role as admin.

🤖 **Commands**
```
/reset - reset current dialog
/usage - show usage for current month
/models - open models menu
/settings - open settings menu
/text2speech - generate voice message from message (last message or replied)
/usage_all - show usage for all users
```
These commands will provide additional interaction control for the bot users. You can find most settings in settings menu, commands are just shortcuts for them.


⚠️ **Troubleshooting**

If you have any issues with the bot, please create an issue in this repository. I will try to help you as soon as possible.  

Here are some typical issues and solutions:  
- ```Error code: 400 - {'error': {'message': 'Invalid image.', 'type': 'invalid_request_error' ...}}``` - This error usually occurs when openai cannot access the image. Make sure you set up the `IMAGE_PROXY_URL` variable correctly with your server IP / hostname. 
You can try to open this url in your browser to check if it works. Also you can debug the setup by looking at `chatgpttg.message` table in postgres, there will be message with image url. You can try to open this url in your browser to check if it works.
- ```Error code: 400 - {'error': {'message': 'Invalid content type. image_url is only supported by certain models.', 'type': 'invalid_request_error' ...}}``` - This error usually occurs when you have image in your context, but current model doesn't support vision. You can try to change model to gpt-4-vision-preview or reset your context with /reset command.
