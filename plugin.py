"""
聊天记录总结插件

功能:
- 生成群聊整体的聊天记录总结
- 生成个人用户的聊天总结（只分析该用户的发言，不掺杂他人消息）
- 支持选择日期范围
- 支持每日定时自动生成总结
- 管理员可查看他人的个人总结

命令格式:
- /summary - 生成今天整个群聊的总结
- /summary 今天 - 生成今天整个群聊的总结
- /summary 昨天 - 生成昨天整个群聊的总结
- /mysummary - 生成自己今天的个人总结
- /mysummary 今天 - 生成自己今天的个人总结
- /mysummary 昨天 - 生成自己昨天的个人总结
- /mysummary @某人 - 管理员查看他人今天的个人总结
- /mysummary @某人 昨天 - 管理员查看他人昨天的个人总结
- /mysummary QQ号 - 管理员通过QQ号查看他人的个人总结
"""

import re
import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict
from collections import Counter

from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseCommand,
    BaseEventHandler,
    EventType,
    MaiMessages,
    ConfigField,
    database_api,
    llm_api,
    send_api,
    get_logger,
)
from src.common.database.database_model import Messages
from src.config.config import model_config
from .core import SummaryImageGenerator, ChatAnalysisUtils

logger = get_logger("chat_summary_plugin")


