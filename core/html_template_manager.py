"""
HTML模板管理器

负责加载和渲染Jinja2 HTML模板
"""

import os
from typing import Dict, Any
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.plugin_system import get_logger

logger = get_logger("html_template_manager")


class HTMLTemplateManager:
    """HTML模板管理器 - 负责加载和渲染Jinja2模板"""

    def __init__(self, template_dir: str):
        """初始化模板管理器

        Args:
            template_dir: 模板文件夹路径（绝对路径）
        """
        self.template_dir = template_dir

        # 创建Jinja2环境
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,  # 移除块级标签后的第一个换行符
            lstrip_blocks=True,  # 移除块级标签前的空白
        )

        logger.info(f"HTML模板管理器已初始化，模板目录: {template_dir}")

    def render_template(self, template_name: str, **context: Any) -> str:
        """渲染指定模板

        Args:
            template_name: 模板文件名（相对于template_dir）
            **context: 传递给模板的上下文变量

        Returns:
            渲染后的HTML字符串
        """
        try:
            template = self.env.get_template(template_name)
            return template.render(**context)
        except Exception as e:
            logger.error(f"渲染模板 {template_name} 失败: {e}", exc_info=True)
            return ""

    def get_image_template(self) -> str:
        """获取主图片模板的原始内容

        Returns:
            主模板HTML字符串（包含{{placeholder}}占位符）
        """
        try:
            template_path = os.path.join(self.template_dir, "image_template.html")
            with open(template_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"读取主模板失败: {e}", exc_info=True)
            return ""
