# AI Bot Brain - AI智能体大脑

一个功能完整的AI智能体框架，为智能体提供思考、行动、记忆等核心能力。

## 核心思路

AI Bot Brain采用模块化设计，将智能体能力抽象为几个核心系统：
- **Providers层**：各类能力提供商适配器（LLM、T2I、语音、网络工具等）
- **Actions系统**：动作系统，通过`<action-xxx>`标签快速调用
- **Tools系统**：工具系统，通过标准function call调用
- **Memory系统**：记忆管理，包括会话记忆和长期印象
- **Agent核心**：智能体思考与执行引擎，整合所有系统

## 核心模块

### 服务层
- **AgentService**: 智能体思考与执行引擎，支持工具/动作调用和循环推理
- **PrimitivesService**: 基础能力封装，提供统一的HTTP接口
- **MemoryService**: 记忆管理服务，支持会话记忆和长期印象

### Actions & Tools
- **Actions**: 轻量级动作调用（Wait等）
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

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置
cp config.example.json config.json

# 运行服务
python app.py
```

## 相关项目

- [前端项目](https://github.com/chenweichuan/ai-bot-web)