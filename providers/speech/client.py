import os
import time
import asyncio
import numpy as np
import whisper
import soundfile as sf
import sherpa_onnx
import opencc
from providers.storage.client import StorageClient
from common.log import logger
from common.tmp_dir import TmpDir
from config import conf


class SpeechClient:
    """语音处理客户端 - 直接实现，无需适配器模式"""
    
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.models_base_path = conf().get("models_base_path")
        
        # 初始化Whisper模型
        self.whisper_model = whisper.load_model(
            name="small",
            download_root=os.path.join(self.models_base_path, "whisper")
        )
        # 初始化繁简转换器
        self.t2s_converter = opencc.OpenCC('t2s')
        
        # 初始化Sherpa-Onnx TTS
        try:
            # 构建VITS模型配置
            vits_config = sherpa_onnx.OfflineTtsVitsModelConfig(
                model=os.path.join(self.models_base_path, "vits-melo-tts-zh_en/model.onnx"),
                tokens=os.path.join(self.models_base_path, "vits-melo-tts-zh_en/tokens.txt"),
                lexicon=os.path.join(self.models_base_path, "vits-melo-tts-zh_en/lexicon.txt")
            )
            # 构建模型全局配置
            model_config = sherpa_onnx.OfflineTtsModelConfig(
                vits=vits_config,
                num_threads=2,
                provider="cpu"
            )
            # 构建TTS全局配置
            tts_config = sherpa_onnx.OfflineTtsConfig(
                model=model_config
            )
            # 初始化TTS模型
            self.tts_model = sherpa_onnx.OfflineTts(tts_config)
            logger.info("[SpeechClient] Sherpa-Onnx TTS 初始化成功")
        except Exception as e:
            logger.error("[SpeechClient] Sherpa-Onnx TTS 初始化失败: {}".format(e))
            raise e
    
    async def speech_to_text(self, audio_input: str) -> str:
        """
        语音转文字
        
        Args:
            audio_input: 音频输入，支持本地文件路径或远程URL
            lang: 语言代码，默认'zh-CN'
        
        Returns:
            识别到的文字内容
        """
        result = None
        try:
            tmp_path = await TmpDir.save(audio_input)
            
            # 转写音频 - 使用线程池避免阻塞事件循环
            transcribe_result = await asyncio.to_thread(
                self.whisper_model.transcribe,
                tmp_path,
                language="zh"
            )
            result = self.t2s_converter.convert(transcribe_result["text"])
        except Exception as e:
            logger.error("[SpeechClient] speech_to_text error: {}".format(e))
            logger.exception(e)
            raise e

        return result
    
    async def text_to_speech(self, text: str) -> str:
        """
        文字转语音（本地离线合成，使用Sherpa-Onnx TTS）
        资源占用极低，2核CPU即可流畅运行，支持中英混合
        
        Args:
            text: 要转换的文字内容，支持中英混合
        
        Returns:
            生成的音频文件的URL地址
        """
        result = None
        try:
            # 保存到临时文件
            tmp_path = TmpDir.path() + f"/tts_{time.time_ns()}.wav"
            
            # 本地生成音频 - 使用线程池避免阻塞事件循环
            audio = await asyncio.to_thread(
                self.tts_model.generate,
                text,
                sid=0,
                speed=1.0
            )
            
            # 保存为wav文件 - 同步IO操作也放到线程池
            await asyncio.to_thread(
                sf.write,
                tmp_path,
                audio.samples,
                audio.sample_rate
            )
            
            # 保存到Storage并返回URL
            audio_path = await StorageClient.save(tmp_path)
            result = StorageClient.path_to_url(audio_path)
            
            # 清理临时文件
            os.unlink(tmp_path)
            
        except Exception as e:
            logger.error("[SpeechClient] text_to_speech error: {}".format(e))
            logger.exception(e)
            raise e

        return result
    