class ChatSummaryCommand(BaseCommand):
    """聊天记录总结命令"""

    command_name = "chat_summary"
    command_description = "生成聊天记录总结"
    command_pattern = r"^/summary\s*(.*)$"

    async def execute(self) -> Tuple[bool, str, bool]:
        """执行聊天记录总结"""
        try:
            # ===== 权限检查 =====
            # 获取当前群聊的 QQ 群号
            if not self.message.chat_stream:
                logger.error("chat_stream 为空，无法进行权限检查")
                return False, "chat_stream为空", False

            # 从 group_info 中获取真正的 QQ 群号
            if not self.message.chat_stream.group_info:
                logger.debug("这不是群聊消息，跳过权限检查")
                return True, "", False  # 非群聊消息，允许继续

            group_id = self.message.chat_stream.group_info.group_id

            # 读取配置
            use_blacklist = self.get_config("command_permission.use_blacklist", True)
            target_chats = self.get_config("command_permission.target_chats", [])

            # 确保 target_chats 是整数列表（WebUI 可能发送字符串列表）
            if target_chats and isinstance(target_chats, list):
                target_chats = [int(chat_id) if isinstance(chat_id, str) else chat_id for chat_id in target_chats]

            # group_id 可能是字符串或整数，统一转为整数进行比较
            try:
                group_id_int = int(group_id)
            except (ValueError, TypeError):
                logger.error(f"无效的 group_id: {group_id}")
                return False, "无效的群号", False

            # 检查权限
            if use_blacklist:
                # 黑名单模式：列表中的群不能使用
                if group_id_int in target_chats:
                    logger.debug(f"群聊 {group_id_int} 在黑名单中，静默跳过 /summary 命令")
                    return False, "权限不足", False  # 静默，不处理，让其他命令继续
            else:
                # 白名单模式：只有列表中的群可以使用
                if target_chats and group_id_int not in target_chats:
                    logger.debug(f"群聊 {group_id_int} 不在白名单中，静默跳过 /summary 命令")
                    return False, "权限不足", False  # 静默，不处理，让其他命令继续

            # ===== 管理员权限检查 =====
            admin_users = self.get_config("command_permission.admin_users", [])
            if admin_users:  # 如果列表不为空，进行管理员检查
                # 获取当前用户的QQ号
                user_id = self.message.message_info.user_info.user_id

                # 确保转换为整数进行比较
                try:
                    user_id_int = int(user_id)
                except (ValueError, TypeError):
                    logger.error(f"无效的 user_id: {user_id}")
                    return False, "无效的用户ID", False

                # 确保 admin_users 是整数列表（WebUI 可能发送字符串列表）
                admin_users = [int(uid) if isinstance(uid, str) else uid for uid in admin_users]

                # 检查用户是否在管理员列表中
                if user_id_int not in admin_users:
                    logger.debug(f"用户 {user_id_int} 不在管理员列表中，静默跳过 /summary 命令")
                    return False, "权限不足", False  # 静默，不处理

            # ===== 原有逻辑 =====
            # 获取命令参数
            match = re.match(self.command_pattern, self.message.raw_message)
            if not match:
                await self.send_text("用法: /summary [今天|昨天]")
                return True, "已发送使用说明", True

            args = match.group(1).strip()

            # 解析参数：只支持时间范围
            time_range = args if args else "今天"

            # 获取时间范围
            start_time, end_time = self._parse_time_range(time_range)
            if start_time is None or end_time is None:
                await self.send_text(f"只支持查询今天或昨天的记录哦")
                return False, f"不支持的时间范围: {time_range}", False

            # 获取聊天记录
            messages = await self._get_messages(start_time, end_time)

            if not messages:
                await self.send_text(f"{time_range}没有聊天记录呢")
                return True, "没有聊天记录", True

            # 发送等候提示
            await self.send_text(f"⏳ 正在分析{time_range}的聊天记录，请稍候...")

            # 生成总结
            summary = await self._generate_summary(messages, time_range)

            if summary:
                # 生成并发送图片
                try:
                    # 准备图片信息
                    title = f"{time_range}的群聊总结"

                    # 统计信息
                    participants = set()
                    for msg in messages:
                        nickname = msg.get("user_nickname", "")
                        if nickname:
                            participants.add(nickname)
                    participant_count = len(participants)

                    # 分析用户统计
                    user_stats = ChatAnalysisUtils.analyze_user_stats(messages)

                    # 计算24小时发言分布
                    from collections import Counter
                    hourly_distribution = Counter()
                    for msg in messages:
                        timestamp = msg.get("time", 0)
                        hour = datetime.fromtimestamp(timestamp).hour
                        hourly_distribution[hour] += 1
                    # 转换为普通字典
                    hourly_distribution = dict(hourly_distribution)

                    # 始终分析所有数据，由 display_order 控制显示
                    topics = await ChatAnalysisUtils.analyze_topics(messages) or []
                    user_titles = await ChatAnalysisUtils.analyze_user_titles(messages, user_stats) or []
                    golden_quotes = await ChatAnalysisUtils.analyze_golden_quotes(messages) or []
                    depression_index = await ChatAnalysisUtils.analyze_depression_index(messages, user_stats) or []

                    # 为 user_titles 添加头像数据
                    if user_titles:
                        for title_item in user_titles:
                            user_id = title_item.get("user_id", "")
                            if user_id:
                                # QQ头像URL格式
                                title_item["avatar_data"] = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100"
                            else:
                                title_item["avatar_data"] = ""

                    # 获取显示顺序配置
                    display_order = self.get_config("summary.display_order", ["24H", "Topics", "Portraits", "Quotes", "Rankings"])

                    # 计算目标日期
                    if time_range == "昨天":
                        target_date = datetime.now() - timedelta(days=1)
                    else:
                        target_date = datetime.now()

                    # 生成图片并获取临时文件路径
                    img_path = await SummaryImageGenerator.generate_summary_image(
                        title=title,
                        summary_text=summary,
                        time_info=target_date.strftime("%Y-%m-%d"),
                        message_count=len(messages),
                        participant_count=participant_count,
                        topics=topics,
                        user_titles=user_titles,
                        golden_quotes=golden_quotes,
                        depression_index=depression_index,
                        hourly_distribution=hourly_distribution,
                        user_profile=None,
                        group_id=str(group_id_int),  # 添加群号用于标识和清理旧图片
                        display_order=display_order,
                        target_date=target_date
                    )

                    # 发送图片
                    try:
                        if not os.path.exists(img_path):
                            raise FileNotFoundError(f"图片文件不存在: {img_path}")

                        with open(img_path, 'rb') as f:
                            img_data = f.read()

                        import base64
                        img_base64 = base64.b64encode(img_data).decode('utf-8')
                        await self.send_custom("image", img_base64)
                        await asyncio.sleep(2)
                    finally:
                        try:
                            if os.path.exists(img_path):
                                os.remove(img_path)
                        except Exception as e:
                            logger.warning(f"清理临时图片失败: {e}")

                except Exception as e:
                    logger.error(f"生成图片失败，使用文本输出: {e}", exc_info=True)
                    # 降级到文本输出
                    await self.send_text(summary)

                return True, "已生成聊天记录总结", True
            else:
                await self.send_text("生成总结失败了，等会再试试吧")
                return False, "生成总结失败", False

        except Exception as e:
            logger.error(f"执行聊天记录总结命令时出错: {e}", exc_info=True)
            await self.send_text(f"出错了: {str(e)}")
            return False, f"执行命令时出错: {str(e)}", False

    def _parse_time_range(self, time_range: str) -> Tuple[Optional[float], Optional[float]]:
        """解析时间范围

        Args:
            time_range: 时间范围字符串

        Returns:
            (start_time, end_time) 时间戳元组，失败返回 (None, None)
        """
        now = datetime.now()
        today_start = datetime(now.year, now.month, now.day)

        try:
            if time_range == "今天" or time_range == "":
                start_time = today_start
                end_time = now
            elif time_range == "昨天":
                start_time = today_start - timedelta(days=1)
                end_time = today_start
            else:
                # 不支持的时间范围
                return None, None

            return start_time.timestamp(), end_time.timestamp()

        except Exception as e:
            logger.error(f"解析时间范围出错: {e}")
            return None, None

    async def _get_messages(
        self, start_time: float, end_time: float
    ) -> List[dict]:
        """获取聊天记录

        Args:
            start_time: 起始时间戳
            end_time: 结束时间戳

        Returns:
            聊天记录列表
        """
        try:
            # 获取当前聊天ID
            if not self.message.chat_stream:
                logger.error("chat_stream 为空")
                return []

            chat_id = self.message.chat_stream.stream_id

            # 查询消息
            # 注意：由于peewee的限制，我们需要分两步查询
            # 1. 先查询所有符合chat_id和时间范围的消息
            all_messages = await database_api.db_query(
                Messages,
                query_type="get",
                filters={"chat_id": chat_id},
                order_by=["-time"],
            )

            # 检查查询结果 - db_query 可能返回 None 或空列表
            if not all_messages or all_messages is None:
                return []

            # 2. 在内存中过滤时间范围和用户
            filtered_messages = []

            for msg in all_messages:
                # 检查时间范围
                msg_time = msg.get("time", 0)
                if not (start_time <= msg_time < end_time):
                    continue

                # 检查是否为命令或通知（排除这些消息）
                if msg.get("is_command") or msg.get("is_notify"):
                    continue

                filtered_messages.append(msg)

            # 按时间正序排序（旧到新）
            filtered_messages.sort(key=lambda x: x.get("time", 0))

            return filtered_messages

        except Exception as e:
            logger.error(f"获取聊天记录出错: {e}", exc_info=True)
            return []

    async def _generate_summary(
        self, messages: List[dict], time_range: str
    ) -> Optional[str]:
        """生成聊天记录总结

        Args:
            messages: 聊天记录列表
            time_range: 时间范围描述

        Returns:
            总结文本，失败返回None
        """
        try:
            # 构建聊天记录文本
            chat_text = ChatAnalysisUtils.format_messages(messages)

            # 获取人设和回复风格
            from src.config.config import global_config

            bot_name = global_config.bot.nickname
            personality = global_config.personality.personality
            reply_style = global_config.personality.reply_style

            # 统计参与用户
            participants = set()
            for msg in messages:
                nickname = msg.get("user_nickname", "")
                if nickname:
                    participants.add(nickname)

            # 构建提示词
            prompt = f"""你是{bot_name}。{personality}
{reply_style}

以下是群聊记录（{len(messages)}条消息，{len(participants)}人参与）：
{chat_text}

请像给朋友讲故事一样复述群里发生了什么。

要求：
1. 按时间顺序讲，保持连贯性
2. 精彩内容详细说，平淡内容略过
3. 对话要说清谁说了什么、谁怎么回的
4. 必须有具体人名和具体内容，不要抽象描述
5. 口语化，不要用"首先""其次""然后""总之"这类词

直接开始，不要标题。"""

            # 使用LLM生成总结
            # 使用主回复模型 (replyer)
            model_task_config = model_config.model_task_config.replyer

            success, summary, reasoning, model_name = await llm_api.generate_with_model(
                prompt=prompt,
                model_config=model_task_config,
                request_type="plugin.chat_summary",
            )

            if not success:
                logger.error(f"LLM生成总结失败: {summary}")
                return None

            # 返回总结内容
            return summary.strip()

        except Exception as e:
            logger.error(f"生成聊天记录总结出错: {e}", exc_info=True)
            return None


