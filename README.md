# AI Bot Brain - AI智能体大脑

一个功能完整的AI智能体框架，为智能体提供思考、行动、记忆等核心能力。

## 核心思路

AI Bot Brain采用模块化设计，将智能体能力抽象为几个核心系统：
- **Providers层**：各类能力提供商适配器（LLM、T2I、语音、网络工具等）
- **Tools系统**：工具系统，通过标准function call调用
- **Memory系统**：记忆管理，包括会话记忆和长期印象
- **Agent核心**：智能体思考与执行引擎，整合所有系统

## 核心模块

### 服务层
- **AgentService**: 智能体思考与执行引擎，支持工具调用和循环推理
- **PrimitivesService**: 基础能力封装，提供统一的HTTP接口
- **MemoryService**: 记忆管理服务，支持会话记忆和长期印象

### Tools
- **记忆工具**: 记忆的保存、召回、组织
- **媒体工具**: 图片生成、语音合成、二维码处理等
- **网页工具**: 网页搜索、内容抓取、短链接生成
- **工作区工具**: 文件读写、浏览器操作、计算机控制
- **技能工具**: 技能列表、技能读取
- **流程控制**: 任务等待、流程控制

### Provider适配器
- **LLM**: OpenAI、智谱AI、豆包AI
- **T2I**: 豆包AI、Google AI
- **Web Search**: 搜狗、腾讯、火山
- **语音、二维码、短链接、网页抓取、计算机控制**等

## 系统要求

- Python 3.10 及以上版本

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置
cp config.example.json config.json

# 运行服务
python app.py
```

## 生产环境配置

以下是生产环境的基础配置脚本：
- `www` 用户：用于运行 web 服务
- `bot` 用户：供智能体使用的系统环境用户

```bash
# 创建www用户（无home目录，使用nologin shell）
sudo useradd -r -s /sbin/nologin -M www

# 创建web服务目录
mkdir -p /opt/www
chmod 755 /opt/www

# 创建bot用户
sudo useradd -m -d /home/bot -s /bin/bash
# 限制bot的密码访问
sudo passwd -l bot

# 创建bot工作空间
sudo mkdir -p /opt/bot
sudo chown -R bot:bot /opt/bot

# 允许bot使用用户级别的systemctl
sudo loginctl enable-linger bot
sudo -u bot sh -c 'grep -q "XDG_RUNTIME_DIR" ~/.bashrc || echo "export XDG_RUNTIME_DIR=/run/user/$(id -u)" >> ~/.bashrc'

# 限制bot访问含极敏感信息的目录
sudo setfacl -m u:bot:--- /opt/www
sudo setfacl -m u:bot:--- /opt/local
sudo setfacl -m u:bot:--- /etc/pki/tls/private
sudo setfacl -m u:bot:--- /etc/pki/rsyslog
sudo /bin/bash -c 'setfacl -m u:bot:--- /etc/ssh/*_key'

# 让www可以访问bot的工作空间
sudo -S usermod -aG bot www
```

## 相关项目

- [前端项目](https://github.com/chenweichuan/ai-bot-web)
