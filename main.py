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

@register("poke_monitor", "长安某", "监控戳一戳事件插件", "1.5.0")
class PokeMonitorPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.user_poke_timestamps = {}
        # 构建配置文件路径
        config_dir = os.path.join("data", "plugins", "astrbot_plugin_pock")
        config_path = os.path.join(config_dir, "config.yml")

        # 检查配置文件是否存在，若不存在则创建并写入默认值
        if not os.path.exists(config_path):
            self._create_default_config(config_path)

        try:
            # 读取配置文件
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
            self.poke_responses = self.config.get('poke_responses', [])
            self.emoji_url_mapping = self.config.get('emoji_url_mapping', {})
            self.random_emoji_trigger_probability = self.config['random_emoji_trigger_probability']
            self.feature_switches = self.config.get('feature_switches', {})
            self.poke_back_probability = self.config['poke_back_probability']
            self.super_poke_probability = self.config['super_poke_probability']
            self.timeout = self.config['post_timeout']
        except FileNotFoundError:
            pass
        except Exception as e:
            pass

        self._clean_legacy_directories()
        self._clean_emoji_directory()  # 添加此行以在启动时清理表情包目录

    def _create_default_config(self, config_path):
        """创建默认配置文件"""
        default_config = {
            # 戳一戳回复消息
            "poke_responses": [
                "别戳啦！",
                "哎呀，还戳呀，别闹啦！",
                "别戳我啦  你要做什么  不理你了"
            ],
            # 表情包 API type 映射
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
            # 随机触发表情包的概率
            "random_emoji_trigger_probability": 0.5,
            # 请求超时时间
            "post_timeout": 20,
            # 功能开关
            "feature_switches": {
                "poke_response_enabled": True,
                "poke_back_enabled": True,
                "emoji_trigger_enabled": True
            },
            # 戳 Bot 反击相关概率
            "poke_back_probability": 0.3,
            "super_poke_probability": 0.1
        }
        # 创建配置文件所在目录
        config_dir = os.path.dirname(config_path)
        os.makedirs(config_dir, exist_ok=True)
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(default_config, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            pass

    def _clean_legacy_directories(self):
        """安全清理旧目录（仅删除特定目录）"""
        legacy_dirs = [
            os.path.abspath("./data/plugins/poke_monitor"),  # 旧版本目录
            os.path.abspath("./data/plugins/plugins/poke_monitor")  # 防止误删其他插件
        ]

        for path in legacy_dirs:
            try:
                if os.path.exists(path):
                    shutil.rmtree(path)
            except Exception as e:
                pass

    def _clean_emoji_directory(self):
        """清理表情包目录下的所有图片"""
        save_dir = os.path.join("data", "plugins", "astrbot_plugin_pock", "poke_monitor")
        if os.path.exists(save_dir):
            for filename in os.listdir(save_dir):
                file_path = os.path.join(save_dir, filename)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                except Exception as e:
                    pass

    @event_message_type(filter.EventMessageType.ALL)
    async def on_group_message(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        raw_message = message_obj.raw_message
        is_super = False  # 超级加倍标志

        # 判断 aiocqhttp 戳一戳事件
        if raw_message.get('post_type') == 'notice' and \
                raw_message.get('notice_type') == 'notify' and \
                raw_message.get('sub_type') == 'poke':
            bot_id = raw_message.get('self_id')
            sender_id = raw_message.get('user_id')
            target_id = raw_message.get('target_id')

            now = time.time()
            three_minutes_ago = now - 3 * 60

            # 清理旧记录
            if sender_id in self.user_poke_timestamps:
                self.user_poke_timestamps[sender_id] = [
                    t for t in self.user_poke_timestamps[sender_id] if t > three_minutes_ago
                ]

            if bot_id and sender_id and target_id:
                # 用户戳机器人
                if str(target_id) == str(bot_id):
                    # 记录戳一戳
                    if sender_id not in self.user_poke_timestamps:
                        self.user_poke_timestamps[sender_id] = []
                    self.user_poke_timestamps[sender_id].append(now)

                    # 文本回复
                    if self.feature_switches.get('poke_response_enabled', True):
                        poke_count = len(self.user_poke_timestamps[sender_id])
                        if poke_count < 4:
                            response = self.poke_responses[poke_count - 1] if poke_count <= len(self.poke_responses) else self.poke_responses[-1]
                            yield event.plain_result(response)

                    # 概率戳回
                    if self.feature_switches.get('poke_back_enabled', True) and random.random() < self.poke_back_probability:
                        if random.random() < self.super_poke_probability:
                            poke_times = 5 # 缩小暴击次数，从 10 次缩减至 5 次，尽量避免风控
                            yield event.plain_result("喜欢戳是吧")
                            is_super = True
                        else:
                            poke_times = 1
                            yield event.plain_result("戳回去")

                        # 发送戳一戳
                        if event.get_platform_name() == "aiocqhttp":
                            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                            assert isinstance(event, AiocqhttpMessageEvent)
                            client = event.bot
                            group_id = raw_message.get('group_id')
                            payloads = {"user_id": sender_id}
                            if group_id:
                                payloads["group_id"] = group_id
                            for _ in range(poke_times):
                                try:
                                    await client.api.call_action('send_poke', **payloads)
                                except Exception as e:
                                    pass

                # 用户戳其他人（且不是机器人自己触发的）
                elif str(sender_id) != str(bot_id):  
                    # 随机触发表情包
                    if self.feature_switches.get('emoji_trigger_enabled', True) and random.random() < self.random_emoji_trigger_probability:
                        available_actions = list(self.emoji_url_mapping.keys())
                        selected_action = random.choice(available_actions)
                        emoji_type = self.emoji_url_mapping[selected_action] # 拿到随机表情包的 type

                        url = "https://api.lolimi.cn/API/preview/api.php" # 原API的新版接口
                        params = {'qq': target_id, "action": "create_meme", "type": emoji_type} # 构建请求参数

                        # 硬编码请求配置
                        timeout = self.timeout # 超时时间，移至配置文件修改
                        max_retries = 3
                        retry_count = 0
                        while retry_count < max_retries:
                            try:
                                response = requests.get(url, params=params, timeout=timeout)
                                if response.status_code == 200:
                                    # 跨平台安全路径
                                    save_dir = os.path.join("data", "plugins", "astrbot_plugin_pock", "poke_monitor")
                                    os.makedirs(save_dir, exist_ok=True)

                                    # 唯一文件名防止冲突
                                    filename = f"{selected_action}_{target_id}_{int(time.time())}.gif"
                                    image_path = os.path.join(save_dir, filename)

                                    with open(image_path, "wb") as f:
                                        f.write(response.content)
                                    yield event.image_result(image_path)

                                    # 在发送成功后删除图片
                                    if os.path.exists(image_path):
                                        try:
                                            os.remove(image_path)
                                        except Exception as e:
                                            pass
                                    break
                                else:
                                    yield event.plain_result(f"表情包请求失败，状态码：{response.status_code}")
                                    break
                            except requests.exceptions.ReadTimeout:
                                retry_count += 1
                                if retry_count == max_retries:
                                    yield event.plain_result(f"表情包处理出错：多次请求超时，无法获取数据。")
                            except Exception as e:
                                yield event.plain_result(f"表情包处理出错：{str(e)}")
                                break
        # 判断 WechatPadPro 拍一拍事件
        if raw_message.get("to_user_name"):
            is_private = False
            content_str = raw_message["content"]["str"]  # 逐层获取data
            # 只处理拍一拍类型的 sysmsg 消息
            if raw_message.get("msg_type") != 10002:
                return  # 非拍一拍消息，返回不处理
            # 过滤前缀，拿到xml全文，获取xml对象（群聊场景分割掉前缀再解析，如果抛出 IndexError 则判断为私聊场景，直接解析）
            try:
                xml_content = Selector(text = content_str.split(":", 1)[1].strip())
            except IndexError:
                xml_content = content_str.strip()
                is_private = True
            if not isinstance(xml_content, Selector):
                xml_content = Selector(text=xml_content, type="html")
            bot_id = event.get_self_id() # 获得bot自身wxid
            sender_id = xml_content.xpath("//pat/fromusername//text()").get()  # 谁拍的（wxid）
            chatusername = xml_content.xpath("//pat/chatusername//text()").get()  # 在哪里拍的（chatroom）
            target_id = xml_content.xpath("//pat/pattedusername//text()").get()  # 拍的目标
            logger.info(f"捕获到拍一拍事件：{sender_id}在{chatusername}拍了拍{target_id}，bot_id为{bot_id}")

            now = time.time()
            three_minutes_ago = now - 3 * 60

            # 清理旧记录
            if sender_id in self.user_poke_timestamps:
                self.user_poke_timestamps[sender_id] = [
                    t for t in self.user_poke_timestamps[sender_id] if t > three_minutes_ago
                ]

            if bot_id and sender_id and target_id:
                # 用户拍机器人
                if str(target_id) == str(bot_id):
                    # 记录戳一戳
                    if sender_id not in self.user_poke_timestamps:
                        self.user_poke_timestamps[sender_id] = []
                    self.user_poke_timestamps[sender_id].append(now)

                    # 文本回复
                    if self.feature_switches.get('poke_response_enabled', True):
                        poke_count = len(self.user_poke_timestamps[sender_id])
                        if poke_count < 4:
                            response = self.poke_responses[poke_count - 1] if poke_count <= len(
                                self.poke_responses) else self.poke_responses[-1]
                            yield event.plain_result(response)

                    # 概率拍回
                    if self.feature_switches.get('poke_back_enabled',
                                                 True) and random.random() < self.poke_back_probability:
                        if random.random() < self.super_poke_probability:
                            poke_times = 5 # 缩小暴击次数，从 10 次缩减至 5 次，尽量避免风控
                            yield event.plain_result("喜欢拍是吧")
                            is_super = True
                        else:
                            poke_times = 1
                            yield event.plain_result("拍回去")

                        # 发送戳一戳
                        if event.get_platform_name() == "wechatpadpro":
                            from astrbot.core.platform.sources.wechatpadpro.wechatpadpro_message_event import WeChatPadProMessageEvent
                            assert isinstance(event, WeChatPadProMessageEvent)
                            # 构造请求体
                            headers = {
                                "accept": "application/json",
                                "Content-Type": "application/json"
                            }
                            payloads = {
                              "ChatRoomName": chatusername,
                              "Scene": 0,
                              "ToUserName": sender_id
                            }
                            params = {"key": event.adapter.auth_key} # 带上Token发送
                            wxapi_url = event.adapter.base_url + "/group/SendPat"

                            for _ in range(poke_times):
                                try:
                                    async with aiohttp.ClientSession() as session:
                                        async with session.post(wxapi_url, headers=headers, json=payloads,
                                                                params=params) as resp:
                                            if resp.status != 200:
                                                logger.error(f"❌请求失败，状态码: {resp.status}")
                                                return
                                except Exception as e:
                                    pass

                # 用户戳其他人（且不是机器人自己触发的）
                elif str(sender_id) != str(bot_id):
                    # 如果是私聊场景，自己戳自己（貌似也只能这样了），则直接停止事件传播，不产出表情包
                    if is_private:
                        event.stop_event()
                        return
                    # 随机触发表情包
                    if self.feature_switches.get('emoji_trigger_enabled', True) and random.random() < self.random_emoji_trigger_probability:
                        available_actions = list(self.emoji_url_mapping.keys())
                        selected_action = random.choice(available_actions)
                        emoji_type = self.emoji_url_mapping[selected_action] # 拿到随机表情包的 type

                        url = "https://api.lolimi.cn/API/preview/api.php" # 原API的新版接口

                        # 硬编码请求配置
                        timeout = self.timeout # 超时时间，移至配置文件修改
                        max_retries = 3
                        retry_count = 0
                        while retry_count < max_retries:
                            try:
                                # 构造请求体，请求群聊列表
                                payloads = {
                                    "ChatRoomName": chatusername
                                }
                                headers = {
                                    "accept": "application/json",
                                    "Content-Type": "application/json"
                                }
                                params = {"key": event.adapter.auth_key}
                                wxapi_url = event.adapter.base_url + "/group/GetChatroomMemberDetail"
                                async with aiohttp.ClientSession() as session:
                                    async with session.post(wxapi_url, headers=headers, json=payloads, params=params) as resp:
                                        response = await resp.json()

                                # 请求到群聊列表后，遍历依次查找data里成员列表，找到发送者的头像 URL
                                members_list = response["Data"]["member_data"]["chatroom_member_list"]

                                big_head_img_url = next(
                                    (member["big_head_img_url"] for member in members_list if
                                     member["user_name"] == target_id), None)

                                # 拿到头像 URL 后，直接拿给API去制作表情包（接口带上i = 2参数后，qq参数可以直接传图片URL）
                                params = {'qq': big_head_img_url, "i": "2", "action": "create_meme", "type": emoji_type}
                                response = requests.post(url, params=params, timeout=timeout)

                                if response.status_code == 200:
                                    # 跨平台安全路径
                                    save_dir = os.path.join("data", "plugins", "astrbot_plugin_pock", "poke_monitor")
                                    os.makedirs(save_dir, exist_ok=True)

                                    # 唯一文件名防止冲突
                                    filename = f"{selected_action}_{target_id}_{int(time.time())}.gif"
                                    image_path = os.path.join(save_dir, filename)
                                    with open(image_path, "wb") as f:
                                        f.write(response.content)
                                    yield event.image_result(image_path)
                                    # 在发送成功后删除图片
                                    if os.path.exists(image_path):
                                        try:
                                            os.remove(image_path)
                                        except Exception as e:
                                            pass
                                    break
                                else:
                                    yield event.plain_result(f"表情包请求失败，状态码：{response.status_code}")
                                    break
                            except requests.exceptions.ReadTimeout:
                                retry_count += 1
                                if retry_count == max_retries:
                                    yield event.plain_result(f"表情包处理出错：多次请求超时，无法获取数据。")
                            except Exception as e:
                                yield event.plain_result(f"表情包处理出错：{str(e)}")
                                break