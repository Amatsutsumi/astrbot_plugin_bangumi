# method.py
import aiohttp
import os
import aiofiles
from PIL import Image

# 统一从 astrbot 框架导入 logger
from astrbot.api import logger

# 临时缓存文件夹定义
TEMP_DIR = os.path.join(os.path.dirname(__file__), "tmp")
os.makedirs(TEMP_DIR, exist_ok=True)


async def get_img_changeFormat(url: str, save_dir: str, output_format: str = "jpeg",
                              ssl: bool = True) -> str:
    """
    下载图片并转换为指定格式
    :param url: 图片URL
    :param save_dir: 保存目录
    :param output_format: 输出格式 (例如 "jpeg", "png")
    :param ssl: 是否验证SSL证书 (默认为True, 保持安全连接)
    :return: 转换后图片的本地路径
    """
    if not url.startswith('http'):
        raise ValueError(f"无效的图片URL: {url}")

    filename = os.path.basename(url.split('?')[0])  # 移除查询参数
    filepath = os.path.join(save_dir, f"temp_{filename}")
    output_path = os.path.join(save_dir, f"{os.path.splitext(filename)[0]}.{output_format}")

    try:
        # 下载图片
        async with aiohttp.ClientSession() as session:
            # 使用传入的 ssl 参数，默认为 True
            async with session.get(url, ssl=ssl) as response:
                if response.status != 200:
                    raise Exception(f"图片下载失败: HTTP {response.status}")

                async with aiofiles.open(filepath, "wb") as f:
                    await f.write(await response.read())

        # 转换格式
        with Image.open(filepath) as img:
            if output_format.lower() == "jpeg":
                # 对于JPEG格式，需要转换为RGB模式以避免某些图片（如RGBA）保存时出错
                img = img.convert("RGB")
            img.save(output_path, format=output_format.upper(), quality=85)

        return output_path

    except Exception as e:
        logger.error(f"图片处理错误: {str(e)}")
        raise
    finally:
        # 清理临时下载文件
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                logger.warning(f"临时文件清理失败: {filepath} - {e}")