class SummaryScheduler:
    """聊天总结定时任务调度器

    负责管理每日自动总结的定时任务，采用精确计算等待时间的方式，
    避免轮询检查，提高效率并减少资源消耗。
    """

    def __init__(self, config_getter):
        """初始化调度器

        Args:
            config_getter: 配置获取函数
        """
        self.get_config = config_getter
        self.is_running = False
        self.task = None
        self.last_execution_date = None

    def _get_timezone_now(self):
        """获取配置时区的当前时间"""
        timezone_str = self.get_config("auto_summary.timezone", "Asia/Shanghai")
        try:
            import pytz
            tz = pytz.timezone(timezone_str)
            return datetime.now(tz)
        except ImportError:
            logger.warning("pytz模块未安装，使用系统时间")
            return datetime.now()
        except Exception as e:
            logger.warning(f"时区处理出错: {e}，使用系统时间")
            return datetime.now()

    async def start(self, summary_generator):
        """启动定时任务

        Args:
            summary_generator: 总结生成协程函数
        """
        if self.is_running:
            return

        enabled = self.get_config("plugin.enabled", True)
        auto_summary_enabled = self.get_config("auto_summary.enabled", False)

        if not enabled or not auto_summary_enabled:
            return

        self.is_running = True
        self.task = asyncio.create_task(self._schedule_loop(summary_generator))

        summary_time = self.get_config("auto_summary.time", "23:00")
        target_chats = self.get_config("auto_summary.target_chats", [])

        if target_chats:
            logger.info(f"✅ 定时任务已启动 - 执行时间: {summary_time}, 目标群聊: {len(target_chats)}个")
        else:
            logger.info(f"✅ 定时任务已启动 - 执行时间: {summary_time}, 目标: 所有群聊")

    async def stop(self):
        """停止定时任务"""
        if not self.is_running:
            return

        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("定时任务已停止")

    async def _schedule_loop(self, summary_generator):
        """定时任务循环

        Args:
            summary_generator: 总结生成协程函数
        """
        while self.is_running:
            try:
                now = self._get_timezone_now()
                summary_time_str = self.get_config("auto_summary.time", "23:00")

                # 解析执行时间
                try:
                    hour, minute = map(int, summary_time_str.split(":"))
                except ValueError:
                    logger.error(f"无效的时间格式: {summary_time_str}，使用默认值 23:00")
                    hour, minute = 23, 0

                # 计算今天的执行时间点
                today_schedule = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                # 如果今天的时间点已过，则计算明天的时间点
                if now >= today_schedule:
                    today_schedule += timedelta(days=1)

                # 计算等待秒数
                wait_seconds = (today_schedule - now).total_seconds()
                logger.info(f"⏰ 下次总结生成时间: {today_schedule.strftime('%Y-%m-%d %H:%M:%S')} (等待 {int(wait_seconds/3600)}小时{int((wait_seconds%3600)/60)}分钟)")

                # 等待到执行时间
                await asyncio.sleep(wait_seconds)

                # 检查是否还在运行
                if not self.is_running:
                    break

                # 检查今天是否已执行（避免重复）
                current_date = self._get_timezone_now().date()
                if self.last_execution_date == current_date:
                    continue

                # 执行总结生成
                logger.info(f"⏰ 开始执行每日自动总结 - {current_date}")
                await summary_generator()
                self.last_execution_date = current_date
                logger.info("✅ 每日自动总结执行完成")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 定时任务执行出错: {e}", exc_info=True)
                # 出错后等待1分钟再重试
                await asyncio.sleep(60)


