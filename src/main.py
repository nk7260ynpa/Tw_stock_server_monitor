"""台股伺服器監控 - 主程式。"""

import logging
import sys

from src.logger import setup_logger


def main():
    """主程式進入點。"""
    logger = setup_logger()
    logger.info("台股伺服器監控啟動")

    # TODO: 實作監控邏輯

    logger.info("台股伺服器監控結束")


if __name__ == "__main__":
    main()
