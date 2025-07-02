
import discord
from discord.ext import commands
from discord import app_commands
import json
import asyncio
from datetime import datetime, timezone
import os
from flask import Flask
import threading

# Flask webサーバーの設定（起動確認用）
app = Flask(__name__)

@app.route('/')
def health_check():
    if bot.is_ready():
        guild_count = len(bot.guilds)
        return f'''
        <h1>Discord Bot Status</h1>
        <p>Status: <span style="color: green;">Online</span></p>
        <p>Bot Name: {bot.user.name if bot.user else "Not Ready"}</p>
        <p>Servers: {guild_count}</p>
        <p>Latency: {round(bot.latency * 1000)}ms</p>
        '''
    else:
        return '''
        <h1>Discord Bot Status</h1>
        <p>Status: <span style="color: red;">Offline</span></p>
        '''

@app.route('/health')
def health():
    return {"status": "ok", "bot_ready": bot.is_ready()}

# Discord botの設定
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# 継続ログ設定を保存する辞書
continuous_logging = {}
# フォーマット: {guild_id: {'log_server_id': str, 'channels': [channel_ids], 'log_channel': channel_obj}}

@bot.event
async def on_ready():
    print(f'{bot.user} がログインしました')
    print(f'Bot ID: {bot.user.id}')
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Failed to sync commands: {e}')

@bot.event
async def on_message(message):
    # Botのメッセージは無視
    if message.author == bot.user:
        return
    
    # 継続ログが設定されているサーバーかチェック
    if message.guild and str(message.guild.id) in continuous_logging:
        log_config = continuous_logging[str(message.guild.id)]
        
        # 監視対象のチャンネルかチェック
        if not log_config['channels'] or str(message.channel.id) in log_config['channels']:
            try:
                # メッセージ情報を構築
                message_info = {
                    'id': str(message.id),
                    'channel': message.channel.name,
                    'channel_id': str(message.channel.id),
                    'guild': message.guild.name,
                    'guild_id': str(message.guild.id),
                    'author': str(message.author),
                    'author_id': str(message.author.id),
                    'display_name': message.author.display_name,
                    'is_webhook': message.webhook_id is not None,
                    'webhook_id': str(message.webhook_id) if message.webhook_id else None,
                    'content': message.content,
                    'timestamp': message.created_at.isoformat(),
                    'attachments': [att.url for att in message.attachments],
                    'embeds': len(message.embeds),
                    'embed_details': [
                        {
                            'title': embed.title,
                            'description': embed.description,
                            'url': embed.url,
                            'color': embed.color.value if embed.color else None,
                            'timestamp': embed.timestamp.isoformat() if embed.timestamp else None,
                            'footer': {'text': embed.footer.text, 'icon_url': embed.footer.icon_url} if embed.footer else None,
                            'author': {'name': embed.author.name, 'url': embed.author.url, 'icon_url': embed.author.icon_url} if embed.author else None,
                            'thumbnail': embed.thumbnail.url if embed.thumbnail else None,
                            'image': embed.image.url if embed.image else None,
                            'fields': [{'name': field.name, 'value': field.value, 'inline': field.inline} for field in embed.fields]
                        } for embed in message.embeds
                    ],
                    'mentions': [str(user) for user in message.mentions],
                    'reactions': []  # リアクションは後から追加されるので空で初期化
                }
                
                # ログチャンネルに送信
                log_channel = log_config['log_channel']
                embed = discord.Embed(
                    title="📝 新しいメッセージ",
                    color=0x00ff00,
                    timestamp=message.created_at
                )
                embed.add_field(name="サーバー", value=message.guild.name, inline=True)
                embed.add_field(name="チャンネル", value=f"#{message.channel.name}", inline=True)
                embed.add_field(name="投稿者", value=f"{message.author.display_name} ({message.author})", inline=True)
                
                if message.content:
                    content = message.content[:1000] + "..." if len(message.content) > 1000 else message.content
                    embed.add_field(name="内容", value=content, inline=False)
                
                if message.attachments:
                    attachment_list = "\n".join([att.url for att in message.attachments])
                    embed.add_field(name="添付ファイル", value=attachment_list[:1000], inline=False)
                
                if message.embeds:
                    embed.add_field(name="埋め込み", value=f"{len(message.embeds)}個の埋め込み", inline=True)
                
                if message.webhook_id:
                    embed.add_field(name="⚠️ マスカレード", value="Webhookメッセージ", inline=True)
                
                embed.set_footer(text=f"メッセージID: {message.id}")
                
                await log_channel.send(embed=embed)
                
                # 詳細JSONファイルも定期的に送信（1時間ごと）
                current_hour = datetime.now().hour
                if hasattr(log_config, 'last_json_hour') and log_config.get('last_json_hour') != current_hour:
                    filename = f"hourly_log_{message.guild.name}_{datetime.now().strftime('%Y%m%d_%H')}.json"
                    json_content = json.dumps([message_info], ensure_ascii=False, indent=2)
                    
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write(json_content)
                    
                    with open(filename, 'rb') as f:
                        await log_channel.send(
                            f"📊 時間別ログファイル",
                            file=discord.File(f, filename)
                        )
                    
                    os.remove(filename)
                    log_config['last_json_hour'] = current_hour
                
            except Exception as e:
                print(f"継続ログエラー: {e}")

