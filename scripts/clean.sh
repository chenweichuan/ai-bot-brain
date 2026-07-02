#!/bin/sh

cd `dirname $0`
cd ..

# 从 config.json 读取 bot_os_workspace 配置，使用通用的正则匹配
TARGET_DIR=$(sed -n 's/.*"bot_os_workspace"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' config.json 2>/dev/null)

# 默认值，如果读取失败
if [ -z "$TARGET_DIR" ]; then
    echo "Error: bot_os_workspace not found in config.json"
    exit 0
fi

find ${TARGET_DIR}/desktop_screenshot -type f -mtime +1 -delete 2>/dev/null || true
find ${TARGET_DIR}/browser_screenshot -type f -mtime +1 -delete 2>/dev/null || true
