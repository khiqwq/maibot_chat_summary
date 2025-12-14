"""生成单用户总结预览图片"""
import asyncio
import os
from playwright.async_api import async_playwright


async def generate_preview():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 850, 'height': 1400})

        # 读取HTML内容
        current_dir = os.path.dirname(os.path.abspath(__file__))
        html_file = os.path.join(current_dir, 'templates', 'scrapbook', 'user_summary_preview.html')

        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # 直接设置内容，不需要加载文件
        print('设置HTML内容...')
        await page.set_content(html_content, wait_until='networkidle', timeout=60000)

        # 等待渲染
        await page.wait_for_timeout(1000)

        # 截图
        output_file = os.path.join(current_dir, 'user_summary_preview.png')
        await page.screenshot(path=output_file, full_page=True)
        await browser.close()

        print(f'OK - Preview image generated: {output_file}')


if __name__ == '__main__':
    asyncio.run(generate_preview())
