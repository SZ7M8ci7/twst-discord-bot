import asyncio
from datetime import timezone
import datetime
import logging
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
from sync_logic import build_sheet_statuses, extract_furniture_name, parse_sync_command
from furniture_input.post_parser import parse_post
from furniture_input.models import Result
from furniture_input.sheet_writer import build_plan, cell_request
from furniture_input.sheet_schema import verify_headers
from furniture_input.service import AutoInputService, target_post
dotenv.load_dotenv()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SCREENSHOT_CHANNEL_ID = 1290587266695036958
TEST_CHANNEL_ID = 1294952504752082964
SYNC_CHANNEL_ID = 1297464731841597460
DONE_EMOJI = "<:done:1290672968732774432>"
SCREENSHOT_HISTORY_LIMIT = 10000
SHEET_SYNC_INTERVAL_MINUTES = max(
    int(os.environ.get("SHEET_SYNC_INTERVAL_MINUTES", "1440")),
    1,
)
SYNC_SOURCE_ID = os.environ.get("SYNC_SOURCE_ID")

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
                        ,"雑貨：小物雑貨"
                        ,"雑貨：衣装"]


def get_env(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def normalize_private_key(value):
    if not value:
        return value
    return value.replace("\\n", "\n")

def hankaku_to_zenkaku(text):
    return mojimoji.han_to_zen(text)


def is_target_screenshot(message):
    return target_post(message)


def message_furniture_key(message):
    post = parse_post(message.content)
    return post["key"] if post else extract_furniture_name(message.content)


def get_done_reaction(message):
    return next(
        (reaction for reaction in message.reactions if str(reaction.emoji) == DONE_EMOJI),
        None,
    )


def is_trusted_sync_message(message):
    if SYNC_SOURCE_ID:
        source_ids = {str(message.author.id)}
        if message.webhook_id is not None:
            source_ids.add(str(message.webhook_id))
        return SYNC_SOURCE_ID in source_ids
    return message.webhook_id is not None or message.author.bot

async def check_not_finished(CHANNEL_ID):
    # 特定のチャンネルを取得
    channel = client.get_channel(CHANNEL_ID)
    messages = [message async for message in channel.history(limit=100)]
    messages.sort(key=lambda x:x.id)
    # 画像がついているメッセージで「done」リアクションがないものをフィルタリング
    filtered_messages = []
    for message in messages:
        
        # メッセージに添付ファイルがあるかどうか確認
        if is_target_screenshot(message):
            done_reaction = get_done_reaction(message)
            if not done_reaction or done_reaction.count == 0:
                if message.content:
                    new_content = message.content.split('\n')[0]
                else:
                    new_content = ""
                filtered_messages.append((message, new_content))
    return filtered_messages

try:
    TOKEN = get_env("DISCORD_TEAM_TOKEN", "DISCORD_TOKEN", "TOKEN")
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TEAM_TOKEN, DISCORD_TOKEN, or TOKEN is not set."
        )

    intents = discord.Intents.all()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    restore_started = False
    screenshot_messages_by_name = {}

    @tree.command(name="tellme",description="未入力のスクショを探してくるよ")
    async def tellme(interaction: discord.Interaction):
        filtered_messages = await check_not_finished(CHANNEL_ID:= 1290587266695036958)
        # 結果をユーザーに返信
        count = 0
        if filtered_messages:
            response = "まだ入力されてない画像を最大10件表示するよ！\n"
            for msg, new_content in filtered_messages:
                response += f"- [{new_content}](https://discord.com/channels/1289921439310417920/{CHANNEL_ID}/{msg.id})\n"
                count += 1
                if count == 10:
                    break
        else:
            response = "未入力の画像はないよ！"

        await interaction.response.send_message(response,ephemeral=True)
    @client.event
    async def on_ready():
        global restore_started
        print('login') 
        # アクティビティを設定 
        new_activity = f"みんなのお手伝いをするよ" 
        await client.change_presence(activity=discord.Game(new_activity)) 
        # スラッシュコマンドを同期 
        await tree.sync()
        if not loop.is_running():
            loop.start()
        if not restore_started:
            restore_started = True
            try:
                await restore_done_reactions()
                if not sheet_reconciliation_loop.is_running():
                    sheet_reconciliation_loop.start()
            except Exception:
                restore_started = False
                logger.exception("Failed to restore done reactions")

    @tasks.loop(hours=1)
    async def loop():
        tokyo_tz = timezone('Asia/Tokyo')
        now = datetime.datetime.now(tokyo_tz)
        if now.weekday() == 5 and now.hour == 11:
            filtered_messages = await check_not_finished(CHANNEL_ID:= 1290587266695036958)
            tokyo_tz = timezone('Asia/Tokyo')
            now = datetime.datetime.now(tokyo_tz)
            days_ago = now - datetime.timedelta(days=7)
            recent_filtered_messages = [
                msg for msg in filtered_messages 
                if msg[0].created_at.astimezone(tokyo_tz) < days_ago
            ]
            if len(recent_filtered_messages):
                channel = client.get_channel(CHANNEL_ID)
                await channel.send(f'未入力のスクショが{len(filtered_messages)}件あるよ！')
        else:
            filtered_messages = await check_not_finished(CHANNEL_ID:= 1290587266695036958)
            tokyo_tz = timezone('Asia/Tokyo')
            now = datetime.datetime.now(tokyo_tz)
            days_ago = now - datetime.timedelta(days=7)
            recent_filtered_messages = [
                msg for msg in filtered_messages 
                if msg[0].created_at.astimezone(tokyo_tz) < days_ago
            ]
            print(recent_filtered_messages)
    
    async def sync_done(send_message):
        command = parse_sync_command(send_message.content)
        if command is None:
            logger.warning(
                "Ignored malformed sync message id=%s author=%s",
                send_message.id,
                send_message.author.id,
            )
            return
        done_status, furniture_name = command
        channel = client.get_channel(SCREENSHOT_CHANNEL_ID)
        if channel is None:
            channel = await client.fetch_channel(SCREENSHOT_CHANNEL_ID)
        messages = [
            message async for message in channel.history(limit=SCREENSHOT_HISTORY_LIMIT)
        ]
        matched_count = 0
        changed_count = 0
        for message in messages:
            if not is_target_screenshot(message):
                continue
            if message_furniture_key(message) != furniture_name:
                continue

            matched_count += 1
            done_reaction = get_done_reaction(message)
            if done_status and (done_reaction is None or not done_reaction.me):
                await message.add_reaction(DONE_EMOJI)
                changed_count += 1
            elif not done_status and done_reaction is not None and done_reaction.me:
                await message.remove_reaction(done_reaction.emoji, client.user)
                changed_count += 1

        logger.info(
            "Processed sync message id=%s status=%s furniture=%r matched=%d changed=%d",
            send_message.id,
            int(done_status),
            furniture_name,
            matched_count,
            changed_count,
        )

    async def restore_done_reactions():
        global screenshot_messages_by_name
        screenshot_channel = client.get_channel(SCREENSHOT_CHANNEL_ID)
        if screenshot_channel is None:
            screenshot_channel = await client.fetch_channel(SCREENSHOT_CHANNEL_ID)

        screenshot_messages = [
            message
            async for message in screenshot_channel.history(limit=SCREENSHOT_HISTORY_LIMIT)
            if is_target_screenshot(message)
        ]
        screenshot_messages_by_name = {}
        for message in screenshot_messages:
            furniture_name = message_furniture_key(message)
            if furniture_name:
                screenshot_messages_by_name.setdefault(furniture_name, []).append(message)

        result = await reconcile_done_reactions_from_sheet()
        logger.info(
            "Screenshot index initialized screenshots=%d names=%d",
            len(screenshot_messages),
            len(screenshot_messages_by_name),
        )
        return result

    def load_sheet_statuses():
        sheet = connect_to_google_sheets()
        return build_sheet_statuses(sheet.get("B3:C"))

    async def reconcile_done_reactions_from_sheet():
        sheet_statuses = await asyncio.to_thread(load_sheet_statuses)
        added_count = 0
        removed_count = 0
        matched_names = 0
        for furniture_name, messages in screenshot_messages_by_name.items():
            done_status = sheet_statuses.get(furniture_name)
            if done_status is None:
                continue
            matched_names += 1
            for message in messages:
                done_reaction = get_done_reaction(message)
                if done_status and (done_reaction is None or not done_reaction.me):
                    await message.add_reaction(DONE_EMOJI)
                    added_count += 1
                elif not done_status and done_reaction is not None and done_reaction.me:
                    await message.remove_reaction(done_reaction.emoji, client.user)
                    removed_count += 1

        logger.info(
            "Sheet reconciliation completed sheet_names=%d matched_names=%d "
            "unmatched_screenshots=%d added=%d removed=%d",
            len(sheet_statuses),
            matched_names,
            len(screenshot_messages_by_name) - matched_names,
            added_count,
            removed_count,
        )
        return added_count, removed_count

    @tasks.loop(minutes=SHEET_SYNC_INTERVAL_MINUTES)
    async def sheet_reconciliation_loop():
        try:
            await reconcile_done_reactions_from_sheet()
        except Exception:
            logger.exception("Periodic sheet reconciliation failed")

    @sheet_reconciliation_loop.before_loop
    async def before_sheet_reconciliation_loop():
        await client.wait_until_ready()
        await asyncio.sleep(SHEET_SYNC_INTERVAL_MINUTES * 60)

    # Google Sheets APIに接続するための関数
    def connect_to_google_sheets():
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        client_credentials = {
            "type": "service_account",
            "project_id": get_env("GOOGLE_PROJECT_ID", "project_id"),
            "private_key_id": get_env("GOOGLE_PRIVATE_KEY_ID", "private_key_id"),
            "private_key": normalize_private_key(get_env("GOOGLE_PRIVATE_KEY", "private_key")),
            "client_email": get_env("GOOGLE_CLIENT_EMAIL", "client_email"),
            "client_id": get_env("GOOGLE_CLIENT_ID", "client_id") or "104427532326867566121",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": get_env("GOOGLE_CLIENT_X509_CERT_URL", "client_x509_cert_url")
        }

        creds = ServiceAccountCredentials._from_parsed_json_keyfile(client_credentials, scope, None, None)
        client = gspread.authorize(creds)
        
        # スプレッドシートIDとシート名を指定
        spreadsheet_id = os.getenv('GOOGLE_SPREADSHEET_ID', '1WGAQSg0vKHhmy0T-uWunqXxCuoEinTTi12RrEcStj2o')
        sheet_name = os.getenv('GOOGLE_WORKSHEET_NAME', '家具データ入力シート')
        sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
        return sheet

    # スプレッドシートのC3以下の空いているセルにデータを書き込む
    def write_to_spreadsheet(furniture_name, furniture_type):
        sheet = connect_to_google_sheets()
        verify_headers(sheet.get("B1:BF2"))

        post = parse_post(f"{furniture_name}\n{furniture_type}")
        rows = sheet.get("B3:BF", value_render_option="FORMULA")
        plan = build_plan(rows, post, Result())
        if plan.values and rows == sheet.get("B3:BF", value_render_option="FORMULA"):
            requests = [cell_request(sheet.id, plan.row, c, v) for c, v in plan.values.items()]
            requests.extend(cell_request(sheet.id, plan.row, c, v, True) for c, v in plan.formula_values.items())
            if plan.row > sheet.row_count:
                requests.insert(0, {"appendDimension": {"sheetId": sheet.id, "dimension": "ROWS", "length": plan.row-sheet.row_count}})
            sheet.spreadsheet.batch_update({"requests": requests})




    # メッセージを処理する関数
    def write_spreadsheet(message):
        post = parse_post(message.content)
        if post:
            write_to_spreadsheet(post["name"], post["category"])

    auto_channels = {int(value) for value in os.getenv(
        "FURNITURE_AUTO_INPUT_CHANNEL_IDS", f"{SCREENSHOT_CHANNEL_ID},{TEST_CHANNEL_ID}"
    ).split(",") if value.strip()}
    auto_input = AutoInputService(client, connect_to_google_sheets, auto_channels)
    legacy_write_lock = asyncio.Lock()

    @client.event
    async def on_message(message):
        
        # gas連携用チャンネルの場合、済スタンプ管理ロジックを実行
        if message.channel.id == SYNC_CHANNEL_ID:
            if not is_trusted_sync_message(message):
                logger.warning(
                    "Ignored untrusted sync message id=%s author=%s webhook=%s",
                    message.id,
                    message.author.id,
                    message.webhook_id,
                )
                return
            await sync_done(message)
            return
        # メッセージ送信者がBotだった場合は無視する
        if message.author.bot:
            return
        

        # 特定のチャンネルIDのみに反応させる
        specific_channel_id = (SCREENSHOT_CHANNEL_ID, TEST_CHANNEL_ID) # 家具スクショチャンネル、テストチャンネル

        if message.channel.id not in set(specific_channel_id) | auto_channels:
            return  # 指定したチャンネル以外では何もしない

        # メッセージが画像付きの場合、家具入力ロジック
        if message.attachments:
            if message.channel.id == SCREENSHOT_CHANNEL_ID and is_target_screenshot(message):
                furniture_name = message_furniture_key(message)
                if furniture_name:
                    screenshot_messages_by_name.setdefault(furniture_name, []).append(message)
            if auto_input.mode == "off":
                async with legacy_write_lock:
                    await asyncio.to_thread(write_spreadsheet, message)
            else:
                try:
                    await auto_input.submit(message)
                except Exception as exc:
                    # This event ends here; no recovery queue or automatic replay.
                    logger.warning("Furniture submission failed id=%s error=%s", message.id, type(exc).__name__)

    @client.event
    async def on_raw_message_edit(payload):
        if payload.channel_id not in auto_channels or not {"content", "attachments"}.intersection(payload.data):
            return
        try:
            channel = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            for name in list(screenshot_messages_by_name):
                screenshot_messages_by_name[name] = [m for m in screenshot_messages_by_name[name] if m.id != message.id]
            if target_post(message) and not message.author.bot:
                name = message_furniture_key(message)
                screenshot_messages_by_name.setdefault(name, []).append(message)
            await auto_input.submit(message)
        except Exception as exc:
            logger.warning("Furniture edit failed id=%s error=%s", payload.message_id, type(exc).__name__)


    server_thread()
    # Botを実行
    client.run(TOKEN)
    
except Exception as e:
    print(e)
