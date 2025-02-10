from datetime import timezone
import datetime
import os
import discord
from discord.ext import tasks
from pytz import timezone
import dotenv
from server import server_thread
from discord import app_commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import mojimoji
dotenv.load_dotenv()

FURNITURE_TYPE_CONST = ["内観・外観：前景"
                        ,"内観・外観：壁紙"
                        ,"内観・外観：床"
                        ,"家具：その他"
                        ,"家具：収納"
                        ,"家具：机"
                        ,"家具：椅子"
                        ,"装飾：パーティション"
                        ,"装飾：ラグ"
                        ,"装飾：写真"
                        ,"装飾：壁装飾"
                        ,"雑貨：大型雑貨"
                        ,"雑貨：小型雑貨"
                        ,"雑貨：衣装"]

def hankaku_to_zenkaku(text):
    return mojimoji.han_to_zen(text)

async def check_not_finished(CHANNEL_ID):
    # 特定のチャンネルを取得
    channel = client.get_channel(CHANNEL_ID)
    messages = [message async for message in channel.history(limit=100)]
    messages.sort(key=lambda x:x.id)
    # 画像がついているメッセージで「done」リアクションがないものをフィルタリング
    filtered_messages = []
    for message in messages:
        
        # メッセージに添付ファイルがあるかどうか確認
        if message.attachments and 3 <= len(message.attachments) <= 4:
            has_image = any(attachment.filename.lower().endswith(('png', 'jpg', 'jpeg', 'gif')) for attachment in message.attachments)

            # 添付ファイルが画像であり、"done"リアクションがない場合
            if has_image:
                done_reaction = None
                for reaction in message.reactions:
                    if isinstance(reaction.emoji, discord.Emoji) and reaction.emoji.name == "done":
                        done_reaction = reaction
                        break
                if not done_reaction or done_reaction.count == 0:
                    if message.content:
                        new_content = message.content.split('\n')[0]
                    else:
                        new_content = ""
                    filtered_messages.append((message, new_content))
    return filtered_messages