class UserSummaryCommand(BaseCommand):
    """个人用户总结命令"""

    command_name = "user_summary"
    command_description = "生成个人聊天总结"
    command_pattern = r"^/mysummary\s*(.*)$"

    async def execute(self) -> Tuple[bool, str, bool]:
        """执行个人用户总结"""
        try:
            # ===== 权限检查（复用群聊总结的权限逻辑）=====
            if not self.message.chat_stream:
                logger.error("chat_stream 为空，无法进行权限检查")
                return False, "chat_stream为空", False

            if not self.message.chat_stream.group_info:
                logger.debug("这不是群聊消息，跳过")
                return False, "非群聊消息", False

            group_id = self.message.chat_stream.group_info.group_id

            # 读取配置
            use_blacklist = self.get_config("command_permission.use_blacklist", True)
            target_chats = self.get_config("command_permission.target_chats", [])

            if target_chats and isinstance(target_chats, list):
                target_chats = [int(chat_id) if isinstance(chat_id, str) else chat_id for chat_id in target_chats]

            try:
                group_id_int = int(group_id)
            except (ValueError, TypeError):
                logger.error(f"无效的 group_id: {group_id}")
                return False, "无效的群号", False

            if use_blacklist:
                if group_id_int in target_chats:
                    logger.debug(f"群聊 {group_id_int} 在黑名单中，静默跳过 /mysummary 命令")
                    return False, "权限不足", False
            else:
                if target_chats and group_id_int not in target_chats:
                    logger.debug(f"群聊 {group_id_int} 不在白名单中，静默跳过 /mysummary 命令")
                    return False, "权限不足", False

            # ===== /mysummary 独立权限检查 =====
            # 检查功能开关
            mysummary_enabled = self.get_config("user_summary.enabled", True)
            if not mysummary_enabled:
                logger.debug("/mysummary 功能已关闭，静默跳过")
                return False, "功能已关闭", False

            # ===== 获取当前用户信息 =====
            current_user_id = str(self.message.message_info.user_info.user_id)
            current_user_nickname = self.message.message_info.user_info.user_nickname or "未知用户"

            # 获取 allowed_users 列表（用于后续判断查看他人权限）
            allowed_users = self.get_config("user_summary.allowed_users", [])
            if allowed_users:
                allowed_users = [int(uid) if isinstance(uid, str) else uid for uid in allowed_users]
            try:
                current_user_id_int = int(current_user_id)
            except (ValueError, TypeError):
                current_user_id_int = 0

            # ===== 解析参数 =====
            match = re.match(self.command_pattern, self.message.raw_message)
            if not match:
                await self.send_text("用法: /mysummary [今天|昨天] 或 /mysummary @某人 [今天|昨天]")
                return True, "已发送使用说明", True

            args = match.group(1).strip()

            # ===== 检查是否指定了目标用户（@某人 或 QQ号）=====
            target_user_id = None
            target_user_name = None
            time_range = "今天"

            # 1. 处理 CQ 码格式的 at，例如: [CQ:at,qq=123456]
            cq_at_match = re.search(r'\[CQ:at,qq=(\d+)\]', args)
            # 2. 匹配 @<昵称:QQ号> 格式（MaiBot 内部消息格式）
            at_match = re.search(r'@<([^:<>]+):(\d+)>', args)
            # 3. 匹配 @用户名 格式（简单格式）
            simple_at_match = re.search(r'^@(\S+)', args)

            if cq_at_match:
                # CQ 码格式
                target_user_id = cq_at_match.group(1)
                # 移除CQ码，剩下的是时间参数
                remaining_args = re.sub(r'\[CQ:at,qq=\d+\]\s*', '', args).strip()
                time_range = remaining_args if remaining_args in ["今天", "昨天"] else "今天"
            elif at_match:
                # @<昵称:QQ号> 格式
                target_user_name = at_match.group(1)
                target_user_id = at_match.group(2)
                # 移除@部分，剩下的是时间参数
                remaining_args = re.sub(r'@<[^:<>]+:\d+>\s*', '', args).strip()
                time_range = remaining_args if remaining_args in ["今天", "昨天"] else "今天"
            elif simple_at_match:
                # @用户名 格式 - 可能是昵称或QQ号
                at_value = simple_at_match.group(1)
                parts = args.split(maxsplit=1)
                if at_value.isdigit():
                    # @后面是纯数字，当作QQ号
                    target_user_id = at_value
                else:
                    # @后面是昵称，需要从消息记录中查找
                    target_user_name = at_value
                # 移除@部分，剩下的是时间参数
                remaining_args = args[len(parts[0]):].strip() if len(parts) > 0 else ""
                time_range = remaining_args if remaining_args in ["今天", "昨天"] else "今天"
            else:
                # 检查是否为纯数字（QQ号）
                parts = args.split(maxsplit=1)
                if parts and parts[0].isdigit():
                    target_user_id = parts[0]
                    time_range = parts[1] if len(parts) > 1 else "今天"
                elif args in ["今天", "昨天", ""]:
                    # 没有指定目标用户，查看自己
                    time_range = args if args else "今天"
                else:
                    # 其他情况默认今天
                    time_range = "今天"

            # ===== 如果只有昵称没有QQ号，需要先获取消息记录来查找 =====
            if target_user_name and not target_user_id:
                # 先获取时间范围
                temp_start_time, temp_end_time = self._parse_time_range(time_range)
                if temp_start_time and temp_end_time:
                    # 获取消息记录
                    temp_messages = await self._get_messages(temp_start_time, temp_end_time)
                    # 从消息记录中查找匹配昵称的用户
                    for msg in temp_messages:
                        msg_nickname = msg.get("user_nickname", "")
                        msg_cardname = msg.get("user_cardname", "")
                        if target_user_name in [msg_nickname, msg_cardname]:
                            target_user_id = str(msg.get("user_id", ""))
                            break

                if not target_user_id:
                    await self.send_text(f"找不到用户 {target_user_name} 的发言记录")
                    return True, "找不到目标用户", True

            # ===== 设置目标用户 =====
            # 权限逻辑：
            # - 开关关闭：所有人都不能用（已在上面检查）
            # - 开关开启 + allowed_users 为空：所有人可以看自己和别人
            # - 开关开启 + allowed_users 有值：所有人可以看自己，但只有列表中的人可以看别人
            if target_user_id and target_user_id != current_user_id:
                # 尝试查看他人的总结
                if allowed_users and current_user_id_int not in allowed_users:
                    # 列表有值且当前用户不在列表中，不能查看他人
                    logger.debug(f"用户 {current_user_id} 不在 allowed_users 列表中，无法查看他人总结，静默跳过")
                    return False, "权限不足", False

                # 使用目标用户
                user_id = target_user_id
                # 如果没有从@中获取名字，尝试从消息记录中查找
                if not target_user_name:
                    target_user_name = f"用户{target_user_id}"
                user_name = target_user_name
            else:
                # 查看自己
                user_id = current_user_id
                user_cardname = self.message.message_info.user_info.user_cardname or ""
                user_name = user_cardname if user_cardname else current_user_nickname

            if not time_range:
                time_range = "今天"

            # 获取时间范围
            start_time, end_time = self._parse_time_range(time_range)
            if start_time is None or end_time is None:
                await self.send_text(f"只支持查询今天或昨天的记录哦")
                return False, f"不支持的时间范围: {time_range}", False

            # ===== 获取聊天记录 =====
            all_messages = await self._get_messages(start_time, end_time)

            if not all_messages:
                await self.send_text(f"{time_range}群里没有聊天记录呢")
                return True, "没有聊天记录", True

            # ===== 过滤出目标用户的消息（关键：只使用该用户的消息）=====
            user_messages = ChatAnalysisUtils.filter_user_messages(all_messages, user_id)

            # 尝试从消息记录中获取用户名（如果之前没有获取到）
            if target_user_id and user_messages:
                first_msg = user_messages[0]
                msg_cardname = first_msg.get("user_cardname", "")
                msg_nickname = first_msg.get("user_nickname", "")
                if msg_cardname:
                    user_name = msg_cardname
                elif msg_nickname:
                    user_name = msg_nickname

            is_self = (user_id == current_user_id)

            if not user_messages:
                if is_self:
                    await self.send_text(f"{time_range}你没有发言记录呢，多说说话吧~")
                else:
                    await self.send_text(f"{time_range}{user_name}没有发言记录呢~")
                return True, "用户没有发言记录", True

            if len(user_messages) < 3:
                if is_self:
                    await self.send_text(f"{time_range}你只发了{len(user_messages)}条消息，发言太少啦，多聊聊天再来总结吧~")
                else:
                    await self.send_text(f"{time_range}{user_name}只发了{len(user_messages)}条消息，发言太少无法生成总结~")
                return True, "用户发言太少", True

            # 发送等候提示
            await self.send_text(f"⏳ 正在分析{user_name}的{time_range}发言记录，请稍候...")

            # ===== 分析用户数据（只使用该用户的消息）=====
            # 统计数据
            user_stats = ChatAnalysisUtils.analyze_single_user_stats(user_messages)

            # AI总结（只使用该用户的消息）
            summary_text = await ChatAnalysisUtils.analyze_single_user_summary(
                user_messages, user_name, user_id
            )

            # 群友画像（只使用该用户的消息）
            portrait_data = await ChatAnalysisUtils.analyze_single_user_portrait(
                user_messages, user_name, user_id
            )

            # 炫压抑评级（只使用该用户的消息）
            depression_data = await ChatAnalysisUtils.analyze_single_user_depression(
                user_messages, user_name, user_id
            )

            # 金句（只使用该用户的消息）
            golden_quotes = await ChatAnalysisUtils.analyze_single_user_quotes(
                user_messages, user_name, user_id
            )

            # ===== 获取配置的显示顺序 =====
            display_order = self.get_config(
                "user_summary.display_order",
                ["3H", "Portraits,Rankings"]
            )

            # 计算目标日期
            if time_range == "昨天":
                target_date = datetime.now() - timedelta(days=1)
            else:
                target_date = datetime.now()

            # ===== 生成图片 =====
            try:
                img_path = await SummaryImageGenerator.generate_user_summary_image(
                    user_name=user_name,
                    user_id=user_id,
                    summary_text=summary_text or "",
                    message_count=user_stats["message_count"],
                    total_characters=user_stats["char_count"],
                    emoji_count=user_stats["emoji_count"],
                    hourly_distribution=user_stats["hourly_distribution"],
                    user_title=portrait_data.get("title", "") if portrait_data else "",
                    user_mbti=portrait_data.get("mbti", "") if portrait_data else "",
                    portrait_data=portrait_data,
                    depression_data=depression_data,
                    golden_quotes=golden_quotes,
                    display_order=display_order,
                    target_date=target_date
                )

                # 发送图片（和群聊总结保持一致的发送方式）
                try:
                    if not os.path.exists(img_path):
                        raise FileNotFoundError(f"图片文件不存在: {img_path}")

                    with open(img_path, 'rb') as f:
                        img_data = f.read()

                    import base64
                    img_base64 = base64.b64encode(img_data).decode('utf-8')
                    await self.send_custom("image", img_base64)
                    logger.info(f"成功发送个人总结图片: {img_path}")
                    await asyncio.sleep(2)
                finally:
                    try:
                        if os.path.exists(img_path):
                            os.remove(img_path)
                    except Exception as e:
                        logger.warning(f"清理临时图片失败: {e}")

                return True, "成功生成个人总结", True

            except Exception as e:
                logger.error(f"生成个人总结图片失败: {e}", exc_info=True)
                # 如果图片生成失败，发送文字版本
                if summary_text:
                    await self.send_text(f"📊 {user_name}的{time_range}总结\n\n{summary_text}")
                    return True, "发送文字版总结", True
                else:
                    await self.send_text("生成总结失败了，请稍后再试~")
                    return False, "生成失败", False

        except Exception as e:
            logger.error(f"执行个人总结命令出错: {e}", exc_info=True)
            await self.send_text("生成总结时出错了，请稍后再试~")
            return False, str(e), False

    def _parse_time_range(self, time_range: str) -> Tuple[Optional[float], Optional[float]]:
        """解析时间范围"""
        now = datetime.now()
        today_start = datetime(now.year, now.month, now.day)

        try:
            if time_range == "今天" or time_range == "":
                start_time = today_start
                end_time = now
            elif time_range == "昨天":
                start_time = today_start - timedelta(days=1)
                end_time = today_start
            else:
                return None, None

            return start_time.timestamp(), end_time.timestamp()

        except Exception as e:
            logger.error(f"解析时间范围出错: {e}")
            return None, None

    async def _get_messages(
        self, start_time: float, end_time: float
    ) -> List[dict]:
        """获取聊天记录"""
        try:
            if not self.message.chat_stream:
                logger.error("chat_stream 为空")
                return []

            chat_id = self.message.chat_stream.stream_id

            all_messages = await database_api.db_query(
                Messages,
                query_type="get",
                filters={"chat_id": chat_id},
                order_by=["-time"],
            )

            if not all_messages or all_messages is None:
                return []

            filtered_messages = []

            for msg in all_messages:
                msg_time = msg.get("time", 0)
                if not (start_time <= msg_time < end_time):
                    continue

                if msg.get("is_command") or msg.get("is_notify"):
                    continue

                filtered_messages.append(msg)

            filtered_messages.sort(key=lambda x: x.get("time", 0))

            return filtered_messages

        except Exception as e:
            logger.error(f"获取聊天记录出错: {e}", exc_info=True)
            return []


