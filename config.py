# encoding:utf-8

import json
import logging
import os

from common.log import logger


config = None

def load_config():
    global config

    config_path = "./config.json"
    if not os.path.exists(config_path):
        raise Exception("配置文件不存在")

    with open(config_path, mode="r", encoding="utf-8") as f:
        config_str = f.read()
    logger.debug("[INIT] config str: {}".format(config_str))

    # 将json字符串反序列化为dict类型
    config = json.loads(config_str)

    if config.get("debug", False):
        logger.setLevel(logging.DEBUG)
        logger.debug("[INIT] set log level to DEBUG")

    logger.info("[INIT] load config")

def conf():
    return config

load_config()