@bot.tree.command(name='export', description='チャンネルのメッセージをログとして取得します')
@app_commands.describe(
    log_server_id='ログを送信するサーバーのID',
    channel_id='取得するチャンネルのID（省略時は現在のチャンネル）',
    limit='取得するメッセージ数（デフォルト: 100）'
)
async def export_log(interaction: discord.Interaction, log_server_id: str, channel_id: str = None, limit: int = 100):
    """
    指定されたチャンネルのメッセージを取得して別のサーバーに送信
    """
    await interaction.response.defer()
    
    # 権限チェック
    if not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("❌ このコマンドを実行するには管理者権限が必要です。")
        return
    
    try:
        # ログサーバーを取得
        log_server = bot.get_guild(int(log_server_id))
        if not log_server:
            await interaction.followup.send(f"❌ サーバーID {log_server_id} が見つかりません。Botがそのサーバーに参加していることを確認してください。")
            return
        
        # ログサーバーの最初のテキストチャンネルを取得
        log_channel = None
        for channel in log_server.text_channels:
            if channel.permissions_for(log_server.me).send_messages:
                log_channel = channel
                break
        
        if not log_channel:
            await interaction.followup.send(f"❌ ログサーバー {log_server.name} に書き込み可能なチャンネルが見つかりません。")
            return
        
        # 取得対象のチャンネルを決定
        if channel_id is None:
            target_channel = interaction.channel
        else:
            target_channel = bot.get_channel(int(channel_id))
            if not target_channel:
                await interaction.followup.send(f"❌ チャンネルID {channel_id} が見つかりません。")
                return
        
        await interaction.followup.send(f"📋 チャンネル {target_channel.name} からメッセージを取得中...")
        
        messages_data = []
        message_count = 0
        
        # メッセージ履歴を取得
        async for message in target_channel.history(limit=limit):
            # マスカレード（webhook）メッセージも含めて全てのメッセージを取得
            message_info = {
                'id': str(message.id),
                'channel': target_channel.name,
                'channel_id': str(target_channel.id),
                'guild': message.guild.name if message.guild else 'DM',
                'guild_id': str(message.guild.id) if message.guild else None,
                'author': str(message.author),
                'author_id': str(message.author.id),
                'display_name': message.author.display_name,
                'is_webhook': message.webhook_id is not None,
                'webhook_id': str(message.webhook_id) if message.webhook_id else None,
                'content': message.content,
                'timestamp': message.created_at.isoformat(),
                'edited_at': message.edited_at.isoformat() if message.edited_at else None,
                'attachments': [att.url for att in message.attachments],
                'embeds': len(message.embeds),
                'embed_details': [
                    {
                        'title': embed.title,
                        'description': embed.description,
                        'url': embed.url,
                        'color': embed.color.value if embed.color else None,
                        'timestamp': embed.timestamp.isoformat() if embed.timestamp else None,
                        'footer': {'text': embed.footer.text, 'icon_url': embed.footer.icon_url} if embed.footer else None,
                        'author': {'name': embed.author.name, 'url': embed.author.url, 'icon_url': embed.author.icon_url} if embed.author else None,
                        'thumbnail': embed.thumbnail.url if embed.thumbnail else None,
                        'image': embed.image.url if embed.image else None,
                        'fields': [{'name': field.name, 'value': field.value, 'inline': field.inline} for field in embed.fields]
                    } for embed in message.embeds
                ],
                'mentions': [str(user) for user in message.mentions],
                'reactions': [{'emoji': str(reaction.emoji), 'count': reaction.count} for reaction in message.reactions]
            }
            messages_data.append(message_info)
            message_count += 1
        
        if not messages_data:
            await interaction.followup.send("❌ 取得できるメッセージがありませんでした。")
            return
        
        # JSONファイルとして保存
        filename = f"messages_{target_channel.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        # JSONデータをファイルとして送信
        json_content = json.dumps(messages_data, ensure_ascii=False, indent=2)
        
        # ファイルサイズが大きい場合は分割
        if len(json_content.encode('utf-8')) > 8000000:  # 8MB制限
            # 複数のファイルに分割
            chunk_size = len(messages_data) // 3 + 1
            for i in range(0, len(messages_data), chunk_size):
                chunk = messages_data[i:i+chunk_size]
                chunk_filename = f"messages_{target_channel.name}_part{i//chunk_size + 1}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                chunk_content = json.dumps(chunk, ensure_ascii=False, indent=2)
                
                with open(chunk_filename, 'w', encoding='utf-8') as f:
                    f.write(chunk_content)
                
                with open(chunk_filename, 'rb') as f:
                    await log_channel.send(
                        f"📋 メッセージログ (Part {i//chunk_size + 1})\n"
                        f"元サーバー: {message.guild.name if message.guild else 'DM'}\n"
                        f"チャンネル: {target_channel.name}\n"
                        f"メッセージ数: {len(chunk)}\n"
                        f"取得者: {interaction.user}",
                        file=discord.File(f, chunk_filename)
                    )
                
                os.remove(chunk_filename)
        else:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(json_content)
            
            with open(filename, 'rb') as f:
                await log_channel.send(
                    f"📋 メッセージログエクスポート完了\n"
                    f"元サーバー: {message.guild.name if message.guild else 'DM'}\n"
                    f"チャンネル: {target_channel.name}\n"
                    f"取得メッセージ数: {message_count}\n"
                    f"マスカレードメッセージ含む: はい\n"
                    f"取得者: {interaction.user}",
                    file=discord.File(f, filename)
                )
            
            os.remove(filename)
        
        await interaction.followup.send(f"✅ {message_count}件のメッセージを {log_server.name} に送信しました。")
            
    except discord.Forbidden:
        await interaction.followup.send("❌ メッセージを読む権限がないか、ログサーバーに書き込み権限がありません。")
    except ValueError:
        await interaction.followup.send("❌ 無効なIDが指定されました。数値のIDを入力してください。")
    except Exception as e:
        await interaction.followup.send(f"❌ エラーが発生しました: {str(e)}")