class DailySummaryEventHandler(BaseEventHandler):
    """每日自动总结事件处理器"""

    event_type = EventType.ON_START
    handler_name = "daily_summary_handler"
    handler_description = "每日定时自动生成群聊总结"
    weight = 10
    intercept_message = False

    # 类变量：确保只启动一个调度器
    _scheduler = None
    _scheduler_started = False

    def __init__(self):
        super().__init__()

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, Optional[str], Optional[any], Optional[MaiMessages]]:
        """执行事件处理"""
        # 确保只启动一个调度器实例
        if not DailySummaryEventHandler._scheduler_started:
            DailySummaryEventHandler._scheduler_started = True
            DailySummaryEventHandler._scheduler = SummaryScheduler(self.get_config)
            await DailySummaryEventHandler._scheduler.start(self._generate_daily_summaries)

        return True, True, None, None, None

    async def _generate_daily_summaries(self):
        """为所有群聊生成今日总结"""
        try:
            # 计算今天的时间范围
            now = datetime.now()
            today_start = datetime(now.year, now.month, now.day)
            start_time = today_start.timestamp()
            end_time = now.timestamp()

            # 获取今天有消息的所有群聊ID
            all_messages = await database_api.db_query(
                Messages,
                query_type="get",
                filters={},
                order_by=["-time"],
            )

            if not all_messages:
                return

            # 提取唯一的 chat_id 并建立 chat_id -> group_id 的映射
            chat_id_to_group_id = {}
            today_message_count = 0

            for msg in all_messages:
                msg_time = msg.get("time", 0)
                if start_time <= msg_time < end_time:
                    today_message_count += 1
                    chat_id = msg.get("chat_id")
                    group_id = msg.get("chat_info_group_id")

                    if chat_id and chat_id not in chat_id_to_group_id:
                        chat_id_to_group_id[chat_id] = group_id

            if not chat_id_to_group_id:
                return

            # 获取配置
            target_chats = self.get_config("auto_summary.target_chats", [])
            min_messages = self.get_config("auto_summary.min_messages", 10)

            # 确保 target_chats 是整数列表（WebUI 可能发送字符串列表）
            if target_chats and isinstance(target_chats, list):
                target_chats = [int(chat_id) if isinstance(chat_id, str) else chat_id for chat_id in target_chats]

            # 过滤目标群聊（使用实际的 group_id 进行匹配）
            if target_chats:
                target_group_ids = set(str(gid) for gid in target_chats)
                filtered_chat_ids = {}

                for chat_id, group_id in chat_id_to_group_id.items():
                    if str(group_id) in target_group_ids:
                        filtered_chat_ids[chat_id] = group_id

                chat_id_to_group_id = filtered_chat_ids

            # 为每个群聊生成总结
            for chat_id, group_id in chat_id_to_group_id.items():
                try:
                    # 获取今天的聊天记录
                    messages = await self._get_messages_for_chat(
                        chat_id, start_time, end_time
                    )

                    # 检查消息数量是否达到最小要求
                    if len(messages) < min_messages:
                        continue

                    # 生成总结
                    summary = await self._generate_summary_for_chat(messages)

                    if summary:
                        # 生成并发送图片
                        try:
                            # 统计参与用户
                            participants = set()
                            for msg in messages:
                                nickname = msg.get("user_nickname", "")
                                if nickname:
                                    participants.add(nickname)

                            # 分析用户统计
                            user_stats = ChatAnalysisUtils.analyze_user_stats(messages)
                            user_titles = []
                            golden_quotes = []
                            topics = []

                            # 计算24小时发言分布
                            from collections import Counter
                            hourly_distribution = Counter()
                            for msg in messages:
                                timestamp = msg.get("time", 0)
                                hour = datetime.fromtimestamp(timestamp).hour
                                hourly_distribution[hour] += 1
                            # 转换为普通字典
                            hourly_distribution = dict(hourly_distribution)

                            # 始终分析所有数据，由 display_order 控制显示
                            topics = await ChatAnalysisUtils.analyze_topics(messages) or []
                            user_titles = await ChatAnalysisUtils.analyze_user_titles(messages, user_stats) or []
                            golden_quotes = await ChatAnalysisUtils.analyze_golden_quotes(messages) or []
                            depression_index = await ChatAnalysisUtils.analyze_depression_index(messages, user_stats) or []

                            # 为 user_titles 添加头像数据
                            if user_titles:
                                for title_item in user_titles:
                                    user_id = title_item.get("user_id", "")
                                    if user_id:
                                        # QQ头像URL格式
                                        title_item["avatar_data"] = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100"
                                    else:
                                        title_item["avatar_data"] = ""

                            # 获取显示顺序配置
                            display_order = self.get_config("summary.display_order", ["24H", "Topics", "Portraits", "Quotes", "Rankings"])

                            # 自动总结使用今天的日期
                            target_date = datetime.now()

                            # 生成图片并获取临时文件路径
                            img_path = await SummaryImageGenerator.generate_summary_image(
                                title="📊 今日群聊总结",
                                summary_text=summary,
                                time_info=target_date.strftime("%Y-%m-%d"),
                                message_count=len(messages),
                                participant_count=len(participants),
                                topics=topics,
                                user_titles=user_titles,
                                golden_quotes=golden_quotes,
                                depression_index=depression_index,
                                hourly_distribution=hourly_distribution,
                                group_id=str(group_id),  # 添加群号用于标识和清理旧图片
                                display_order=display_order,
                                target_date=target_date
                            )

                            # 发送图片
                            try:
                                if not os.path.exists(img_path):
                                    raise FileNotFoundError(f"图片文件不存在: {img_path}")

                                with open(img_path, 'rb') as f:
                                    img_data = f.read()

                                import base64
                                img_base64 = base64.b64encode(img_data).decode('utf-8')
                                await send_api.image_to_stream(img_base64, chat_id, storage_message=False)
                                await asyncio.sleep(2)
                            finally:
                                try:
                                    if os.path.exists(img_path):
                                        os.remove(img_path)
                                except Exception as e:
                                    logger.warning(f"清理临时图片失败: {e}")

                        except Exception as e:
                            logger.error(f"生成图片失败，使用文本输出: {e}", exc_info=True)
                            # 降级到文本输出
                            prefix = "📊 今日群聊总结\n\n"
                            await send_api.text_to_stream(prefix + summary, chat_id, storage_message=False)
                    else:
                        logger.warning(f"群聊 {group_id} 总结生成失败")

                except Exception as e:
                    logger.error(f"为群聊 {group_id} 生成总结失败: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"生成每日总结失败: {e}", exc_info=True)

    async def _get_messages_for_chat(
        self, chat_id: str, start_time: float, end_time: float
    ) -> List[dict]:
        """获取指定群聊的聊天记录"""
        try:
            # 查询消息
            all_messages = await database_api.db_query(
                Messages,
                query_type="get",
                filters={"chat_id": chat_id},
                order_by=["-time"],
            )

            if not all_messages:
                return []

            # 过滤时间范围和消息类型
            filtered_messages = []
            for msg in all_messages:
                msg_time = msg.get("time", 0)
                if not (start_time <= msg_time < end_time):
                    continue

                # 排除命令和通知
                if msg.get("is_command") or msg.get("is_notify"):
                    continue

                filtered_messages.append(msg)

            # 按时间正序排序
            filtered_messages.sort(key=lambda x: x.get("time", 0))
            return filtered_messages

        except Exception as e:
            logger.error(f"获取群聊 {chat_id} 的聊天记录出错: {e}", exc_info=True)
            return []

    async def _generate_summary_for_chat(self, messages: List[dict]) -> Optional[str]:
        """为指定聊天记录生成总结"""
        try:
            # 构建聊天记录文本
            chat_text = ChatAnalysisUtils.format_messages(messages)

            # 获取人设和回复风格
            from src.config.config import global_config
            bot_name = global_config.bot.nickname
            personality = global_config.personality.personality
            reply_style = global_config.personality.reply_style

            # 统计参与用户
            participants = set()
            for msg in messages:
                nickname = msg.get("user_nickname", "")
                if nickname:
                    participants.add(nickname)

            # 构建提示词
            prompt = f"""你是{bot_name}。{personality}
{reply_style}

以下是群聊记录（{len(messages)}条消息，{len(participants)}人参与）：
{chat_text}

请像给朋友讲故事一样复述群里发生了什么。

要求：
1. 按时间顺序讲，保持连贯性
2. 精彩内容详细说，平淡内容略过
3. 对话要说清谁说了什么、谁怎么回的
4. 必须有具体人名和具体内容，不要抽象描述
5. 口语化，不要用"首先""其次""然后""总之"这类词

直接开始，不要标题。"""

            # 使用LLM生成总结
            model_task_config = model_config.model_task_config.replyer

            success, summary, reasoning, model_name = await llm_api.generate_with_model(
                prompt=prompt,
                model_config=model_task_config,
                request_type="plugin.chat_summary.auto",
            )

            if not success:
                logger.error(f"LLM生成自动总结失败: {summary}")
                return None

            return summary.strip()

        except Exception as e:
            logger.error(f"生成聊天总结出错: {e}", exc_info=True)
            return None


@register_plugin
class ChatSummaryPlugin(BasePlugin):
    """聊天记录总结插件"""

    plugin_name: str = "chat_summary_plugin"
    enable_plugin: bool = False
    dependencies: List[str] = []
    python_dependencies: List[str] = []
    config_file_name: str = "config.toml"

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件基本信息",
        "summary": "群聊总结功能配置",
        "user_summary": "个人总结功能配置",
        "auto_summary": "自动总结配置",
        "command_permission": "命令权限控制",
    }

    # 配置Schema定义
    config_schema: dict = {
        "plugin": {
            "config_version": ConfigField(type=str, default="1.0.0", description="配置文件版本"),
            "enabled": ConfigField(type=bool, default=False, description="是否启用插件"),
        },
        "summary": {
            "display_order": ConfigField(
                type=list,
                default=["24H", "Topics", "Portraits", "Quotes", "Rankings"],
                description="图片模块显示顺序（可选项：24H=24H活跃轨迹, Topics=今日话题, Portraits=群友画像, Quotes=语出惊人, Rankings=炫压抑评级。列表中的模块会按顺序显示，不在列表中的模块不显示）",
            ),
        },
        "user_summary": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用个人总结功能（关闭后所有人都无法使用/mysummary命令）"),
            "allowed_users": ConfigField(
                type=list,
                default=[],
                description="允许查看他人总结的用户QQ号列表（为空时所有人可以看自己和别人；有值时所有人可以看自己，但只有列表中的用户可以查看他人总结）",
            ),
            "display_order": ConfigField(
                type=list,
                default=["3H", "Portraits,Rankings"],
                description="个人总结图片模块显示顺序（可选项：3H=3H活跃轨迹, Portraits=群友画像, Rankings=炫压抑评级, Quotes=语出惊人。用逗号分隔的模块会横向排列，如'Portraits,Rankings'表示画像和评级并排显示）",
            ),
        },
        "auto_summary": {
            "enabled": ConfigField(type=bool, default=False, description="是否启用每日自动总结"),
            "time": ConfigField(type=str, default="23:00", description="每日自动总结的时间（HH:MM格式）"),
            "timezone": ConfigField(type=str, default="Asia/Shanghai", description="时区设置（需安装pytz模块）"),
            "min_messages": ConfigField(type=int, default=10, description="生成总结所需的最少消息数量"),
            "target_chats": ConfigField(type=list, default=[], description="目标群聊QQ号列表（为空则对所有群聊生效）"),
        },
        "command_permission": {
            "use_blacklist": ConfigField(
                type=bool,
                default=True,
                description="使用黑名单模式（开启：黑名单模式-列表中的群不能使用命令；关闭：白名单模式-只有列表中的群可以使用命令）",
            ),
            "target_chats": ConfigField(
                type=list,
                default=[],
                description="目标群聊列表（黑名单模式：这些群不能使用；白名单模式：只有这些群可以使用；为空时：黑名单允许所有群，白名单禁用所有群）",
            ),
            "admin_users": ConfigField(
                type=list,
                default=[],
                description="管理员QQ号列表，仅控制/summary命令（为空时所有人可用；有值时只有列表中的用户可以使用/summary命令）",
            ),
        },
    }

    def get_plugin_components(self) -> List[Tuple]:
        return [
            (ChatSummaryCommand.get_command_info(), ChatSummaryCommand),
            (UserSummaryCommand.get_command_info(), UserSummaryCommand),
            (DailySummaryEventHandler.get_handler_info(), DailySummaryEventHandler),
        ]
