"""
聊天总结插件 - 依赖安装脚本

自动安装 HTML 渲染所需的依赖：
- jinja2: HTML 模板引擎
- playwright: 浏览器自动化工具
- Chromium 浏览器
"""

import sys
import subprocess
import os


def print_step(step_num, total_steps, message):
    """打印步骤信息"""
    print(f"\n[{step_num}/{total_steps}] {message}")
    print("=" * 60)


def check_package_installed(package_name):
    """检查 Python 包是否已安装"""
    try:
        __import__(package_name)
        return True
    except ImportError:
        return False


def install_pip_package(package_name, version=None):
    """安装 pip 包"""
    package_spec = f"{package_name}>={version}" if version else package_name
    print(f"正在安装 {package_spec}...")

    try:
        # 使用阿里云镜像源加速安装
        subprocess.check_call(
            [
                sys.executable, "-m", "pip", "install",
                package_spec,
                "-i", "https://mirrors.aliyun.com/pypi/simple/",
                "--trusted-host", "mirrors.aliyun.com"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(f"[OK] {package_name} 安装成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] {package_name} 安装失败: {e}")
        return False


def install_playwright_browsers():
    """安装 Playwright 浏览器"""
    print("正在安装 Chromium 浏览器...")
    print("注意: 浏览器文件较大 (约 170MB)，首次安装可能需要几分钟...")

    try:
        # 直接运行，显示进度
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=False
        )
        if result.returncode == 0:
            print("[OK] Chromium 浏览器安装成功")
            return True
        else:
            print("[ERROR] Chromium 浏览器安装失败")
            return False
    except Exception as e:
        print(f"[ERROR] Chromium 浏览器安装失败: {e}")
        return False


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("  聊天总结插件 - 依赖安装程序")
    print("=" * 60)
    print("\n本脚本将自动安装以下依赖:")
    print("  1. jinja2 (>= 3.1.2) - HTML 模板引擎")
    print("  2. playwright (>= 1.48.0) - 浏览器自动化")
    print("  3. Chromium 浏览器 - 用于渲染 HTML 为图片")
    print("\n镜像源: 阿里云 (https://mirrors.aliyun.com/pypi/simple/)")

    # 步骤 1: 检查并安装 jinja2
    print_step(1, 3, "检查并安装 jinja2")
    if check_package_installed("jinja2"):
        print("[OK] jinja2 已安装")
    else:
        if not install_pip_package("jinja2", "3.1.2"):
            print("\n安装失败! 请手动运行: pip install jinja2>=3.1.2")
            return False

    # 步骤 2: 检查并安装 playwright
    print_step(2, 3, "检查并安装 playwright")
    if check_package_installed("playwright"):
        print("[OK] playwright 已安装")
    else:
        if not install_pip_package("playwright", "1.48.0"):
            print("\n安装失败! 请手动运行: pip install playwright>=1.48.0")
            return False

    # 步骤 3: 安装 Chromium 浏览器
    print_step(3, 3, "安装 Chromium 浏览器")
    if not install_playwright_browsers():
        print("\n浏览器安装失败! 请手动运行: python -m playwright install chromium")
        return False

    # 安装完成
    print("\n" + "=" * 60)
    print("  [OK] 所有依赖安装完成!")
    print("=" * 60)
    print("\n现在可以使用聊天总结插件的 HTML 渲染功能了。")
    print()

    return True


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n安装已取消")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