@bot.tree.command(name='export_all', description='サーバー内の全チャンネルのメッセージを取得します')
@app_commands.describe(
    log_server_id='ログを送信するサーバーのID',
    limit='各チャンネルから取得するメッセージ数（デフォルト: 50）'
)
async def export_all_channels(interaction: discord.Interaction, log_server_id: str, limit: int = 50):
    """
    サーバー内の全てのテキストチャンネルからメッセージを取得
    """
    await interaction.response.defer()
    
    # 権限チェック
    if not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("❌ このコマンドを実行するには管理者権限が必要です。")
        return
    
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ このコマンドはサーバー内でのみ使用できます。")
        return
    
    try:
        # ログサーバーを取得
        log_server = bot.get_guild(int(log_server_id))
        if not log_server:
            await interaction.followup.send(f"❌ サーバーID {log_server_id} が見つかりません。")
            return
        
        # ログサーバーの最初のテキストチャンネルを取得
        log_channel = None
        for channel in log_server.text_channels:
            if channel.permissions_for(log_server.me).send_messages:
                log_channel = channel
                break
        
        if not log_channel:
            await interaction.followup.send(f"❌ ログサーバー {log_server.name} に書き込み可能なチャンネルが見つかりません。")
            return
        
        await interaction.followup.send(f"📋 サーバー {guild.name} の全チャンネルからメッセージを取得中...")
        
        processed_channels = 0
        total_messages = 0
        
        for channel in guild.text_channels:
            try:
                if channel.permissions_for(guild.me).read_message_history:
                    messages_data = []
                    message_count = 0
                    
                    async for message in channel.history(limit=limit):
                        message_info = {
                            'id': str(message.id),
                            'channel': channel.name,
                            'channel_id': str(channel.id),
                            'guild': guild.name,
                            'guild_id': str(guild.id),
                            'author': str(message.author),
                            'author_id': str(message.author.id),
                            'display_name': message.author.display_name,
                            'is_webhook': message.webhook_id is not None,
                            'webhook_id': str(message.webhook_id) if message.webhook_id else None,
                            'content': message.content,
                            'timestamp': message.created_at.isoformat(),
                            'edited_at': message.edited_at.isoformat() if message.edited_at else None,
                            'attachments': [att.url for att in message.attachments],
                            'embeds': len(message.embeds),
                            'embed_details': [
                                {
                                    'title': embed.title,
                                    'description': embed.description,
                                    'url': embed.url,
                                    'color': embed.color.value if embed.color else None,
                                    'timestamp': embed.timestamp.isoformat() if embed.timestamp else None,
                                    'footer': {'text': embed.footer.text, 'icon_url': embed.footer.icon_url} if embed.footer else None,
                                    'author': {'name': embed.author.name, 'url': embed.author.url, 'icon_url': embed.author.icon_url} if embed.author else None,
                                    'thumbnail': embed.thumbnail.url if embed.thumbnail else None,
                                    'image': embed.image.url if embed.image else None,
                                    'fields': [{'name': field.name, 'value': field.value, 'inline': field.inline} for field in embed.fields]
                                } for embed in message.embeds
                            ],
                            'mentions': [str(user) for user in message.mentions],
                            'reactions': [{'emoji': str(reaction.emoji), 'count': reaction.count} for reaction in message.reactions]
                        }
                        messages_data.append(message_info)
                        message_count += 1
                    
                    if messages_data:
                        filename = f"messages_{guild.name}_{channel.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        json_content = json.dumps(messages_data, ensure_ascii=False, indent=2)
                        
                        with open(filename, 'w', encoding='utf-8') as f:
                            f.write(json_content)
                        
                        with open(filename, 'rb') as f:
                            await log_channel.send(
                                f"📋 チャンネルログ: #{channel.name}\n"
                                f"サーバー: {guild.name}\n"
                                f"メッセージ数: {message_count}\n"
                                f"取得者: {interaction.user}",
                                file=discord.File(f, filename)
                            )
                        
                        os.remove(filename)
                        processed_channels += 1
                        total_messages += message_count
                    
                    # レート制限を避けるため少し待機
                    await asyncio.sleep(1)
                    
            except discord.Forbidden:
                continue
            except Exception as e:
                print(f"チャンネル {channel.name} でエラー: {e}")
                continue
        
        await interaction.followup.send(f"✅ 完了: {processed_channels}個のチャンネルから{total_messages}件のメッセージを {log_server.name} に送信しました。")
        
    except ValueError:
        await interaction.followup.send("❌ 無効なサーバーIDが指定されました。")
    except Exception as e:
        await interaction.followup.send(f"❌ エラーが発生しました: {str(e)}")

