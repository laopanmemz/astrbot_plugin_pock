import aiohttp
import astrbot.api.event.filter as filter
from scrapy import Selector
from astrbot.api.all import *
import random
import requests
import os
import time
import shutil
import yaml
import logging
import asyncio

logger = logging.getLogger(__name__)

@register("poke_monitor", "长安某", "监控戳一戳事件插件", "1.7.0")
class PokeMonitorPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.global_poke_timestamps = []  # 全局戳/拍时间戳
        self.cooldown_end_time = 0  # 全局冷却结束时间
        self.emoji_last_used_time = 0  # 表情包最后使用时间戳
        self.emoji_lock = asyncio.Lock()  # 表情包生成锁，防止并发问题
        self.config = self._load_config()
        self._clean_legacy_directories()
        self._clean_emoji_directory()

    def _load_config(self):
        """加载或创建配置文件"""
        config_dir = os.path.join("data", "plugins", "astrbot_plugin_pock")
        config_path = os.path.join(config_dir, "config.yml")
        
        # 创建默认配置
        if not os.path.exists(config_path):
            default_config = {
                "poke_responses": ["别戳啦！", "哎呀，还戳呀，别闹啦！", "别戳我啦  你要做什么  不理你了"],
                "emoji_url_mapping": {
                    "阿罗娜扔": "13", "咖波画": "33", "咖波指": "34", "咖波蹭": "36",
                    "丢": "38", "撕": "56", "爬": "69", "顶": "102",
                    "拍": "184", "摸": "187", "捏": "188", "普拉娜吃": "191", "捣": "199",
                },
                "random_emoji_trigger_probability": 0.5,
                "post_timeout": 20,
                "emoji_cooldown_seconds": 20,  # 表情包生成冷却时间(秒)
                "feature_switches": {
                    "poke_response_enabled": True,
                    "poke_back_enabled": True,
                    "emoji_trigger_enabled": True
                },
                "poke_back_probability": 0.3,
                "super_poke_probability": 0.1
            }
            os.makedirs(config_dir, exist_ok=True)
            try:
                with open(config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(default_config, f, allow_unicode=True, default_flow_style=False)
            except Exception as e:
                logger.error(f"配置文件创建失败: {str(e)}")
            return default_config
        
        # 加载现有配置
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"配置文件加载失败: {str(e)}")
            return {}

    def _clean_legacy_directories(self):
        """安全清理旧目录"""
        legacy_dirs = [
            os.path.abspath("./data/plugins/poke_monitor"),
            os.path.abspath("./data/plugins/plugins/poke_monitor")
        ]
        for path in legacy_dirs:
            try:
                if os.path.exists(path):
                    shutil.rmtree(path)
            except Exception as e:
                logger.error(f"旧目录清理失败: {str(e)}")

    def _clean_emoji_directory(self):
        """清理表情包目录"""
        save_dir = os.path.join("data", "plugins", "astrbot_plugin_pock", "poke_monitor")
        if os.path.exists(save_dir):
            for filename in os.listdir(save_dir):
                file_path = os.path.join(save_dir, filename)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                except Exception as e:
                    logger.error(f"表情包文件清理失败: {str(e)}")

    def _record_global_poke(self):
        """记录全局戳/拍行为，并清理旧记录"""
        now = time.time()
        two_minutes_ago = now - 2 * 60
        
        # 清理2分钟前的记录
        self.global_poke_timestamps = [
            t for t in self.global_poke_timestamps if t > two_minutes_ago
        ]
        
        # 添加新记录
        self.global_poke_timestamps.append(now)
        
        return len(self.global_poke_timestamps)

    def _get_response_message(self, poke_count):
        """根据戳/拍次数获取对应的回复消息"""
        responses = self.config.get('poke_responses', [])
        if poke_count <= len(responses):
            return responses[poke_count - 1]
        return responses[-1] if responses else "不理你们了"

    def _should_reply_text(self):
        """判断是否应该发送文字回复"""
        now = time.time()
        return now >= self.cooldown_end_time

    def _set_cooldown(self):
        """设置全局冷却时间（5分钟）"""
        self.cooldown_end_time = time.time() + 5 * 60

    async def _handle_poke_back(self, event, sender_id, platform):
        """处理戳/拍回逻辑"""
        if not self.config.get('feature_switches', {}).get('poke_back_enabled', True):
            return
        
        if random.random() < self.config['poke_back_probability']:
            is_super = random.random() < self.config['super_poke_probability']
            poke_times = 5 if is_super else 1
            
            if is_super:
                yield event.plain_result("喜欢" + ("戳" if platform == "aiocqhttp" else "拍") + "是吧")
            else:
                yield event.plain_result("" + ("戳" if platform == "aiocqhttp" else "拍") + "回去")

            # 平台特定的戳/拍回实现
            if platform == "aiocqhttp":
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
                client = event.bot
                
                # 从message_obj获取原始消息数据
                raw_message = event.message_obj.raw_message
                
                # 尝试群聊戳一戳
                group_id = raw_message.get('group_id')
                if group_id:
                    payloads = {"user_id": sender_id, "group_id": group_id}
                    for _ in range(poke_times):
                        try:
                            await client.api.call_action('send_poke', **payloads)
                        except Exception as e:
                            logger.error(f"QQ群戳回失败: {str(e)}")
                            # 尝试私聊戳一戳作为备选
                            try:
                                private_payloads = {"user_id": sender_id}
                                await client.api.call_action('send_poke', **private_payloads)
                            except Exception as e2:
                                logger.error(f"QQ私聊戳回失败: {str(e2)}")
                else:
                    # 私聊场景
                    try:
                        payloads = {"user_id": sender_id}
                        for _ in range(poke_times):
                            await client.api.call_action('send_poke', **payloads)
                    except Exception as e:
                        logger.error(f"QQ私聊戳回失败: {str(e)}")
            
            elif platform == "wechatpadpro":
                from astrbot.core.platform.sources.wechatpadpro.wechatpadpro_message_event import WeChatPadProMessageEvent
                assert isinstance(event, WeChatPadProMessageEvent)
                
                # 从message_obj获取原始消息数据
                raw_message = event.message_obj.raw_message
                
                # 提取群聊名称
                content_str = raw_message["content"]["str"]
                try:
                    chatroom_name = content_str.split(":", 1)[0].strip()
                except (IndexError, AttributeError):
                    chatroom_name = None
                
                # 尝试群聊拍一拍
                if chatroom_name:
                    headers = {"accept": "application/json", "Content-Type": "application/json"}
                    payloads = {
                        "ChatRoomName": chatroom_name,
                        "Scene": 0,
                        "ToUserName": sender_id
                    }
                    params = {"key": event.adapter.auth_key}
                    wxapi_url = event.adapter.base_url + "/group/SendPat"
                    
                    for _ in range(poke_times):
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.post(wxapi_url, headers=headers, json=payloads, params=params) as resp:
                                    if resp.status != 200:
                                        logger.error(f"微信拍回失败，状态码: {resp.status}")
                        except Exception as e:
                            logger.error(f"微信拍回失败: {str(e)}")
                else:
                    logger.error("微信拍回失败: 无法提取群聊名称")

    async def _handle_emoji(self, event, target_id, platform, chatusername=None):
        """处理随机触发表情包，优化冷却时间记录逻辑"""
        if not self.config.get('feature_switches', {}).get('emoji_trigger_enabled', True):
            return
            
        # 使用锁防止并发问题
        async with self.emoji_lock:
            # 冷却时间检查
            now = time.time()
            cooldown_seconds = self.config.get('emoji_cooldown_seconds', 20)
            if now - self.emoji_last_used_time < cooldown_seconds:
                logger.debug(f"表情包冷却中，剩余时间: {cooldown_seconds - (now - self.emoji_last_used_time):.2f}秒")
                return
                
            # 概率触发检查
            if random.random() >= self.config['random_emoji_trigger_probability']:
                return
                
            available_actions = list(self.config.get('emoji_url_mapping', {}).keys())
            if not available_actions:
                return
                
            # 关键优化：在条件通过后立即记录冷却时间，而非等待API响应
            self.emoji_last_used_time = time.time()
            
            # 选择表情包动作
            selected_action = random.choice(available_actions)
            emoji_type = self.config['emoji_url_mapping'][selected_action]
            url = "https://api.lolimi.cn/API/preview/api.php"
            
            # 根据平台获取头像URL
            if platform == "wechatpadpro" and chatusername:
                try:
                    headers = {"accept": "application/json", "Content-Type": "application/json"}
                    payloads = {"ChatRoomName": chatusername}
                    params = {"key": event.adapter.auth_key}
                    wxapi_url = event.adapter.base_url + "/group/GetChatroomMemberDetail"
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.post(wxapi_url, headers=headers, json=payloads, params=params) as resp:
                            response = await resp.json()
                    
                    members_list = response["Data"]["member_data"]["chatroom_member_list"]
                    big_head_img_url = next(
                        (member["big_head_img_url"] for member in members_list if member["user_name"] == target_id), None
                    )
                    
                    if not big_head_img_url:
                        return
                            
                    params = {'qq': big_head_img_url, "i": "2", "action": "create_meme", "type": emoji_type}
                except Exception as e:
                    logger.error(f"微信头像获取失败: {str(e)}")
                    return
            else:
                params = {'qq': target_id, "action": "create_meme", "type": emoji_type}
                
            # 发送请求生成表情包
            timeout = self.config.get('post_timeout', 20)
            try:
                response = requests.get(url, params=params, timeout=timeout)
                if response.status_code == 200:
                    save_dir = os.path.join("data", "plugins", "astrbot_plugin_pock", "poke_monitor")
                    os.makedirs(save_dir, exist_ok=True)
                        
                    filename = f"{selected_action}_{target_id}_{int(time.time())}.gif"
                    image_path = os.path.join(save_dir, filename)
                        
                    with open(image_path, "wb") as f:
                        f.write(response.content)
                    yield event.image_result(image_path)
                        
                    # 清理临时文件
                    if os.path.exists(image_path):
                        try:
                            os.remove(image_path)
                        except Exception as e:
                            logger.error(f"表情包文件清理失败: {str(e)}")
            except Exception as e:
                logger.error(f"表情包请求失败: {str(e)}")

    @event_message_type(filter.EventMessageType.ALL)
    async def on_group_message(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        raw_message = message_obj.raw_message
        
        # 判断 aiocqhttp 戳一戳事件
        if raw_message.get('post_type') == 'notice' and \
                raw_message.get('notice_type') == 'notify' and \
                raw_message.get('sub_type') == 'poke':
            bot_id = raw_message.get('self_id')
            sender_id = raw_message.get('user_id')
            target_id = raw_message.get('target_id')
            
            if bot_id and sender_id and target_id:
                # 用户戳机器人
                if str(target_id) == str(bot_id):
                    # 记录全局戳一戳
                    poke_count = self._record_global_poke()
                    
                    # 处理文字回复
                    if self.config.get('feature_switches', {}).get('poke_response_enabled', True):
                        if self._should_reply_text():
                            # 前两次按配置回复，第三次触发冷却
                            if poke_count < 3:
                                response = self._get_response_message(poke_count)
                                yield event.plain_result(response)
                            else:
                                yield event.plain_result("不理你们了")
                                self._set_cooldown()
                    
                    # 处理戳回
                    async for result in self._handle_poke_back(event, sender_id, "aiocqhttp"):
                        yield result
                
                # 用户戳其他人
                elif str(sender_id) != str(bot_id):
                    # 处理表情包
                    async for result in self._handle_emoji(event, target_id, "aiocqhttp"):
                        yield result
        
        # 判断 WechatPadPro 拍一拍事件
        elif raw_message.get("to_user_name") and raw_message.get("msg_type") == 10002:
            is_private = False
            content_str = raw_message["content"]["str"]
            
            try:
                xml_content = Selector(text=content_str.split(":", 1)[1].strip())
            except IndexError:
                xml_content = content_str.strip()
                is_private = True
                
            if not isinstance(xml_content, Selector):
                xml_content = Selector(text=xml_content, type="html")
                
            bot_id = event.get_self_id()
            sender_id = xml_content.xpath("//pat/fromusername//text()").get()
            chatusername = xml_content.xpath("//pat/chatusername//text()").get()
            target_id = xml_content.xpath("//pat/pattedusername//text()").get()
            
            if bot_id and sender_id and target_id:
                # 用户拍机器人
                if str(target_id) == str(bot_id):
                    # 记录全局拍一拍
                    poke_count = self._record_global_poke()
                    
                    # 处理文字回复
                    if self.config.get('feature_switches', {}).get('poke_response_enabled', True):
                        if self._should_reply_text():
                            if poke_count < 3:
                                response = self._get_response_message(poke_count)
                                yield event.plain_result(response)
                            else:
                                yield event.plain_result("不理你们了")
                                self._set_cooldown()
                    
                    # 处理拍回
                    async for result in self._handle_poke_back(event, sender_id, "wechatpadpro"):
                        yield result
                
                # 用户拍其他人
                elif str(sender_id) != str(bot_id):
                    if is_private:
                        event.stop_event()
                        return
                    
                    # 处理表情包
                    async for result in self._handle_emoji(event, target_id, "wechatpadpro", chatusername):
                        yield result
