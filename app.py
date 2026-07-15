"""
API接口 - 暴露service层的HTTP接口
"""
import json
import os
import time
from typing import Optional, List
from aiohttp import web
from aiohttp.web import StreamResponse

from common.log import logger
from common.redis import RedisClient
from common.storage import Storage
from common.tmp_dir import TmpDir
from scheduler.auto import AutoScheduler
from service.memory import MemoryService
from service.primitives import PrimitivesService
from service.agent import AgentService


# ==================== Global Services ====================

primitives_service: Optional[PrimitivesService] = None
memory_service: Optional[MemoryService] = None
agent_service: Optional[AgentService] = None


# ==================== Startup and Shutdown ====================

async def startup(app: web.Application):
    """应用启动时初始化服务"""
    global primitives_service, memory_service, agent_service
    
    logger.info("[API] Initializing services...")
    
    # 测试Redis连接
    await RedisClient.get_instance().ping()
    
    # 初始化Primitives服务
    primitives_service = PrimitivesService()
    
    # 初始化Memory服务
    memory_service = MemoryService()
    
    # 初始化Agent服务
    agent_service = AgentService()

    # 启动AutoScheduler
    AutoScheduler.setup()

    logger.info("[API] All services initialized successfully")

async def shutdown(app: web.Application):
    """应用关闭时清理资源"""
    logger.info("[API] Services shutdown completed")


# ==================== Helper Functions ====================



async def _get_post_file_path(request: web.Request) -> str:
    """获取单个上传文件的本地路径"""
    # Check for multipart file upload
    if request.content_type and 'multipart/form-data' in request.content_type:
        reader = await request.multipart()
        async for field in reader:
            if field.name == "file":
                # Read uploaded file content
                content = await field.read()
                return await TmpDir.save(content)
    
    # Check for JSON data with file URL
    try:
        data = await request.json()
        remote_url = data.get("file")
        if remote_url:
            return await TmpDir.save(remote_url)
    except:
        pass
    
    raise Exception("Upload file not found")


async def _get_post_file_paths(request: web.Request) -> List[str]:
    """
    批量获取并保存上传图片，仅支持：
    - 多文件上传：multipart/form-data中的files字段
    - JSON中的files数组
    返回本地临时路径列表。
    """
    saved_paths: List[str] = []

    def rollback():
        for p in saved_paths:
            try:
                os.remove(p)
            except Exception:
                pass

    try:
        # 1) 多文件上传
        if request.content_type and 'multipart/form-data' in request.content_type:
            reader = await request.multipart()
            async for field in reader:
                if field.name == "files":
                    # Read uploaded file content
                    content = await field.read()
                    tmp_path = await TmpDir.save(content)
                    saved_paths.append(tmp_path)
            
            if saved_paths:
                return saved_paths

        # 2) JSON: files 为数组
        try:
            data = await request.json()
            files_field = data.get("files")
            if isinstance(files_field, list) and files_field:
                for u in files_field:
                    tmp_path = await TmpDir.save(str(u))
                    saved_paths.append(tmp_path)
                return saved_paths
        except:
            pass

        # 批量接口不处理其他形式
        raise Exception("Upload files not found (expecting 'files' list or multipart files)")
    except Exception:
        rollback()
        raise


async def _get_post_data(request: web.Request) -> dict:
    """获取POST数据，排除file和files字段"""
    data = {}
    
    # Try to get from multipart form data
    if request.content_type and 'multipart/form-data' in request.content_type:
        reader = await request.multipart()
        async for field in reader:
            if field.name not in ["file", "files"]:
                content = await field.read()
                try:
                    data[field.name] = content.decode('utf-8')
                except:
                    data[field.name] = content
    else:
        # Try to get from JSON
        try:
            data = await request.json()
        except:
            pass
    
    # Remove file and files if present
    if "file" in data:
        del data["file"]
    if "files" in data:
        del data["files"]
    
    logger.info("[API] post data: {}".format(data))
    return data


# ==================== Health Check ====================

async def health_check(request: web.Request) -> web.Response:
    """健康检查接口"""
    return web.json_response(
        {"status": "ok", "message": "AI Bot Brain API is running"},
        dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
    )

# ==================== File Primitives Endpoints ====================

async def upload(request: web.Request):
    file_path = await _get_post_file_path(request)
    file_url = Storage.path_to_url(await Storage.save(file_path))
    return web.Response(text=file_url)


# ==================== Llm Primitives Endpoints ====================

