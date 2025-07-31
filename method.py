import aiohttp
import os
import aiofiles
from PIL import Image
import logging

# 创建临时缓存文件夹
TEMP_DIR = os.path.join(os.path.dirname(__file__), "tmp")
os.makedirs(TEMP_DIR, exist_ok=True)

logger = logging.getLogger("bangumi.method")

async def get_img_changeFormat(url: str, save_dir: str, output_format: str = "jpeg", 
                              ssl: bool = True) -> str:
    """下载图片并转换为指定格式"""
    if not url.startswith('http'):
        raise ValueError(f"无效的图片URL: {url}")
    
    filename = os.path.basename(url.split('?')[0])  # 移除查询参数
    filepath = os.path.join(save_dir, f"temp_{filename}")
    output_path = os.path.join(save_dir, f"{os.path.splitext(filename)[0]}.{output_format}")
    
    try:
        # 下载图片
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=ssl) as response:
                if response.status != 200:
                    raise Exception(f"图片下载失败: HTTP {response.status}")
                
                async with aiofiles.open(filepath, "wb") as f:
                    await f.write(await response.read())
        
        # 转换格式
        with Image.open(filepath) as img:
            if output_format.lower() == "jpeg":
                img = img.convert("RGB")
            img.save(output_path, format=output_format.upper(), quality=85)
        
        return output_path
        
    except Exception as e:
        logger.error(f"图片处理错误: {str(e)}")
        raise
    finally:
        # 清理临时文件
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass