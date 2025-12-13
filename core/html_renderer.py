"""
HTML渲染服务

使用Playwright将HTML转换为图片
"""

import os
import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, Page

from src.plugin_system import get_logger

logger = get_logger("html_renderer")


class HTMLRenderer:
    """HTML渲染器 - 使用Playwright将HTML渲染为图片"""

    def __init__(self):
        """初始化渲染器"""
        self.browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self):
        """初始化Playwright浏览器"""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            try:
                playwright = await async_playwright().start()
                # 使用chromium，headless模式
                self.browser = await playwright.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox']
                )
                self._initialized = True
                logger.info("Playwright浏览器已启动")
            except Exception as e:
                logger.error(f"初始化Playwright失败: {e}", exc_info=True)
                raise

    async def close(self):
        """关闭浏览器"""
        if self.browser:
            await self.browser.close()
            self._initialized = False
            logger.info("Playwright浏览器已关闭")

    async def render_html_to_image(
        self,
        html_content: str,
        output_path: str,
        viewport_width: int = 1200,
        viewport_height: int = 800,
        full_page: bool = True,
        image_type: str = "jpeg",
        quality: int = 95,
        device_scale_factor: float = 1.0,
    ) -> bool:
        """将HTML内容渲染为图片

        Args:
            html_content: HTML内容字符串
            output_path: 输出图片路径
            viewport_width: 视口宽度
            viewport_height: 视口高度
            full_page: 是否截取整个页面（True=完整页面，False=仅视口）
            image_type: 图片类型 ("jpeg" 或 "png")
            quality: 图片质量 (0-100，仅对jpeg有效)
            device_scale_factor: 设备像素比（2.0=2倍清晰度，3.0=3倍清晰度）

        Returns:
            是否成功
        """
        if not self._initialized:
            await self.initialize()

        if not self.browser:
            logger.error("浏览器未初始化")
            return False

        page: Optional[Page] = None
        try:
            # 创建新页面，设置视口和设备像素比
            page = await self.browser.new_page(
                viewport={'width': viewport_width, 'height': viewport_height},
                device_scale_factor=device_scale_factor
            )

            # 设置HTML内容
            await page.set_content(html_content, wait_until="networkidle")

            # 等待所有字体和图片加载完成
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(0.5)  # 额外等待0.5秒确保渲染完成

            # 截图配置
            screenshot_options: Dict[str, Any] = {
                "path": output_path,
                "full_page": full_page,
                "type": image_type,
            }

            if image_type == "jpeg":
                screenshot_options["quality"] = quality

            # 截图
            await page.screenshot(**screenshot_options)

            # 验证文件生成
            if os.path.exists(output_path):
                logger.info(f"成功渲染图片: {output_path}")
                return True
            else:
                logger.error("图片文件未生成")
                return False

        except Exception as e:
            logger.error(f"渲染HTML为图片失败: {e}", exc_info=True)
            return False
        finally:
            if page:
                await page.close()


# 全局单例渲染器
_global_renderer: Optional[HTMLRenderer] = None


async def get_renderer() -> HTMLRenderer:
    """获取全局HTML渲染器实例（单例模式）

    Returns:
        HTMLRenderer实例
    """
    global _global_renderer
    if _global_renderer is None:
        _global_renderer = HTMLRenderer()
        await _global_renderer.initialize()
    return _global_renderer


async def render_html_to_image(
    html_content: str,
    output_path: str,
    **kwargs
) -> bool:
    """便捷函数：渲染HTML为图片

    Args:
        html_content: HTML内容
        output_path: 输出路径
        **kwargs: 传递给HTMLRenderer.render_html_to_image的参数

    Returns:
        是否成功
    """
    renderer = await get_renderer()
    return await renderer.render_html_to_image(html_content, output_path, **kwargs)
