import requests
import os

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
COMMAND_TEXT = '/tellme'  # 送りたいDiscordコマンド

headers = {
    'Authorization': DISCORD_TOKEN,
    'Content-Type': 'application/json',
}

json_data = {
    'content': COMMAND_TEXT,
}

response = requests.post(
    f'https://discord.com/api/v10/channels/{CHANNEL_ID}/messages',
    headers=headers,
    json=json_data
)

if response.status_code == 200:
    print('コマンドを送信しました。')
else:
    print(f'送信に失敗しました: {response.status_code}, {response.text}')
