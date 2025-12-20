"""
聊天总结图片生成器 - HTML渲染版本
使用 Jinja2 模板 + Playwright 浏览器渲染生成图片
"""

import os
import uuid
import base64
import aiohttp
from typing import List, Dict, Optional
from datetime import datetime

from .html_template_manager import HTMLTemplateManager
from .html_renderer import render_html_to_image
from .constants import AnalysisConfig

# 导入logger
try:
    from src.common.logger import get_logger
    logger = get_logger("summary_image_generator")
except ImportError:
    import logging
    logger = logging.getLogger("summary_image_generator")


class SummaryImageGenerator:
    """生成聊天总结图片 - HTML渲染版本"""

    @staticmethod
    async def _download_qq_avatar_base64(qq_id: str, size: int = 100) -> Optional[str]:
        """下载QQ用户头像并转换为base64

        Args:
            qq_id: QQ号
            size: 头像尺寸

        Returns:
            base64编码的图片数据URL，失败返回 None
        """
        if not qq_id:
            return None

        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={qq_id}&s={size}"
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(avatar_url) as resp:
                    if resp.status == 200:
                        avatar_data = await resp.read()
                        avatar_base64 = base64.b64encode(avatar_data).decode('utf-8')
                        return f"data:image/jpeg;base64,{avatar_base64}"
        except Exception as e:
            logger.debug(f"下载头像失败 (QQ:{qq_id}): {e}")

        return None

    @staticmethod
    async def generate_summary_image(
        title: str,
        summary_text: str,
        time_info: str = "",
        message_count: int = 0,
        participant_count: int = 0,
        width: int = None,
        topics: list = None,
        user_titles: list = None,
        golden_quotes: list = None,
        depression_index: list = None,
        hourly_distribution: dict = None,
        user_profile: dict = None,
        group_id: str = None,
        display_order: list = None,
        target_date: datetime = None,
        max_depression_display: int = None,
        depression_show_bottom: bool = None
    ) -> str:
        """生成聊天总结图片 - 使用HTML模板渲染

        Args:
            title: 标题
            summary_text: 总结文本
            time_info: 时间信息
            message_count: 消息数量
            participant_count: 参与人数
            width: 图片宽度 (未使用，保持接口兼容)
            topics: 话题列表
            user_titles: 群友称号列表
            golden_quotes: 金句列表
            depression_index: 炫压抑指数列表
            hourly_distribution: 24小时发言分布数据
            user_profile: 单个用户画像数据 (未使用)
            group_id: QQ群号，用于标识和清理旧图片
            display_order: 模块显示顺序（可选项：24H, Topics, Titles, Depression, Quotes）
            target_date: 目标日期（用于显示正确的日期，默认为今天）

        Returns:
            str: 图片文件的绝对路径
        """
        # 初始化
        if topics is None:
            topics = []
        if user_titles is None:
            user_titles = []
        if golden_quotes is None:
            golden_quotes = []
        if hourly_distribution is None:
            hourly_distribution = {}
        if display_order is None:
            display_order = ["24H", "Topics", "Portraits", "Quotes", "Rankings"]
        # 炫压抑评级配置：如果未传入则使用 constants.py 中的默认值
        if max_depression_display is None:
            max_depression_display = AnalysisConfig.MAX_DEPRESSION_DISPLAY
        if depression_show_bottom is None:
            depression_show_bottom = AnalysisConfig.DEPRESSION_SHOW_BOTTOM_HALF

        # 获取插件目录和模板目录
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_dir = os.path.join(plugin_dir, "templates", "scrapbook")

        # 创建模板管理器
        template_manager = HTMLTemplateManager(template_dir)

        # ===== 准备模板数据 =====

        # 目标日期（如果未指定则使用今天）
        if target_date is None:
            target_date = datetime.now()
        current_date = target_date.strftime("%Y年%m月%d日")

        # 计算总字符数
        total_characters = len(summary_text)

        # 计算表情数量 (简化：取消息数的 10%)
        emoji_count = int(message_count * 0.1)

        # 计算最活跃时段
        most_active_period = "未知"
        if hourly_distribution:
            max_hour = max(hourly_distribution, key=hourly_distribution.get)
            most_active_period = f"{max_hour:02d}:00-{(max_hour+1)%24:02d}:00"

        # ===== 渲染24小时活跃图表 =====
        hourly_chart_html = ""
        if "24H" in display_order and hourly_distribution:
            # 准备图表数据
            max_count = max(hourly_distribution.values()) if hourly_distribution.values() else 1
            chart_data = []
            for hour in range(24):
                count = hourly_distribution.get(hour, 0)
                percentage = int((count / max_count) * 100) if max_count > 0 else 0
                chart_data.append({
                    "hour": hour,
                    "count": count,
                    "percentage": percentage
                })

            # 使用模板渲染完整的24H模块（包含标题和图表）
            hourly_chart_html = template_manager.render_template(
                "activity_chart_section.html",
                chart_data=chart_data
            )

        # ===== 渲染话题列表 =====
        topics_html = ""
        if "Topics" in display_order and topics:
            # 准备话题数据
            topic_list = []
            for idx, topic_item in enumerate(topics[:5], start=1):
                topic_data = topic_item.get("topic", "")
                detail = topic_item.get("detail", "")
                contributors = topic_item.get("contributors", [])

                # 如果 topic_data 是字符串，包装成字典
                if isinstance(topic_data, str):
                    topic_dict = {"topic": topic_data, "detail": detail}
                else:
                    topic_dict = topic_data

                topic_list.append({
                    "index": idx,
                    "topic": topic_dict,
                    "detail": detail,
                    "contributors": "、".join(contributors[:5])
                })

            topics_html = template_manager.render_template(
                "topic_item.html",
                topics=topic_list
            )

        # ===== 渲染群友画像（user_titles）=====
        portraits_html = ""
        if "Portraits" in display_order and user_titles:
            # 准备群友画像数据
            title_list = []
            for title_item in user_titles[:6]:  # 最多显示6个
                name = title_item.get("name", "")
                title = title_item.get("title", "")
                mbti = title_item.get("mbti", "")
                reason = title_item.get("reason", "")
                avatar_data = title_item.get("avatar_data", "")

                title_list.append({
                    "name": name,
                    "title": title,
                    "mbti": mbti,
                    "reason": reason,
                    "avatar_data": avatar_data
                })

            portraits_html = template_manager.render_template(
                "user_title_item.html",
                titles=title_list  # 注意：模板期望的变量名是 titles
            )

        # ===== 渲染群贤毕至（金句）=====
        quotes_html = ""
        if "Quotes" in display_order and golden_quotes:
            # 准备金句数据
            quote_list = []
            for quote_item in golden_quotes[:4]:
                content = quote_item.get("content", "")
                sender = quote_item.get("sender", "")
                reason = quote_item.get("reason", "")

                quote_list.append({
                    "content": content,
                    "sender": sender,
                    "reason": reason
                })

            quotes_html = template_manager.render_template(
                "quote_item.html",
                quotes=quote_list
            )

        # ===== 渲染炫压抑评级 =====
        rankings_html = ""
        if "Rankings" in display_order:
            depression_rankings = []

            if not depression_index or len(depression_index) == 0:
                # 0人：显示"此群无压抑指数，可能是凉了~"
                depression_rankings = []  # 空列表，模板会显示特殊消息
            else:
                # 有数据：根据配置决定展示方式
                total_count = len(depression_index)

                if total_count <= max_depression_display:
                    # 人数不超过最大展示数：全部显示，正常排名
                    for i, entry in enumerate(depression_index, 1):
                        depression_rankings.append({
                            "name": entry.get("name", ""),
                            "rank": entry.get("rank", ""),
                            "comment": entry.get("comment", ""),
                            "position": i
                        })
                else:
                    # 超过最大展示数：根据配置决定展示方式
                    if depression_show_bottom:
                        # 展示前半 + 后半，正数优先（奇数时前半多1个）
                        # 6 -> 前3+后3, 7 -> 前4+后3, 8 -> 前4+后4
                        top_count = (max_depression_display + 1) // 2  # 向上取整
                        bottom_count = max_depression_display - top_count  # 剩余给后半
                        # 前半部分
                        for i, entry in enumerate(depression_index[:top_count], 1):
                            depression_rankings.append({
                                "name": entry.get("name", ""),
                                "rank": entry.get("rank", ""),
                                "comment": entry.get("comment", ""),
                                "position": i
                            })
                        # 后半部分（倒数）
                        bottom_entries = depression_index[-bottom_count:]
                        for i, entry in enumerate(bottom_entries, 1):
                            depression_rankings.append({
                                "name": entry.get("name", ""),
                                "rank": entry.get("rank", ""),
                                "comment": entry.get("comment", ""),
                                "position": f"fall {bottom_count - i + 1}"  # fall 3, fall 2, fall 1
                            })
                    else:
                        # 只展示前 N 名
                        for i, entry in enumerate(depression_index[:max_depression_display], 1):
                            depression_rankings.append({
                                "name": entry.get("name", ""),
                                "rank": entry.get("rank", ""),
                                "comment": entry.get("comment", ""),
                                "position": i
                            })

            rankings_html = template_manager.render_template(
                "depression_index_item.html",
                depression_rankings=depression_rankings
            )

        # ===== 根据 display_order 动态组装模块HTML =====
        modules_html_list = []
        module_map = {
            "24H": hourly_chart_html,
            "Topics": topics_html,
            "Portraits": portraits_html,
            "Quotes": quotes_html,
            "Rankings": rankings_html
        }

        for module_name in display_order:
            if module_name in module_map and module_map[module_name]:
                modules_html_list.append(module_map[module_name])

        # 将所有模块HTML合并
        modules_html = "\n".join(modules_html_list)

        # ===== 渲染主模板 =====
        html_content = template_manager.render_template(
            "image_template.html",
            current_date=current_date,
            message_count=message_count,
            participant_count=participant_count,
            emoji_count=emoji_count,
            total_characters=total_characters,
            most_active_period=most_active_period,
            modules_html=modules_html  # 使用动态组装的模块HTML
        )

        # ===== 使用 Playwright 渲染为图片 =====
        try:
            # 获取插件根目录
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # 向上一级到达插件目录 (chat_summary_plugin)
            plugin_root = os.path.dirname(current_dir)
            # 插件内的图片保存目录
            images_dir = os.path.join(plugin_root, "data_GeneratePicture")

            # 确保目录存在
            os.makedirs(images_dir, exist_ok=True)

            # ===== 清理同一群的旧图片 =====
            if group_id:
                try:
                    import glob
                    # 查找所有属于该群的图片文件
                    pattern = os.path.join(images_dir, f"summary_{group_id}_*.jpg")
                    old_images = glob.glob(pattern)

                    for old_image in old_images:
                        try:
                            os.remove(old_image)
                            logger.debug(f"已删除旧图片: {old_image}")
                        except Exception as e:
                            logger.warning(f"删除旧图片失败 {old_image}: {e}")
                except Exception as e:
                    logger.warning(f"清理旧图片失败: {e}")

            # 生成新文件名（包含群号和时间戳）
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if group_id:
                filename = f"summary_{group_id}_{timestamp}.jpg"
            else:
                filename = f"summary_{uuid.uuid4().hex[:8]}_{timestamp}.jpg"

            img_path = os.path.join(images_dir, filename)

            # 使用 Playwright 渲染（保持原始宽度，使用2倍像素密度提高清晰度）
            success = await render_html_to_image(
                html_content,
                img_path,
                viewport_width=1000,         # 保持原始宽度
                viewport_height=800,         # 保持原始高度
                full_page=True,
                image_type="jpeg",
                quality=100,                 # 最高质量
                device_scale_factor=2.0      # 2倍像素密度，图片更清晰但显示尺寸不变
            )

            if not success:
                raise IOError("HTML渲染为图片失败")

            if not os.path.exists(img_path):
                raise IOError("图片文件未生成")

            # 检查文件大小
            file_size = os.path.getsize(img_path) / (1024 * 1024)  # MB
            logger.info(f"成功生成总结图片: {img_path} (大小: {file_size:.2f}MB)")

            return img_path

        except Exception as e:
            logger.error(f"生成总结图片失败: {e}", exc_info=True)
            raise

    @staticmethod
    async def generate_user_summary_image(
        user_name: str,
        user_id: str,
        summary_text: str = "",
        message_count: int = 0,
        total_characters: int = 0,
        emoji_count: int = 0,
        hourly_distribution: dict = None,
        user_title: str = "",
        user_mbti: str = "",
        portrait_data: dict = None,
        depression_data: dict = None,
        golden_quotes: list = None,
        display_order: list = None,
        target_date: datetime = None
    ) -> str:
        """生成个人用户总结图片

        Args:
            user_name: 用户名称
            user_id: 用户QQ号
            summary_text: AI总结文本
            message_count: 消息数量
            total_characters: 总字符数
            emoji_count: 表情数量
            hourly_distribution: 24小时发言分布数据
            user_title: 用户称号
            user_mbti: 用户MBTI
            portrait_data: 用户画像数据 {"name": "xxx", "title": "xxx", "mbti": "xxx", "reason": "xxx", "avatar_data": "xxx"}
            depression_data: 炫压抑评级数据 {"name": "xxx", "rank": "S/A/B/C/D", "comment": "xxx"}
            golden_quotes: 金句列表
            display_order: 模块显示顺序（支持组合：["3H", "Portraits,Rankings", "Quotes"]）
            target_date: 目标日期（用于显示正确的日期，默认为今天）

        Returns:
            str: 图片文件的绝对路径
        """
        # 初始化
        if hourly_distribution is None:
            hourly_distribution = {}
        if golden_quotes is None:
            golden_quotes = []
        if display_order is None:
            display_order = ["3H", "Portraits", "Rankings", "Quotes"]

        # 获取插件目录和模板目录
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_dir = os.path.join(plugin_dir, "templates", "scrapbook")

        # 创建模板管理器
        template_manager = HTMLTemplateManager(template_dir)

        # ===== 准备模板数据 =====

        # 目标日期（如果未指定则使用今天）
        if target_date is None:
            target_date = datetime.now()
        current_date = target_date.strftime("%Y年%m月%d日")

        # 用户头像URL（和群聊总结保持一致，使用直接URL而非base64）
        avatar_data = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100" if user_id else ""

        # ===== 渲染 3H 活跃轨迹 =====
        activity_3h_html = ""
        if "3H" in str(display_order) and hourly_distribution:
            # 找到消息最多的时间段
            max_hour = max(hourly_distribution, key=hourly_distribution.get)
            max_count = hourly_distribution[max_hour]

            # 获取前后时间段
            prev_hour = (max_hour - 1) % 24
            next_hour = (max_hour + 1) % 24

            # 准备3个时间段的数据
            three_hours = [
                {
                    "time_label": f"{prev_hour:02d}:00-{max_hour:02d}:00",
                    "count": hourly_distribution.get(prev_hour, 0),
                    "percentage": 0
                },
                {
                    "time_label": f"{max_hour:02d}:00-{(max_hour+1)%24:02d}:00",
                    "count": max_count,
                    "percentage": 100
                },
                {
                    "time_label": f"{next_hour:02d}:00-{(next_hour+1)%24:02d}:00",
                    "count": hourly_distribution.get(next_hour, 0),
                    "percentage": 0
                }
            ]

            # 计算百分比
            if max_count > 0:
                three_hours[0]["percentage"] = int((three_hours[0]["count"] / max_count) * 100)
                three_hours[2]["percentage"] = int((three_hours[2]["count"] / max_count) * 100)

            # 渲染模板
            activity_3h_html = template_manager.render_template(
                "user_3h_activity.html",
                chart_data=three_hours
            )

        # ===== 准备模块HTML和配置 =====
        module_map = {}

        # 群友画像模块
        if portrait_data:
            # 如果没有头像数据，使用下载的头像
            if not portrait_data.get("avatar_data"):
                portrait_data["avatar_data"] = avatar_data

        # 金句模块
        quotes_html = ""
        if golden_quotes:
            quote_list = []
            for quote_item in golden_quotes[:4]:
                content = quote_item.get("content", "")
                reason = quote_item.get("reason", "")
                quote_list.append({
                    "content": content,
                    "sender": user_name,
                    "reason": reason
                })

            quotes_html = template_manager.render_template(
                "quote_item.html",
                quotes=quote_list
            )

        # ===== 解析 display_order 并动态组装模块 =====
        modules_html_list = []

        for order_item in display_order:
            # 检查是否是组合模块（包含逗号）
            if "," in order_item:
                # 组合模块：横向排列，每个占 span 6
                module_names = [name.strip() for name in order_item.split(",")]
                combined_html = ""

                for module_name in module_names:
                    if module_name == "Portraits" and portrait_data:
                        combined_html += template_manager.render_template(
                            "user_portrait_module.html",
                            portrait=portrait_data,
                            grid_span=6
                        )
                    elif module_name == "Rankings" and depression_data:
                        combined_html += template_manager.render_template(
                            "user_depression_module.html",
                            depression=depression_data,
                            grid_span=6
                        )

                if combined_html:
                    modules_html_list.append(combined_html)

            else:
                # 单独模块：占满一行，span 12
                module_name = order_item.strip()

                if module_name == "3H":
                    if activity_3h_html:
                        modules_html_list.append(activity_3h_html)

                elif module_name == "Portraits" and portrait_data:
                    modules_html_list.append(
                        template_manager.render_template(
                            "user_portrait_module.html",
                            portrait=portrait_data,
                            grid_span=12
                        )
                    )

                elif module_name == "Rankings" and depression_data:
                    modules_html_list.append(
                        template_manager.render_template(
                            "user_depression_module.html",
                            depression=depression_data,
                            grid_span=12
                        )
                    )

                elif module_name == "Quotes" and quotes_html:
                    modules_html_list.append(quotes_html)

        # 将所有模块HTML合并
        modules_html = "\n".join(modules_html_list)

        # 检查 Quotes 是否已经在 display_order 中被处理
        # 如果是，就不再单独传 quotes_html 给模板，避免重复渲染
        quotes_in_display_order = any("Quotes" in str(item) for item in display_order)
        template_quotes_html = "" if quotes_in_display_order else quotes_html

        # ===== 渲染主模板 =====
        html_content = template_manager.render_template(
            "user_summary_template.html",
            user_name=user_name,
            current_date=current_date,
            avatar_data=avatar_data,
            user_title=user_title,
            user_mbti=user_mbti,
            message_count=message_count,
            total_characters=total_characters,
            emoji_count=emoji_count,
            summary_text=summary_text,
            modules_html=modules_html,
            quotes_html=template_quotes_html
        )

        # ===== 使用 Playwright 渲染为图片 =====
        try:
            # 获取插件根目录
            current_dir = os.path.dirname(os.path.abspath(__file__))
            plugin_root = os.path.dirname(current_dir)
            images_dir = os.path.join(plugin_root, "data_GeneratePicture")

            # 确保目录存在
            os.makedirs(images_dir, exist_ok=True)

            # 清理旧的个人总结图片
            if user_id:
                try:
                    import glob
                    pattern = os.path.join(images_dir, f"user_summary_{user_id}_*.jpg")
                    old_images = glob.glob(pattern)

                    for old_image in old_images:
                        try:
                            os.remove(old_image)
                            logger.debug(f"已删除旧图片: {old_image}")
                        except Exception as e:
                            logger.warning(f"删除旧图片失败 {old_image}: {e}")
                except Exception as e:
                    logger.warning(f"清理旧图片失败: {e}")

            # 生成新文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if user_id:
                filename = f"user_summary_{user_id}_{timestamp}.jpg"
            else:
                filename = f"user_summary_{uuid.uuid4().hex[:8]}_{timestamp}.jpg"

            img_path = os.path.join(images_dir, filename)

            # 使用 Playwright 渲染
            success = await render_html_to_image(
                html_content,
                img_path,
                viewport_width=1000,
                viewport_height=800,
                full_page=True,
                image_type="jpeg",
                quality=100,
                device_scale_factor=2.0
            )

            if not success:
                raise IOError("HTML渲染为图片失败")

            if not os.path.exists(img_path):
                raise IOError("图片文件未生成")

            # 检查文件大小
            file_size = os.path.getsize(img_path) / (1024 * 1024)  # MB
            logger.info(f"成功生成个人总结图片: {img_path} (大小: {file_size:.2f}MB)")

            return img_path

        except Exception as e:
            logger.error(f"生成个人总结图片失败: {e}", exc_info=True)
            raise
