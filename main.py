import aiohttp
import asyncio
import os
import re
import json
import time
from urllib.parse import quote
from typing import Dict, Any, List, Optional
from datetime import datetime

from astrbot.api.message_components import Node, Plain, Image
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import AstrBotConfig
from astrbot.api import logger
from .method import TEMP_DIR, get_img_changeFormat

# --- 自定义异常 ---
class NoSubjectFound(Exception):
    """找不到对应条目的异常类"""
    pass

class BangumiApiError(Exception):
    """Bangumi API请求错误的异常类"""
    pass

class BangumiRateLimitError(Exception):
    """API限流异常类"""
    pass

# --- API交互类 ---
class API_Bangumi():
    def __init__(self, access_token: str, user_agent: str):
        if not access_token:
            raise ValueError("Bangumi access_token 未设置, 插件无法工作。")
        self.base_url = "https://api.bgm.tv"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": user_agent
        }
        self.type_map = {
            1: "📚 书籍",
            2: "🎬 动画",
            3: "🎵 音乐",
            4: "🎮 游戏",
            6: "🌐 三次元"
        }
        self.search_cache: Dict[str, Dict] = {}
        self.last_request_time = 0

    async def _request(self, url: str, method: str = 'GET', params: Dict[str, Any] = None, 
                      json_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """通用API请求函数，带限流处理"""
        current_time = time.time()
        if current_time - self.last_request_time < 1.1:
            await asyncio.sleep(1.1 - (current_time - self.last_request_time))
        self.last_request_time = time.time()
        
        logger.info(f"Bangumi API请求: {method} {url}")
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                if method.upper() == 'POST':
                    async with session.post(url, json=json_data, params=params, ssl=False) as response:
                        return await self._handle_response(response)
                else:
                    async with session.get(url, params=params, ssl=False) as response:
                        return await self._handle_response(response)
        except aiohttp.ClientError as e:
            logger.error(f"网络请求失败: {e}")
            raise BangumiApiError("网络连接异常，请稍后再试")

    async def _handle_response(self, response: aiohttp.ClientResponse) -> Dict:
        """处理API响应"""
        if response.status == 200:
            return await response.json()
        elif response.status == 404:
            raise NoSubjectFound("未找到相关条目")
        elif response.status == 429:
            raise BangumiRateLimitError("API请求过于频繁，请稍后再试")
        else:
            error_text = await response.text()
            logger.error(f"API错误: {response.status} - {error_text}")
            raise BangumiApiError(f"API服务异常 ({response.status})")

    async def search_subjects(self, keyword: str, limit: int = 10) -> Dict[str, Any]:
        """通过关键词搜索条目"""
        cache_key = f"search:{keyword}:{limit}"
        if cache_key in self.search_cache:
            return self.search_cache[cache_key]
        
        url = f"{self.base_url}/v0/search/subjects"
        json_data = {'keyword': keyword}
        params = {'limit': limit}
        
        data = await self._request(url, method='POST', json_data=json_data, params=params)
        
        self.search_cache[cache_key] = data
        asyncio.get_event_loop().call_later(300, lambda: self.search_cache.pop(cache_key, None))
        
        return data

    async def get_subject_details(self, subject_id: int) -> Dict[str, Any]:
        """获取单个条目的详细信息"""
        url = f"{self.base_url}/v0/subjects/{subject_id}"
        return await self._request(url)

    def format_subject_info(self, subject: Dict[str, Any]) -> str:
        """格式化条目信息为Markdown"""
        name = subject.get('name', '未知名称')
        name_cn = subject.get('name_cn', name) or name
        
        type_id = subject.get('type', 2)
        type_str = self.type_map.get(type_id, self.type_map[2])
        date_str = subject.get('date', '未知日期')
        
        rating = subject.get('rating', {})
        score = rating.get('score', 0)
        total_votes = rating.get('total', 0)
        rank = subject.get('rank', 0)
        
        summary = subject.get('summary', '暂无简介')
        summary = re.sub(r'<br\s*/?>', '\n', summary)
        summary = re.sub(r'<.*?>', '', summary)
        
        tags = ", ".join([tag['name'] for tag in subject.get('tags', [])[:5]])
        
        info_str = (
            f"**{name_cn}**\n"
            f"原名: {name}\n"
            f"类型: {type_str} | 日期: {date_str}\n"
            f"评分: ⭐ {score} (基于{total_votes}人评分)"
            f"{' | 排名: #' + str(rank) if rank else ''}\n"
            f"标签: {tags or '无'}\n"
            f"ID: `{subject.get('id')}`\n"
            f"---\n"
            f"{summary}"
        )
        return info_str
    
    def format_fuzzy_list(self, data: Dict[str, Any], limit: int) -> str:
        """格式化模糊搜索结果"""
        results = data.get('data', [])
        if not results:
            return "🔍 未找到相关条目"
        
        output = ["找到以下条目：\n"]
        for i, item in enumerate(results[:limit], 1):
            name_cn = item.get('name_cn') or item.get('name', '未知名称')
            item_type = self.type_map.get(item.get('type'), '🎬 动画')
            date = item.get('date', '未知日期')
            output.append(f"{i}. {name_cn} ({item_type}, {date}) ID: `{item['id']}`")
        
        if data.get('total', 0) > limit:
            output.append(f"\n共找到 {data['total']} 个结果, 显示前 {limit} 个")
            
        return "\n".join(output)

# --- Astrbot 插件主类 ---
@register(
    "astrbot_plugin_bangumi",
    "Gemini",
    "一个用于查询Bangumi条目信息的插件",
    "1.1.0",
    "https://github.com/bangumi/api"
)
class BangumiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.access_token = self.config.get("access_token", "")
        self.user_agent = self.config.get("user_agent", "AstrBot-Bangumi-Plugin/1.0 (https://github.com/yourname/yourrepo)")
        self.max_fuzzy_results = int(self.config.get("max_fuzzy_results", 5))
        self.use_forward_msg = self.config.get("use_forward", "关闭") == "开启"
        self.use_filesystem = self.config.get("if_fromfilesystem", "关闭") == "开启"
        
        try:
            self.bgm_api = API_Bangumi(self.access_token, self.user_agent)
            logger.info("Bangumi插件初始化成功")
        except ValueError as e:
            logger.error(f"插件初始化失败: {e}")
            self.bgm_api = None

    @filter.command("bgm搜索")
    async def accurate_search(self, event: AstrMessageEvent):
        """准确搜索条目 - 用法: /bgm搜索 <关键词|ID>"""
        if not self.bgm_api:
            return event.plain_result("❌ Bangumi插件未正确配置")
        
        cmd = event.message_str.split(maxsplit=1)
        if len(cmd) < 2:
            return event.plain_result("❌ 格式错误，用法: /bgm搜索 <关键词|ID>")
        
        query = cmd[1].strip()
        is_id_search = query.isdigit()
        
        try:
            # 先发送搜索中提示
            event.plain_result(f"🔍 正在搜索: {query}")
            
            if is_id_search:
                subject_id = int(query)
                subject = await self.bgm_api.get_subject_details(subject_id)
                info_text = self.bgm_api.format_subject_info(subject)
                message_content = await self._build_reply(subject, info_text)
            else:
                search_data = await self.bgm_api.search_subjects(query, limit=1)
                
                if not search_data.get('data'):
                    return event.plain_result(f"❌ 未找到相关条目: {query}")
                
                subject_id = search_data['data'][0]['id']
                subject = await self.bgm_api.get_subject_details(subject_id)
                info_text = self.bgm_api.format_subject_info(subject)
                message_content = await self._build_reply(subject, info_text)
            
            # 发送最终结果
            return event.chain_result(message_content)
            
        except NoSubjectFound:
            return event.plain_result(f"❌ 未找到相关条目: {query}")
        except BangumiRateLimitError:
            return event.plain_result("⚠️ 请求过于频繁，请稍后再试")
        except BangumiApiError as e:
            return event.plain_result(f"❌ API错误: {str(e)}")
        except Exception as e:
            logger.exception("准确搜索异常")
            return event.plain_result("❌ 内部错误，请查看日志")

    @filter.command("bgm模糊")
    async def fuzzy_search(self, event: AstrMessageEvent):
        """模糊搜索条目 - 用法: /bgm模糊 <关键词>"""
        if not self.bgm_api:
            return event.plain_result("❌ Bangumi插件未正确配置")
        
        cmd = event.message_str.split(maxsplit=1)
        if len(cmd) < 2:
            return event.plain_result("❌ 格式错误，用法: /bgm模糊 <关键词>")
        
        query = cmd[1].strip()
        
        try:
            # 先发送搜索中提示
            event.plain_result(f"🔍 正在搜索: {query}")
            
            search_data = await self.bgm_api.search_subjects(query, limit=self.max_fuzzy_results)
            result_text = self.bgm_api.format_fuzzy_list(search_data, self.max_fuzzy_results)
            
            if self.use_forward_msg:
                node = Node(uin=event.bot.self_id, name="Bangumi模糊搜索", content=[Plain(result_text)])
                return event.chain_result([node])
            return event.plain_result(result_text)
            
        except BangumiRateLimitError:
            return event.plain_result("⚠️ 请求过于频繁，请稍后再试")
        except BangumiApiError as e:
            return event.plain_result(f"❌ API错误: {str(e)}")
        except Exception as e:
            logger.exception("模糊搜索异常")
            return event.plain_result("❌ 内部错误，请查看日志")

    async def _build_reply(self, subject: Dict, info_text: str) -> list:
        """构建回复消息组件列表"""
        img_url = subject.get('images', {}).get('large')
        message_content = []
        
        if img_url:
            try:
                img_path = await get_img_changeFormat(img_url, TEMP_DIR, ssl=False)
                if self.use_filesystem:
                    message_content.append(Image.fromFileSystem(img_path))
                else:
                    with open(img_path, "rb") as f:
                        message_content.append(Image.fromBytes(f.read()))
                os.remove(img_path)
            except Exception as e:
                logger.warning(f"图片处理失败: {e}")
        
        message_content.append(Plain(info_text))
        return message_content

    async def terminate(self):
        """插件卸载"""
        logger.info("Bangumi插件已卸载")