try:
    TOKEN = os.environ.get("TOKEN")

    intents = discord.Intents.all()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    @tree.command(name="tellme",description="未入力のスクショを探してくるよ")
    async def tellme(interaction: discord.Interaction):
        filtered_messages = await check_not_finished(CHANNEL_ID:= 1290587266695036958)
        # 結果をユーザーに返信
        count = 0
        tokyo_tz = timezone('Asia/Tokyo')
        now = datetime.datetime.now(tokyo_tz)
        three_days_ago = now - datetime.timedelta(days=3)
        recent_filtered_messages = [
            msg for msg in filtered_messages 
            if msg.created_at.astimezone(tokyo_tz) < three_days_ago
        ]
        if filtered_messages:
            response = "まだ入力されてない画像を最大10件表示するよ！\n"
            for msg, new_content in filtered_messages:
                response += f"- [{new_content}](https://discord.com/channels/1289921439310417920/{CHANNEL_ID}/{msg.id})\n"
                count += 1
                if count == 10:
                    break
        else:
            response = "未入力の画像はないよ！"
        response += str(recent_filtered_messages)

        await interaction.response.send_message(response,ephemeral=True)
    @client.event
    async def on_ready():
        print('login') 
        # アクティビティを設定 
        new_activity = f"みんなのお手伝いをするよ" 
        await client.change_presence(activity=discord.Game(new_activity)) 
        # スラッシュコマンドを同期 
        await tree.sync()
        loop.start()

    @tasks.loop(hours=1)
    async def loop():
        tokyo_tz = timezone('Asia/Tokyo')
        now = datetime.datetime.now(tokyo_tz)
        if now.weekday() == 5 and now.hour == 11:
            filtered_messages = await check_not_finished(CHANNEL_ID:= 1290587266695036958)
            print(filtered_messages)
            if len(filtered_messages):
                channel = client.get_channel(CHANNEL_ID)
                await channel.send(f'未入力のスクショが{len(filtered_messages)}件あるよ！')
    
    async def sync_done(send_message):
        done_status = False
        if send_message.content.startswith('0:'):
            search_text = send_message.content[2:]  # '未:'以降の文字列
            done_status = False        
        elif send_message.content.startswith('1:'):
            search_text = send_message.content[2:]  # '済:'以降の文字列
            done_status = True
        else:
            print('what')
            return
        CHANNEL_ID = 1290587266695036958
        # 特定のチャンネルを取得
        channel = client.get_channel(CHANNEL_ID)
        messages = [message async for message in channel.history(limit=100)]
        for message in messages:
            
            # メッセージに添付ファイルがあるかどうか確認
            if message.attachments and 3 <= len(message.attachments) <= 4:
                has_image = any(attachment.filename.lower().endswith(('png', 'jpg', 'jpeg')) for attachment in message.attachments)

                # 添付ファイルが画像であり、家具名が含まれているメッセージ
                if has_image and hankaku_to_zenkaku(search_text) in hankaku_to_zenkaku(message.content):
                    # 済スタンプを付ける
                    if done_status:
                        await message.add_reaction("<:done:1290672968732774432>")
                    # 済スタンプを削除する
                    else:
                        for reaction in message.reactions:
                            if str(reaction.emoji) == "<:done:1290672968732774432>":
                                async for user in reaction.users():
                                    await message.remove_reaction(reaction.emoji, user)

    # Google Sheets APIに接続するための関数
    def connect_to_google_sheets():
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        client_credentials = {
            "type": "service_account",
            "project_id": os.environ.get("project_id"),
            "private_key_id": os.environ.get("private_key_id"),
            "private_key": os.environ.get("private_key"),
            "client_email": os.environ.get("client_email"),
            "client_id": "104427532326867566121",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": os.environ.get("client_x509_cert_url")
        }

        creds = ServiceAccountCredentials._from_parsed_json_keyfile(client_credentials, scope, None, None)
        client = gspread.authorize(creds)
        
        # スプレッドシートIDとシート名を指定
        spreadsheet_id = '1WGAQSg0vKHhmy0T-uWunqXxCuoEinTTi12RrEcStj2o'  # スプレッドシートのID
        sheet_name = '家具データ入力シート'  # シート名
        sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
        return sheet

    # スプレッドシートのC3以下の空いているセルにデータを書き込む
    def write_to_spreadsheet(furniture_name, furniture_type):
        sheet = connect_to_google_sheets()

        # C列を取得
        col_C = sheet.col_values(3)  # C列のすべての値を取得
        # 同じ文字列が存在しない場合のみ追加
        if furniture_name not in col_C:
            first_empty_row = len(col_C) + 1  # 最初の空のセルの行番号を取得
            sheet.update_cell(first_empty_row, 3, furniture_name)
        if furniture_type:
            sheet.update_cell(first_empty_row, 8, furniture_type)



    # メッセージを処理する関数
    def write_spreadsheet(message):
        # メッセージを行ごとに分割
        lines = message.content.split('\n')

        furniture_name = None
        furniture_type = None

        for line in lines:
            line = hankaku_to_zenkaku(line)
            # 「家具名：」で始まる行を探す
            if line.startswith("家具名"):
                furniture_name = line.split("家具名：")[1].strip()  # 「家具名：」の後の文字列を取得
            for furniture_const in FURNITURE_TYPE_CONST:
                # 家具種別が存在した場合は設定
                if line.startswith(furniture_const):
                    furniture_type = furniture_const
        if furniture_name:
            write_to_spreadsheet(furniture_name, furniture_type)

    @client.event
    async def on_message(message):
        
        # gas連携用チャンネルの場合、済スタンプ管理ロジックを実行
        if message.channel.id == 1297464731841597460:
            await sync_done(message)
            return
        # メッセージ送信者がBotだった場合は無視する
        if message.author.bot:
            return
        

        # 特定のチャンネルIDのみに反応させる
        specific_channel_id = (1290587266695036958,1294952504752082964) # 家具スクショチャンネル、テストチャンネル

        if message.channel.id not in specific_channel_id:
            return  # 指定したチャンネル以外では何もしない

        # メッセージが画像付きの場合、家具入力ロジック
        if message.attachments:
            write_spreadsheet(message)


    server_thread()
    # Botを実行
    client.run(TOKEN)
    
except Exception as e:
    print(e)