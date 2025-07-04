import asyncio
import json
import logging
import os
import random
import shutil
import time

import aiofiles
import aiohttp
import astrbot.api.event.filter as filter
import yaml
from astrbot.api.all import *
from scrapy import Selector

logger = logging.getLogger(__name__)


@register("poke_monitor", "长安某", "监控戳一戳事件插件", "1.8.0")
class PokeMonitorPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.global_poke_timestamps = []  # 全局戳/拍时间戳
        # self.cooldown_end_time = 0  # 全局冷却结束时间（已废弃）
        self.group_cooldown_end_time = {}  # 群聊冷却结束时间
        self.emoji_last_used_time = 0  # 表情包最后使用时间戳
        self.emoji_lock = asyncio.Lock()  # 表情包生成锁，防止并发问题
        self.llm_lock = asyncio.Lock()  # LLM调用锁，防止并发问题
        self.config = self._load_config()
        self._clean_legacy_directories()
        self._clean_emoji_directory()

        # 新增LLM相关管理器
        self.func_tools_mgr = context.get_llm_tool_manager()
        self.conversation_manager = context.conversation_manager

    def _load_config(self):
        """加载或创建配置文件"""
        config_dir = os.path.join("data", "plugins", "astrbot_plugin_pock")
        config_path = os.path.join(config_dir, "config.yml")

        # 创建默认配置
        if not os.path.exists(config_path):
            default_config = {
                "poke_responses": [
                    "别戳啦！",
                    "哎呀，还戳呀，别闹啦！",
                    "别戳我啦  你要做什么  不理你了",
                ],
                "emoji_url_mapping": {
                    "阿罗娜扔": "13",
                    "咖波画": "33",
                    "咖波指": "34",
                    "咖波蹭": "36",
                    "丢": "38",
                    "撕": "56",
                    "爬": "69",
                    "顶": "102",
                    "拍": "184",
                    "摸": "187",
                    "捏": "188",
                    "普拉娜吃": "191",
                    "捣": "199",
                },
                "random_emoji_trigger_probability": 0.5,
                "post_timeout": 20,
                "emoji_cooldown_seconds": 20,  # 表情包生成冷却时间(秒)
                "feature_switches": {
                    "poke_response_enabled": True,
                    "poke_back_enabled": True,
                    "emoji_trigger_enabled": True,
                },
                "poke_back_probability": 0.3,
                "super_poke_probability": 0.1,
            }
            os.makedirs(config_dir, exist_ok=True)
            try:
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(
                        default_config, f, allow_unicode=True, default_flow_style=False
                    )
            except Exception as e:
                logger.error(f"配置文件创建失败: {str(e)}")
            return default_config

        # 加载现有配置
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"配置文件加载失败: {str(e)}")
            return {}

    def _clean_legacy_directories(self):
        """安全清理旧目录"""
        legacy_dirs = [
            os.path.abspath("./data/plugins/poke_monitor"),
            os.path.abspath("./data/plugins/plugins/poke_monitor"),
        ]
        for path in legacy_dirs:
            try:
                if os.path.exists(path):
                    shutil.rmtree(path)
            except Exception as e:
                logger.error(f"旧目录清理失败: {str(e)}")

    def _clean_emoji_directory(self):
        """清理表情包目录"""
        save_dir = os.path.join(
            "data", "plugins", "astrbot_plugin_pock", "poke_monitor"
        )
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

    async def _get_qq_nickname(self, qq_id):
        """通过API获取QQ用户昵称"""
        url = f"http://api.mmp.cc/api/qqname?qq={qq_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("code") == 200 and data.get("data"):
                            return data["data"].get("name", f"QQ用户{qq_id}")
            return f"QQ用户{qq_id}"
        except Exception as e:
            logger.error(f"获取QQ昵称失败: {str(e)}")
            return f"QQ用户{qq_id}"

    async def _get_llm_response(self, poke_count, event, user_nickname=""):
        """通过LLM生成动态回复，使用底层API获取结果"""
        curr_cid = await self.conversation_manager.get_curr_conversation_id(
            event.unified_msg_origin
        )
        conversation = None
        context = []

        if curr_cid:
            conversation = await self.conversation_manager.get_conversation(
                event.unified_msg_origin, curr_cid
            )
            if conversation and conversation.history:
                context = json.loads(conversation.history)

        # 根据戳的次数设置不同提示词
        prompt_map = {
            1: f"用户{user_nickname}突然戳了你一下，回复要略带无奈，请求不要打扰：",
            2: f"用户{user_nickname}戳了你一下，这是你第二次被戳，回复要带点撒娇和警告，带点互动感：",
            3: f"用户{user_nickname}戳了你一下，已经你第三次被戳，回复要表示无奈和生气，并且表示不再回复，根据情景自己考虑躲起来或者生闷气等等：",
        }
        prompt_prefix = prompt_map.get(
            poke_count, f"用户{user_nickname}又戳你了，回复要俏皮、有趣："
        )

        # 使用方法1的底层LLM调用方式
        provider = self.context.get_using_provider()
        try:
            llm_response = await provider.text_chat(
                prompt=prompt_prefix,
                contexts=context,
                func_tool=self.func_tools_mgr,
                system_prompt="用户戳你时要回复俏皮、有趣的内容，每次回复风格要略有变化，避免重复。",
            )

            if llm_response.role == "assistant":
                return llm_response.completion_text.strip()
            else:
                logger.warning(f"LLM返回非预期角色: {llm_response.role}")
                return "呜哇，被戳到啦！"  # 默认回复
        except Exception as e:
            logger.error(f"LLM调用失败: {str(e)}")
            return "哎呀，我有点懵，等下再戳我吧~"  # 错误处理

    def _should_reply_text(self, group_id):
        """判断该群是否应该发送文字回复"""
        now = time.time()
        return now >= self.group_cooldown_end_time.get(group_id, 0)

    def _set_cooldown(self, group_id):
        """设置该群冷却时间（5分钟）"""
        self.group_cooldown_end_time[group_id] = time.time() + 5 * 60

    async def _handle_poke_back(self, event, sender_id, platform):
        """处理戳/拍回逻辑"""
        if not self.config.get("feature_switches", {}).get("poke_back_enabled", True):
            return

        if random.random() < self.config["poke_back_probability"]:
            is_super = random.random() < self.config["super_poke_probability"]
            poke_times = 5 if is_super else 1

            if is_super:
                yield event.plain_result(
                    "喜欢" + ("戳" if platform == "aiocqhttp" else "拍") + "是吧"
                )
            else:
                yield event.plain_result(
                    "" + ("戳" if platform == "aiocqhttp" else "拍") + "回去"
                )

            # 平台特定的戳/拍回实现
            if platform == "aiocqhttp":
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                assert isinstance(event, AiocqhttpMessageEvent)
                client = event.bot

                # 从message_obj获取原始消息数据
                raw_message = event.message_obj.raw_message

                # 尝试群聊戳一戳
                group_id = raw_message.get("group_id")
                if group_id:
                    payloads = {"user_id": sender_id, "group_id": group_id}
                    for _ in range(poke_times):
                        try:
                            await client.api.call_action("send_poke", **payloads)
                        except Exception as e:
                            logger.error(f"QQ群戳回失败: {str(e)}")
                            # 尝试私聊戳一戳作为备选
                            try:
                                private_payloads = {"user_id": sender_id}
                                await client.api.call_action(
                                    "send_poke", **private_payloads
                                )
                            except Exception as e2:
                                logger.error(f"QQ私聊戳回失败: {str(e2)}")
                else:
                    # 私聊场景
                    try:
                        payloads = {"user_id": sender_id}
                        for _ in range(poke_times):
                            await client.api.call_action("send_poke", **payloads)
                    except Exception as e:
                        logger.error(f"QQ私聊戳回失败: {str(e)}")

            elif platform == "wechatpadpro":
                from astrbot.core.platform.sources.wechatpadpro.wechatpadpro_message_event import (
                    WeChatPadProMessageEvent,
                )

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
                    headers = {
                        "accept": "application/json",
                        "Content-Type": "application/json",
                    }
                    payloads = {
                        "ChatRoomName": chatroom_name,
                        "Scene": 0,
                        "ToUserName": sender_id,
                    }
                    params = {"key": event.adapter.auth_key}
                    wxapi_url = event.adapter.base_url + "/group/SendPat"

                    for _ in range(poke_times):
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.post(
                                    wxapi_url,
                                    headers=headers,
                                    json=payloads,
                                    params=params,
                                ) as resp:
                                    if resp.status != 200:
                                        logger.error(
                                            f"微信拍回失败，状态码: {resp.status}"
                                        )
                        except Exception as e:
                            logger.error(f"微信拍回失败: {str(e)}")
                else:
                    logger.error("微信拍回失败: 无法提取群聊名称")

    async def _handle_emoji(self, event, target_id, platform, chatusername=None):
        """处理随机触发表情包，优化冷却时间记录逻辑（全异步版）"""
        if not self.config.get("feature_switches", {}).get(
            "emoji_trigger_enabled", True
        ):
            return

        async with self.emoji_lock:
            now = time.time()
            cooldown_seconds = self.config.get("emoji_cooldown_seconds", 20)
            if now - self.emoji_last_used_time < cooldown_seconds:
                logger.debug(
                    f"表情包冷却中，剩余时间: {cooldown_seconds - (now - self.emoji_last_used_time):.2f}秒"
                )
                return

            if random.random() >= self.config["random_emoji_trigger_probability"]:
                return

            available_actions = list(self.config.get("emoji_url_mapping", {}).keys())
            if not available_actions:
                return

            self.emoji_last_used_time = time.time()

            selected_action = random.choice(available_actions)
            emoji_type = self.config["emoji_url_mapping"][selected_action]
            url = "https://api.lolimi.cn/API/preview/api.php"

            if platform == "wechatpadpro" and chatusername:
                try:
                    headers = {
                        "accept": "application/json",
                        "Content-Type": "application/json",
                    }
                    payloads = {"ChatRoomName": chatusername}
                    params = {"key": event.adapter.auth_key}
                    wxapi_url = (
                        event.adapter.base_url + "/group/GetChatroomMemberDetail"
                    )

                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            wxapi_url, headers=headers, json=payloads, params=params
                        ) as resp:
                            response = await resp.json()

                    members_list = response["Data"]["member_data"][
                        "chatroom_member_list"
                    ]
                    big_head_img_url = next(
                        (
                            member["big_head_img_url"]
                            for member in members_list
                            if member["user_name"] == target_id
                        ),
                        None,
                    )

                    if not big_head_img_url:
                        return

                    params = {
                        "qq": big_head_img_url,
                        "i": "2",
                        "action": "create_meme",
                        "type": emoji_type,
                    }
                except Exception as e:
                    logger.error(f"微信头像获取失败: {str(e)}")
                    return
            else:
                params = {"qq": target_id, "action": "create_meme", "type": emoji_type}

            timeout = self.config.get("post_timeout", 20)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, params=params, timeout=timeout
                    ) as response:
                        if response.status == 200:
                            content = await response.read()
                            save_dir = os.path.join(
                                "data", "plugins", "astrbot_plugin_pock", "poke_monitor"
                            )
                            os.makedirs(save_dir, exist_ok=True)

                            filename = (
                                f"{selected_action}_{target_id}_{int(time.time())}.gif"
                            )
                            image_path = os.path.join(save_dir, filename)

                            async with aiofiles.open(image_path, "wb") as f:
                                await f.write(content)
                            yield event.image_result(image_path)

                            # 清理临时文件
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
        if (
            raw_message.get("post_type") == "notice"
            and raw_message.get("notice_type") == "notify"
            and raw_message.get("sub_type") == "poke"
        ):
            bot_id = raw_message.get("self_id")
            sender_id = raw_message.get("user_id")
            target_id = raw_message.get("target_id")
            group_id = (
                raw_message.get("group_id") or "private"
            )  # 关键：获取群号或用"private"

            if bot_id and sender_id and target_id:
                # 用户戳机器人
                if str(target_id) == str(bot_id):
                    # 获取用户昵称
                    user_nickname = await self._get_qq_nickname(sender_id)

                    # 使用锁防止并发问题
                    async with self.llm_lock:
                        # 记录全局戳一戳
                        poke_count = self._record_global_poke()

                        # 关键修改：在LLM调用前设置群冷却
                        if poke_count > 3:
                            self._set_cooldown(group_id)

                        # 处理文字回复
                        if self.config.get("feature_switches", {}).get(
                            "poke_response_enabled", True
                        ):
                            if self._should_reply_text(group_id):
                                # 调用LLM获取动态回复，传递用户名
                                response = await self._get_llm_response(
                                    poke_count, event, user_nickname
                                )
                                yield event.plain_result(response)

                    # 处理戳回（无需等待LLM响应）
                    async for result in self._handle_poke_back(
                        event, sender_id, "aiocqhttp"
                    ):
                        yield result

                # 用户戳其他人
                elif str(sender_id) != str(bot_id):
                    # 处理表情包
                    async for result in self._handle_emoji(
                        event, target_id, "aiocqhttp"
                    ):
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
            group_id = (
                chatusername or raw_message.get("to_user_name") or "private"
            )  # 微信群聊唯一标识

            if bot_id and sender_id and target_id:
                # 用户拍机器人
                if str(target_id) == str(bot_id):
                    # 使用锁防止并发问题
                    async with self.llm_lock:
                        # 记录全局拍一拍
                        poke_count = self._record_global_poke()

                        # 关键修改：在LLM调用前设置群冷却
                        if poke_count > 3:
                            self._set_cooldown(group_id)

                        # 处理文字回复
                        if self.config.get("feature_switches", {}).get(
                            "poke_response_enabled", True
                        ):
                            if self._should_reply_text(group_id):
                                # 微信暂时不支持获取用户名，使用原始方式
                                response = await self._get_llm_response(
                                    poke_count, event
                                )
                                yield event.plain_result(response)

                    # 处理拍回
                    async for result in self._handle_poke_back(
                        event, sender_id, "wechatpadpro"
                    ):
                        yield result

                # 用户拍其他人
                elif str(sender_id) != str(bot_id):
                    if is_private:
                        event.stop_event()
                        return

                    # 处理表情包
                    async for result in self._handle_emoji(
                        event, target_id, "wechatpadpro", chatusername
                    ):
                        yield result
