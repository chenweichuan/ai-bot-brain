# encoding:utf-8
import json
import uuid
import asyncio
from typing import Optional, Dict, Any

from alibabacloud_green20220302.client import Client
from alibabacloud_green20220302 import models
from alibabacloud_tea_openapi.models import Config
from alibabacloud_tea_util import models as util_models

from common.log import logger
from providers.content_moderation.enums import CheckResult, CheckStatus


class AliyunContentModeration:
    """阿里云内容安全审核适配器"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化阿里云审核客户端

        Args:
            config: 配置字典，包含:
                - access_key_id: 阿里云AccessKey ID
                - access_key_secret: 阿里云AccessKey Secret
                - region_id: 区域ID，默认cn-shanghai
                - endpoint: 接入点，默认green-cip.cn-shanghai.aliyuncs.com
        """
        self.access_key_id = config.get("access_key_id")
        self.access_key_secret = config.get("access_key_secret")
        self.region_id = config.get("region_id", "cn-shanghai")
        self.endpoint = config.get("endpoint", f"green-cip.{self.region_id}.aliyuncs.com")
        self.backup_endpoint = config.get("backup_endpoint", f"green-cip.cn-shanghai.aliyuncs.com")
        self.poll_interval = config.get("poll_interval", 2)
        self.max_poll_attempts = config.get("max_poll_attempts", 30)

        self._client: Optional[Client] = None
        self._runtime = util_models.RuntimeOptions()

    def _get_client(self, use_backup: bool = False) -> Client:
        """获取审核客户端"""
        endpoint = self.backup_endpoint if use_backup else self.endpoint
        config = Config(
            access_key_id=self.access_key_id,
            access_key_secret=self.access_key_secret,
            connect_timeout=10000,
            read_timeout=30000,
            region_id=self.region_id,
            endpoint=endpoint
        )
        return Client(config)

    async def _check_image_with_endpoint(self, image_url: str, use_backup: bool = False) -> CheckResult:
        """使用指定接入点审核图片"""
        client = self._get_client(use_backup)
        data_id = str(uuid.uuid1())

        service_parameters = {
            'imageUrl': image_url,
            'dataId': data_id
        }

        request = models.ImageModerationRequest(
            service='baselineCheck',
            service_parameters=json.dumps(service_parameters)
        )

        try:
            response = client.image_moderation_with_options(request, self._runtime)

            if response.status_code == 200:
                result = response.body
                if result.code == 200:
                    result_data = result.data
                    return self._parse_image_result(result_data)
                else:
                    error_msg = getattr(result, 'msg', getattr(result, 'message', '未知错误'))
                    return CheckResult(
                        CheckStatus.ERROR,
                        f"审核失败: {error_msg}",
                        {"code": result.code}
                    )
            else:
                return CheckResult(
                    CheckStatus.ERROR,
                    f"请求失败: {response.status_code}"
                )
        except Exception as e:
            logger.error(f"[AliyunModeration] 图片审核异常: {e}")
            return CheckResult(CheckStatus.ERROR, str(e))

    async def check_image(self, image_url: str) -> CheckResult:
        """
        审核图片

        Args:
            image_url: 图片公网URL
        """
        result = await self._check_image_with_endpoint(image_url, use_backup=False)

        if result.status == CheckStatus.ERROR:
            logger.info("[AliyunModeration] 主接入点失败，尝试备用接入点")
            result = await self._check_image_with_endpoint(image_url, use_backup=True)

        return result

    def _parse_image_result(self, result_data: Any) -> CheckResult:
        """解析图片审核结果"""
        try:
            data_map = result_data.to_map() if hasattr(result_data, 'to_map') else result_data
            results = data_map.get("Result", [])
            for item in results:
                label = item.get("Label", "nonLabel")
                risk_level = item.get("RiskLevel", "none")
                if risk_level != "none":
                    return CheckResult(
                        CheckStatus.BLOCK,
                        f"检测到违规内容: {label} - {item.get('Description', '')}",
                        {"raw_result": data_map}
                    )
            return CheckResult(CheckStatus.PASS, "审核通过", {"raw_result": data_map})
        except Exception as e:
            logger.error(f"[AliyunModeration] 解析图片结果异常: {e}")
            return CheckResult(CheckStatus.ERROR, f"结果解析失败: {e}")

    async def _poll_async_result(self, task_id: str, result_type: str) -> Optional[Any]:
        """
        轮询查询异步审核结果

        Args:
            task_id: 任务ID
            result_type: 结果类型 ('audio', 'video', 'document')

        Returns:
            审核结果数据
        """
        client = self._get_client()

        for attempt in range(self.max_poll_attempts):
            await asyncio.sleep(self.poll_interval)

            try:
                if result_type == 'audio':
                    result = await self._query_audio_result(client, task_id)
                elif result_type == 'video':
                    result = await self._query_video_result(client, task_id)
                elif result_type == 'document':
                    result = await self._query_document_result(client, task_id)
                else:
                    return None

                if result is not None:
                    return result

            except Exception as e:
                logger.warning(f"[AliyunModeration] 查询{result_type}结果失败 (尝试 {attempt + 1}/{self.max_poll_attempts}): {e}")

        logger.error(f"[AliyunModeration] {result_type}审核结果查询超时")
        return None

    async def _query_audio_result(self, client: Client, task_id: str) -> Optional[Any]:
        """查询音频审核结果"""
        service_parameters = {"taskId": task_id}
        request = models.VoiceModerationResultRequest(
            service='audio_media_detection',
            service_parameters=json.dumps(service_parameters)
        )
        response = client.voice_moderation_result(request)

        if response.status_code == 200:
            result = response.body
            if result.code == 200:
                data_map = result.data.to_map() if hasattr(result.data, 'to_map') else result.data
                # 当code=200且包含RiskLevel时，任务已完成
                if "RiskLevel" in data_map:
                    return result.data
            # code=280或其他情况继续轮询
        return None

    async def _query_video_result(self, client: Client, task_id: str) -> Optional[Any]:
        """查询视频审核结果"""
        service_parameters = {"taskId": task_id}
        request = models.VideoModerationResultRequest(
            service='videoDetection',
            service_parameters=json.dumps(service_parameters)
        )
        response = client.video_moderation_result(request)

        if response.status_code == 200:
            result = response.body
            if result.code == 200:
                data_map = result.data.to_map() if hasattr(result.data, 'to_map') else result.data
                # 当code=200且包含RiskLevel时，任务已完成
                if "RiskLevel" in data_map:
                    return result.data
            # code=280或其他情况继续轮询
        return None

    async def _query_document_result(self, client: Client, task_id: str) -> Optional[Any]:
        """查询文档审核结果"""
        service_parameters = {"taskId": task_id}
        request = models.DescribeFileModerationResultRequest(
            service='document_detection',
            service_parameters=json.dumps(service_parameters)
        )
        response = client.describe_file_moderation_result(request)

        if response.status_code == 200:
            result = response.body
            if result.code == 200:
                data_map = result.data.to_map() if hasattr(result.data, 'to_map') else result.data
                # 当code=200且包含RiskLevel时，任务已完成
                if "RiskLevel" in data_map:
                    return result.data
            # code=280或其他情况继续轮询
        return None

    def _parse_async_result(self, result_data: Any) -> CheckResult:
        """解析异步审核结果"""
        try:
            data_map = result_data.to_map() if hasattr(result_data, 'to_map') else result_data
            risk_level = data_map.get("RiskLevel", "none")

            if risk_level != "none":
                # 检查是否有 Result 数组获取更多详情
                results = data_map.get("Result", [])
                if results:
                    item = results[0]
                    label = item.get("Label", "nonLabel")
                    return CheckResult(
                        CheckStatus.BLOCK,
                        f"检测到违规内容: {label} - {item.get('Description', '')}",
                        {"raw_result": data_map}
                    )
                return CheckResult(
                    CheckStatus.BLOCK,
                    f"检测到违规内容: RiskLevel={risk_level}",
                    {"raw_result": data_map}
                )

            return CheckResult(CheckStatus.PASS, "审核通过", {"raw_result": data_map})
        except Exception as e:
            logger.error(f"[AliyunModeration] 解析异步结果异常: {e}")
            return CheckResult(CheckStatus.ERROR, f"结果解析失败: {e}")

    async def check_audio(self, audio_url: str) -> CheckResult:
        """
        审核音频

        Args:
            audio_url: 音频公网URL
        """
        client = self._get_client()
        service_parameters = {'url': audio_url}

        request = models.VoiceModerationRequest(
            service='audio_media_detection',
            service_parameters=json.dumps(service_parameters)
        )

        try:
            response = client.voice_moderation(request)
            if response.status_code == 200:
                result = response.body
                if result.code == 200:
                    data_map = result.data.to_map() if hasattr(result.data, 'to_map') else result.data
                    task_id = data_map.get("TaskId")

                    if task_id:
                        logger.info(f"[AliyunModeration] 音频任务已提交，任务ID: {task_id}")
                        final_result = await self._poll_async_result(task_id, 'audio')
                        if final_result:
                            return self._parse_async_result(final_result)
                        return CheckResult(CheckStatus.TIMEOUT, "审核超时")
                    else:
                        return self._parse_async_result(result.data)
                else:
                    error_msg = getattr(result, 'msg', getattr(result, 'message', '未知错误'))
                    return CheckResult(CheckStatus.ERROR, f"审核失败: {error_msg}")
            else:
                return CheckResult(CheckStatus.ERROR, f"请求失败: {response.status_code}")
        except Exception as e:
            logger.error(f"[AliyunModeration] 音频审核异常: {e}")
            return CheckResult(CheckStatus.ERROR, str(e))

    def _parse_audio_result(self, result_data: Any) -> CheckResult:
        """解析音频审核结果（兼容性保留）"""
        return self._parse_async_result(result_data)

    async def check_video(self, video_url: str) -> CheckResult:
        """
        审核视频

        Args:
            video_url: 视频公网URL
        """
        client = self._get_client()
        service_parameters = {'url': video_url}

        request = models.VideoModerationRequest(
            service='videoDetection',
            service_parameters=json.dumps(service_parameters)
        )

        try:
            response = client.video_moderation(request)
            if response.status_code == 200:
                result = response.body
                if result.code == 200:
                    data_map = result.data.to_map() if hasattr(result.data, 'to_map') else result.data
                    task_id = data_map.get("TaskId")

                    if task_id:
                        logger.info(f"[AliyunModeration] 视频任务已提交，任务ID: {task_id}")
                        final_result = await self._poll_async_result(task_id, 'video')
                        if final_result:
                            return self._parse_async_result(final_result)
                        return CheckResult(CheckStatus.TIMEOUT, "审核超时")
                    else:
                        return self._parse_async_result(result.data)
                else:
                    error_msg = getattr(result, 'msg', getattr(result, 'message', '未知错误'))
                    return CheckResult(CheckStatus.ERROR, f"审核失败: {error_msg}")
            else:
                return CheckResult(CheckStatus.ERROR, f"请求失败: {response.status_code}")
        except Exception as e:
            logger.error(f"[AliyunModeration] 视频审核异常: {e}")
            return CheckResult(CheckStatus.ERROR, str(e))

    def _parse_video_result(self, result_data: Any) -> CheckResult:
        """解析视频审核结果（兼容性保留）"""
        return self._parse_async_result(result_data)

    async def check_document(self, doc_url: str) -> CheckResult:
        """
        审核文档

        Args:
            doc_url: 文档公网URL
        """
        client = self._get_client()
        service_parameters = {'url': doc_url}

        request = models.FileModerationRequest(
            service='document_detection',
            service_parameters=json.dumps(service_parameters)
        )

        try:
            response = client.file_moderation(request)
            if response.status_code == 200:
                result = response.body
                if result.code == 200:
                    data_map = result.data.to_map() if hasattr(result.data, 'to_map') else result.data
                    task_id = data_map.get("TaskId")

                    if task_id:
                        logger.info(f"[AliyunModeration] 文档任务已提交，任务ID: {task_id}")
                        final_result = await self._poll_async_result(task_id, 'document')
                        if final_result:
                            return self._parse_async_result(final_result)
                        return CheckResult(CheckStatus.TIMEOUT, "审核超时")
                    else:
                        return self._parse_async_result(result.data)
                else:
                    error_msg = getattr(result, 'msg', getattr(result, 'message', '未知错误'))
                    return CheckResult(CheckStatus.ERROR, f"审核失败: {error_msg}")
            else:
                return CheckResult(CheckStatus.ERROR, f"请求失败: {response.status_code}")
        except Exception as e:
            logger.error(f"[AliyunModeration] 文档审核异常: {e}")
            return CheckResult(CheckStatus.ERROR, str(e))

    def _parse_document_result(self, result_data: Any) -> CheckResult:
        """解析文档审核结果（兼容性保留）"""
        return self._parse_async_result(result_data)