@bot.tree.command(name='start_logging', description='指定したサーバーで継続的にログを記録開始')
@app_commands.describe(
    log_server_id='ログを送信するサーバーのID',
    channels='監視するチャンネルIDのリスト（カンマ区切り、省略時は全チャンネル）'
)
async def start_continuous_logging(interaction: discord.Interaction, log_server_id: str, channels: str = None):
    """
    継続的なログ記録を開始
    """
    await interaction.response.defer()
    
    # 権限チェック
    if not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("❌ このコマンドを実行するには管理者権限が必要です。")
        return
    
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ このコマンドはサーバー内でのみ使用できます。")
        return
    
    try:
        # ログサーバーを取得
        log_server = bot.get_guild(int(log_server_id))
        if not log_server:
            await interaction.followup.send(f"❌ サーバーID {log_server_id} が見つかりません。")
            return
        
        # ログサーバーの書き込み可能なチャンネルを取得
        log_channel = None
        for channel in log_server.text_channels:
            if channel.permissions_for(log_server.me).send_messages:
                log_channel = channel
                break
        
        if not log_channel:
            await interaction.followup.send(f"❌ ログサーバー {log_server.name} に書き込み可能なチャンネルが見つかりません。")
            return
        
        # 監視チャンネルの設定
        target_channels = []
        if channels:
            channel_ids = [ch.strip() for ch in channels.split(',')]
            for ch_id in channel_ids:
                try:
                    channel = bot.get_channel(int(ch_id))
                    if channel and channel.guild == guild:
                        target_channels.append(ch_id)
                except ValueError:
                    continue
        
        # 継続ログ設定を保存
        continuous_logging[str(guild.id)] = {
            'log_server_id': log_server_id,
            'channels': target_channels,  # 空の場合は全チャンネル
            'log_channel': log_channel,
            'last_json_hour': datetime.now().hour
        }
        
        # 開始通知をログサーバーに送信
        start_embed = discord.Embed(
            title="🟢 継続ログ記録開始",
            color=0x00ff00,
            timestamp=datetime.now(timezone.utc)
        )
        start_embed.add_field(name="対象サーバー", value=guild.name, inline=True)
        start_embed.add_field(name="サーバーID", value=str(guild.id), inline=True)
        start_embed.add_field(name="開始者", value=str(interaction.user), inline=True)
        
        if target_channels:
            channel_names = []
            for ch_id in target_channels:
                channel = bot.get_channel(int(ch_id))
                if channel:
                    channel_names.append(f"#{channel.name}")
            start_embed.add_field(name="監視チャンネル", value="\n".join(channel_names), inline=False)
        else:
            start_embed.add_field(name="監視範囲", value="全テキストチャンネル", inline=False)
        
        await log_channel.send(embed=start_embed)
        
        if target_channels:
            await interaction.followup.send(f"✅ {len(target_channels)}個のチャンネルで継続ログ記録を開始しました。ログは {log_server.name} に送信されます。")
        else:
            await interaction.followup.send(f"✅ 全チャンネルで継続ログ記録を開始しました。ログは {log_server.name} に送信されます。")
        
    except ValueError:
        await interaction.followup.send("❌ 無効なIDが指定されました。")
    except Exception as e:
        await interaction.followup.send(f"❌ エラーが発生しました: {str(e)}")

