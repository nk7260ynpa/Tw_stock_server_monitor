"""日誌設定模組。"""

import logging
import os
from datetime import datetime


def setup_logger(name: str = "tw_stock_monitor") -> logging.Logger:
    """設定並回傳 logger。

    Args:
        name: Logger 名稱。

    Returns:
        設定完成的 Logger 實例。
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重複加入 handler
    if logger.handlers:
        return logger

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # 檔案 handler
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_filename = datetime.now().strftime("%Y%m%d") + ".log"
    file_handler = logging.FileHandler(
        os.path.join(log_dir, log_filename), encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)

    # 格式
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
