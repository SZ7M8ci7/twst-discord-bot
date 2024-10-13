import discord
import dotenv
import os
import cv2
import numpy as np
import os
import glob
import aiohttp
from server import server_thread

dotenv.load_dotenv()


TOKEN = os.environ.get("TOKEN")
intents = discord.Intents.all()
client = discord.Client(intents=intents)

# テンプレート画像のフォルダ
templates_folder = '/bot/app'


# 画像を読み込む関数
def imread_unicode_from_bytes(file_bytes):
    try:
        file_bytes_np = np.asarray(bytearray(file_bytes), dtype=np.uint8)
        img = cv2.imdecode(file_bytes_np, 1)
        return img
    except Exception as e:
        print(f"Error loading image from bytes, error: {e}")
        return None


# テンプレートマッチングの関数
async def multi_scale_template_matching(image, template, min_scale=0.5, max_scale=1.2, step=0.1, method=cv2.TM_CCOEFF_NORMED):
    best_match = None
    best_value = -1
    h, w = template.shape[:2]

    # スケールをループ
    scale = min_scale
    while scale <= max_scale:
        # テンプレート画像をリサイズ
        resized_template = cv2.resize(template, (int(w * scale), int(h * scale)))
        result = await cv2.matchTemplate(image, resized_template, method)

        # テンプレートマッチングの結果から最小値と最大値を取得
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        # 最大値が現在の最良の値よりも良ければ更新
        if max_val > best_value:
            best_value = max_val
            best_match = (max_loc, scale)

        scale += step

    return best_match, best_value


# メッセージに画像が投稿されたときの処理
@client.event
async def on_message(message):
    # メッセージ送信者がBotだった場合は無視する
    if message.author.bot:
        return
    if message.attachments:
        try:
            for attachment in message.attachments:
                # メッセージの画像を取得
                if attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    async with aiohttp.ClientSession() as session:
                        async with session.get(attachment.url) as resp:
                            if resp.status == 200:
                                image_bytes = await resp.read()
                                image = imread_unicode_from_bytes(image_bytes)

                                # マッチング対象画像の読み込みエラーチェック
                                if image is None:
                                    await message.channel.send("Error: Could not load the image.")
                                    return

                                # テンプレート画像ファイルのパスを取得（jpg, png対応）
                                template_files = glob.glob(os.path.join(templates_folder, '*.jpg')) + glob.glob(
                                    os.path.join(templates_folder, '*.png'))

                                all_matches = []

                                # 最適なスケールを探すための変数
                                best_scale_overall = None
                                best_match_value_overall = -1

                                # すべてのテンプレート画像に対して最適なスケールを決定
                                for template_file in template_files:
                                    # テンプレート画像の読み込み
                                    template = cv2.imread(template_file)

                                    # テンプレート画像の読み込みエラーチェック
                                    if template is None:
                                        print(f"Error: Could not load template image from path: {template_file}")
                                        continue

                                    # 各テンプレートでのマルチスケールテンプレートマッチングの実行
                                    best_match, best_value = await multi_scale_template_matching(image, template)

                                    # 最適なスケールとテンプレートを保持
                                    if best_value > best_match_value_overall:
                                        best_match_value_overall = best_value
                                        best_scale_overall = best_match[1]

                                # 最もマッチする倍率が確定したら、その倍率で全テンプレート画像にマッチング
                                for template_file in template_files:
                                    # テンプレート画像の読み込み
                                    template = cv2.imread(template_file)

                                    if template is None:
                                        continue

                                    # 決定した最適なスケールでテンプレート画像をリサイズ
                                    h, w = template.shape[:2]
                                    resized_template = cv2.resize(template, (int(w * best_scale_overall), int(h * best_scale_overall)))

                                    # 再度マッチングを行い、座標を取得
                                    result = await cv2.matchTemplate(image, resized_template, cv2.TM_CCOEFF_NORMED)
                                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

                                    # マッチした結果をリストに追加
                                    all_matches.append((template_file, max_loc, max_val))

                                # 座標の左から順にソート
                                all_matches.sort(key=lambda x: x[1][0])

                                # 結果の出力（拡張子を除いたテンプレート画像のファイル名とマッチング精度）
                                output_messages = []
                                accuracy = 1
                                for match in all_matches:
                                    template_file, max_loc, max_val = match
                                    file_name = os.path.splitext(os.path.basename(template_file))[0]
                                    if max_val > 0.9:
                                        accuracy *= max_val
                                        output_messages.append(f"{file_name}")

                                if output_messages:
                                    # スレッドを作成して返信
                                    thread = await message.create_thread(name="画像認識の結果だよ！")
                                    await thread.send(f"認識精度は{round(accuracy*100, 1)}％ぐらい")
                                    for output in output_messages:
                                        await thread.send(output)
                                    # すべてのメッセージを送信後にスレッドをアーカイブ（クローズ）
                                    await thread.edit(archived=True)
                # 1ファイル目で終了
                break
        except:
            print('error')

server_thread()
# Botを実行
client.run(TOKEN)