async def chat(request: web.Request) -> web.Response:
    """
    直接调用LLM对话能力
    """
    try:
        data = await request.json()

        if data.get("stream"):
            # 流式响应
            response = StreamResponse()
            response.content_type = "text/event-stream"
            await response.prepare(request)

            async for chunk in await primitives_service.chat(**data):
                await response.write(
                    f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode('utf-8')
                )
            await response.write(b"data: [DONE]\n\n")

            return response
        else:
            # 非流式响应
            response = await primitives_service.chat(**data)
            return web.json_response(
                response,
                dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
            )
    except Exception as e:
        logger.error(f"[API] Primitives chat error: {e}")
        logger.exception(e)
        if data.get("stream"):
            await response.write(f"data: [ERROR] {e}\n\n".encode('utf-8'))
            return response
        else:
            return web.Response(text=str(e), status=500)


# ==================== T2I Primitives Endpoints ====================

async def generate_image(request: web.Request) -> web.Response:
    """
    T2I图片生成接口
    """
    try:
        data = await _get_post_data(request)
        image_files = await _get_post_file_paths(request)

        result = await primitives_service.generate_image(**data, image_files=image_files)
        
        # Handle result
        if result:
            return web.Response(text=result)
        else:
            # Error message
            return web.Response(text="T2I generate image failed", status=500)
    except Exception as e:
        logger.error(f"[API] T2I generate image error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


# ==================== Speech Primitives Endpoints ====================

async def speech_to_text(request: web.Request) -> web.Response:
    """
    语音转文本
    """
    try:
        audio_file = await _get_post_file_path(request)
        result = await primitives_service.speech_to_text(audio_file)
        return web.json_response(
            result,
            dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"[API] Speech to text error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


async def text_to_speech(request: web.Request) -> web.Response:
    """
    文本转语音
    """
    try:
        data = await _get_post_data(request)
        result = await primitives_service.text_to_speech(**data)
        
        # Handle result based on type
        if isinstance(result, str) and result.endswith('.wav'):
            # Voice file path - convert to URL
            voice_url = Storage.path_to_url(await Storage.save(result))
            return web.Response(text=result)
        else:
            # Error message or other result
            return web.Response(text=result, status=500)
    except Exception as e:
        logger.error(f"[API] Text to speech error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


# ==================== Web Primitives Endpoints ====================

async def get_search_options(request: web.Request) -> web.Response:
    """
    获取搜索选项
    """
    try:
        result = primitives_service.get_search_options()
        return web.json_response(
            result,
            dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"[API] Search options error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


async def search_web(request: web.Request) -> web.Response:
    """
    网页搜索
    """
    try:
        data = await _get_post_data(request)
        result = await primitives_service.search_web(**data)
        return web.json_response(
            result,
            dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"[API] Search web error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


async def scrape_webpage(request: web.Request) -> web.Response:
    """
    获取网页内容
    """
    try:
        data = await _get_post_data(request)
        result = await primitives_service.scrape_webpage(**data)
        return web.Response(text=result)
    except Exception as e:
        logger.error(f"[API] Web page error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


async def download_webpage(request: web.Request) -> web.Response:
    """
    下载网页
    """
    try:
        data = await _get_post_data(request)
        result = await primitives_service.download_webpage(**data)
        
        # Handle result based on type
        if isinstance(result, bytes):
            # Return binary content
            return web.Response(body=result, content_type='application/octet-stream')
        else:
            return web.json_response(
                result,
                dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
            )
    except Exception as e:
        logger.error(f"[API] Download page error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)

async def get_short_link(request: web.Request) -> web.Response:
    """
    获取短链接
    """
    try:
        data = await _get_post_data(request)
        result = await primitives_service.get_short_link(**data)
        return web.Response(text=result)
    except Exception as e:
        logger.error(f"[API] Short link error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


async def redirect_by_token(request: web.Request) -> web.Response:
    """
    通过token获取短链接并重定向
    """
    try:
        token = request.match_info.get('token', '')
        
        # 调用get_link_by_token获取链接
        link = await primitives_service.get_link_by_token(token=token)
        
        if not link or not link.startswith("http"):
            return web.Response(text="Link not found", status=404)
        else:
            return web.HTTPFound(link)
    except Exception as e:
        logger.error(f"[API] Redirect by token error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)

# ==================== QRCode Primitives Endpoints ====================

async def generate_qrcode(request: web.Request) -> web.Response:
    """
    生成二维码
    """
    try:
        data = await _get_post_data(request)
        result = await primitives_service.generate_qrcode(**data)
        
        # Handle result
        if result.startswith("http"):
            return web.Response(text=result)
        else:
            # Error message
            return web.Response(text=result, status=500)
    except Exception as e:
        logger.error(f"[API] Generate QRCode error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


async def recognize_qrcode(request: web.Request) -> web.Response:
    """
    识别二维码
    """
    try:
        image_file = await _get_post_file_path(request)
        result = await primitives_service.recognize_qrcode(image_file)
        
        if result:
            return web.Response(text=result)
        else:
            return web.Response(text="No QR code found in image", status=404)
    except Exception as e:
        logger.error(f"[API] Recognize QRCode error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


# ==================== Agent Endpoints ====================

async def think(request: web.Request) -> web.Response:
    """
    Agent思考接口 - 支持工具调用和循环
    """
    response = StreamResponse()

    # 流式响应
    response.content_type = "text/event-stream"
    await response.prepare(request)

    try:
        data = await request.json()

        if "depth" in data:
            del data["depth"]
        if "max_depth" in data:
            del data["max_depth"]
        if "active_time" in data:
            del data["active_time"]
        
        async for chunk in agent_service.think(**data):
            await response.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode('utf-8'))
    except Exception as e:
        logger.error(f"[API] Agent think error: {e}")
        logger.exception(e)
        await response.write(f"data: [ERROR] {e}\n\n".encode('utf-8'))

    return response

async def message(request: web.Request) -> web.Response:
    """
    保存用户消息到会话
    """
    try:
        data = await request.json()
        result = await agent_service.message(**data)
        return web.json_response(
            result,
            dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"[API] Agent message error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)

async def client_tool_result(request: web.Request) -> web.Response:
    """提交 client tool 执行结果"""
    try:
        data = await request.json()
        await agent_service.client_tool_result(**data)
        return web.Response(text="success")
    except Exception as e:
        logger.error(f"[API] Client tool result error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)

async def client_action_complete(request: web.Request) -> web.Response:
    """客户端上报 action 执行结束"""
    try:
        data = await request.json()
        await agent_service.client_action_complete(**data)
        return web.Response(text="success")
    except Exception as e:
        logger.error(f"[API] Client action complete error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)

async def get_history(request: web.Request) -> web.Response:
    """获取用户消息历史"""
    try:
        params = request.rel_url.query
        result = await agent_service.get_history(**params)
        return web.json_response(
            result,
            dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"[API] Get agent history error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


# ==================== Memory Endpoints ====================

async def get_auto_mode_history(request: web.Request) -> web.Response:
    """Agent 自动思考会话消息历史"""
    try:
        result = await memory_service.get_auto_mode_history()
        return web.json_response(
            result,
            dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"[API] Get auto mode history error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)

async def get_mixed_memory(request: web.Request) -> web.Response:
    """Agent 印象信息"""
    try:
        result = await memory_service.get_mixed_memory()
        return web.json_response(
            result,
            dumps=lambda obj: json.dumps(obj, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"[API] Text to image error: {e}")
        logger.exception(e)
        return web.Response(text=str(e), status=500)


# ==================== Application Setup ====================

def create_app() -> web.Application:
    """创建aiohttp应用"""
    app = web.Application(client_max_size=1024**2*512)
    
    # 注册启动和关闭事件
    app.on_startup.append(startup)
    app.on_cleanup.append(shutdown)
    
    # 注册路由
    # Health Check
    app.router.add_get("/health", health_check)
    
    # Upload Primitives Endpoints
    app.router.add_post("/primitives/file/upload", upload)
    
    # Llm Primitives Endpoints
    app.router.add_post("/primitives/llm/chat{tail:.*}", chat)
    
    # T2I Primitives Endpoints
    app.router.add_post("/primitives/t2i/generate-image", generate_image)
    
    # Speech Primitives Endpoints
    app.router.add_post("/primitives/speech/speech-to-text", speech_to_text)
    app.router.add_post("/primitives/speech/text-to-speech", text_to_speech)
    
    # Web Primitives Endpoints
    app.router.add_get("/primitives/web/get-search-options", get_search_options)
    app.router.add_post("/primitives/web/search-web", search_web)
    app.router.add_post("/primitives/web/scrape-webpage", scrape_webpage)
    app.router.add_post("/primitives/web/download-webpage", download_webpage)
    app.router.add_post("/primitives/web/get-short-link", get_short_link)
    app.router.add_get("/primitives/web/redirect-link/{token}", redirect_by_token)
    
    # QRCode Primitives Endpoints
    app.router.add_post("/primitives/qrcode/generate", generate_qrcode)
    app.router.add_post("/primitives/qrcode/recognize", recognize_qrcode)

    # Agent Endpoints
    app.router.add_post("/agent/think", think)
    app.router.add_post("/agent/message", message)
    app.router.add_post("/agent/client-tool-result", client_tool_result)
    app.router.add_post("/agent/client-action-complete", client_action_complete)
    app.router.add_get("/agent/get-history", get_history)
    
    # Memory Endpoints
    app.router.add_get("/memory/get-auto-mode-history", get_auto_mode_history)
    app.router.add_get("/memory/get-mixed-memory", get_mixed_memory)
    
    return app


# ==================== Main ====================

def run_api_server(host: str = "localhost", port: int = 9529):
    """
    启动API服务器
    
    Args:
        host: 监听地址
        port: 监听端口
    """
    logger.info(f"[API] Starting API server on {host}:{port}")
    app = create_app()
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    run_api_server()