@bot.tree.command(name='stop_logging', description='継続的なログ記録を停止')
async def stop_continuous_logging(interaction: discord.Interaction):
    """
    継続的なログ記録を停止
    """
    await interaction.response.defer()
    
    # 権限チェック
    if not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("❌ このコマンドを実行するには管理者権限が必要です。")
        return
    
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ このコマンドはサーバー内でのみ使用できます。")
        return
    
    guild_id = str(guild.id)
    
    if guild_id in continuous_logging:
        log_config = continuous_logging[guild_id]
        log_channel = log_config['log_channel']
        
        # 停止通知をログサーバーに送信
        stop_embed = discord.Embed(
            title="🔴 継続ログ記録停止",
            color=0xff0000,
            timestamp=datetime.now(timezone.utc)
        )
        stop_embed.add_field(name="対象サーバー", value=guild.name, inline=True)
        stop_embed.add_field(name="サーバーID", value=guild_id, inline=True)
        stop_embed.add_field(name="停止者", value=str(interaction.user), inline=True)
        
        try:
            await log_channel.send(embed=stop_embed)
        except:
            pass  # ログサーバーに送信できなくても継続
        
        # 設定を削除
        del continuous_logging[guild_id]
        await interaction.followup.send("✅ 継続ログ記録を停止しました。")
    else:
        await interaction.followup.send("❌ このサーバーでは継続ログ記録が開始されていません。")

@bot.tree.command(name='logging_status', description='継続ログ記録の状態を確認')
async def logging_status(interaction: discord.Interaction):
    """
    継続ログ記録の状態を表示
    """
    await interaction.response.defer()
    
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ このコマンドはサーバー内でのみ使用できます。")
        return
    
    guild_id = str(guild.id)
    
    if guild_id in continuous_logging:
        log_config = continuous_logging[guild_id]
        log_server = bot.get_guild(int(log_config['log_server_id']))
        
        status_embed = discord.Embed(
            title="📊 継続ログ記録状態",
            color=0x00ff00,
            timestamp=datetime.now(timezone.utc)
        )
        status_embed.add_field(name="状態", value="🟢 記録中", inline=True)
        status_embed.add_field(name="ログ送信先", value=log_server.name if log_server else "不明", inline=True)
        status_embed.add_field(name="ログチャンネル", value=f"#{log_config['log_channel'].name}", inline=True)
        
        if log_config['channels']:
            channel_names = []
            for ch_id in log_config['channels']:
                channel = bot.get_channel(int(ch_id))
                if channel:
                    channel_names.append(f"#{channel.name}")
            status_embed.add_field(name="監視チャンネル", value="\n".join(channel_names), inline=False)
        else:
            status_embed.add_field(name="監視範囲", value="全テキストチャンネル", inline=False)
        
        await interaction.followup.send(embed=status_embed)
    else:
        status_embed = discord.Embed(
            title="📊 継続ログ記録状態",
            color=0xff0000,
            timestamp=datetime.now(timezone.utc)
        )
        status_embed.add_field(name="状態", value="🔴 停止中", inline=True)
        await interaction.followup.send(embed=status_embed)

def run_flask():
    """Flaskサーバーを別スレッドで起動"""
    app.run(host='0.0.0.0', port=5000, debug=False)

# Botを起動
if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("DISCORD_BOT_TOKEN環境変数が設定されていません。")
        print("Secretsタブでトークンを設定してください。")
        # トークンがない場合でもFlaskサーバーだけ起動
        app.run(host='0.0.0.0', port=5000, debug=False)
    else:
        # Flaskサーバーを別スレッドで起動
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print("Flask web server started on http://0.0.0.0:5000")
        
        # Discord botを起動
        bot.run(TOKEN)
