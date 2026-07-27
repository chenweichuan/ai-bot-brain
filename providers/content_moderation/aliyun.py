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
    """Aliyun content security moderation adapter"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Aliyun moderation client

        Args:
            config: Configuration dict containing:
                - access_key_id: Aliyun AccessKey ID
                - access_key_secret: Aliyun AccessKey Secret
                - region_id: Region ID, default cn-shanghai
                - endpoint: Endpoint, default green-cip.cn-shanghai.aliyuncs.com
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
        """Get moderation client"""
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
        """Moderate image using specified endpoint"""
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
                    error_msg = getattr(result, 'msg', getattr(result, 'message', 'Unknown error'))
                    return CheckResult(
                        CheckStatus.ERROR,
                        f"Moderation failed: {error_msg}",
                        {"code": result.code}
                    )
            else:
                return CheckResult(
                    CheckStatus.ERROR,
                    f"Request failed: {response.status_code}"
                )
        except Exception as e:
            logger.error(f"[AliyunModeration] Image moderation error: {e}")
            return CheckResult(CheckStatus.ERROR, str(e))

    async def check_image(self, image_url: str) -> CheckResult:
        """
        Moderate an image

        Args:
            image_url: Public URL of the image
        """
        result = await self._check_image_with_endpoint(image_url, use_backup=False)

        if result.status == CheckStatus.ERROR:
            logger.info("[AliyunModeration] Primary endpoint failed, trying backup endpoint")
            result = await self._check_image_with_endpoint(image_url, use_backup=True)

        return result

    def _parse_image_result(self, result_data: Any) -> CheckResult:
        """Parse image moderation result"""
        try:
            data_map = result_data.to_map() if hasattr(result_data, 'to_map') else result_data
            results = data_map.get("Result", [])
            for item in results:
                label = item.get("Label", "nonLabel")
                risk_level = item.get("RiskLevel", "none")
                if risk_level != "none":
                    return CheckResult(
                        CheckStatus.BLOCK,
                        f"Violation detected: {label} - {item.get('Description', '')}",
                        {"raw_result": data_map}
                    )
            return CheckResult(CheckStatus.PASS, "Moderation passed", {"raw_result": data_map})
        except Exception as e:
            logger.error(f"[AliyunModeration] Error parsing image result: {e}")
            return CheckResult(CheckStatus.ERROR, f"Result parsing failed: {e}")

    async def _poll_async_result(self, task_id: str, result_type: str) -> Optional[Any]:
        """
        Poll for async moderation result

        Args:
            task_id: Task ID
            result_type: Result type ('audio', 'video', 'document')

        Returns:
            Moderation result data
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
                logger.warning(f"[AliyunModeration] Failed to query {result_type} result (attempt {attempt + 1}/{self.max_poll_attempts}): {e}")

        logger.error(f"[AliyunModeration] {result_type} moderation result query timed out")
        return None

    async def _query_audio_result(self, client: Client, task_id: str) -> Optional[Any]:
        """Query audio moderation result"""
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
                # Task complete when code=200 and RiskLevel is present
                if "RiskLevel" in data_map:
                    return result.data
            # code=280 or other cases: continue polling
        return None

    async def _query_video_result(self, client: Client, task_id: str) -> Optional[Any]:
        """Query video moderation result"""
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
                # Task complete when code=200 and RiskLevel is present
                if "RiskLevel" in data_map:
                    return result.data
            # code=280 or other cases: continue polling
        return None

    async def _query_document_result(self, client: Client, task_id: str) -> Optional[Any]:
        """Query document moderation result"""
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
                # Task complete when code=200 and RiskLevel is present
                if "RiskLevel" in data_map:
                    return result.data
            # code=280 or other cases: continue polling
        return None

    def _parse_async_result(self, result_data: Any) -> CheckResult:
        """Parse async moderation result"""
        try:
            data_map = result_data.to_map() if hasattr(result_data, 'to_map') else result_data
            risk_level = data_map.get("RiskLevel", "none")

            if risk_level != "none":
                # Check Result array for more details
                results = data_map.get("Result", [])
                if results:
                    item = results[0]
                    label = item.get("Label", "nonLabel")
                    return CheckResult(
                        CheckStatus.BLOCK,
                        f"Violation detected: {label} - {item.get('Description', '')}",
                        {"raw_result": data_map}
                    )
                return CheckResult(
                    CheckStatus.BLOCK,
                    f"Violation detected: RiskLevel={risk_level}",
                    {"raw_result": data_map}
                )

            return CheckResult(CheckStatus.PASS, "Moderation passed", {"raw_result": data_map})
        except Exception as e:
            logger.error(f"[AliyunModeration] Error parsing async result: {e}")
            return CheckResult(CheckStatus.ERROR, f"Result parsing failed: {e}")

    async def check_audio(self, audio_url: str) -> CheckResult:
        """
        Moderate an audio file

        Args:
            audio_url: Public URL of the audio file
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
                        logger.info(f"[AliyunModeration] Audio task submitted, task ID: {task_id}")
                        final_result = await self._poll_async_result(task_id, 'audio')
                        if final_result:
                            return self._parse_async_result(final_result)
                        return CheckResult(CheckStatus.TIMEOUT, "Moderation timed out")
                    else:
                        return self._parse_async_result(result.data)
                else:
                    error_msg = getattr(result, 'msg', getattr(result, 'message', 'Unknown error'))
                    return CheckResult(CheckStatus.ERROR, f"Moderation failed: {error_msg}")
            else:
                return CheckResult(CheckStatus.ERROR, f"Request failed: {response.status_code}")
        except Exception as e:
            logger.error(f"[AliyunModeration] Audio moderation error: {e}")
            return CheckResult(CheckStatus.ERROR, str(e))

    def _parse_audio_result(self, result_data: Any) -> CheckResult:
        """Parse audio moderation result (kept for compatibility)"""
        return self._parse_async_result(result_data)

    async def check_video(self, video_url: str) -> CheckResult:
        """
        Moderate a video

        Args:
            video_url: Public URL of the video
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
                        logger.info(f"[AliyunModeration] Video task submitted, task ID: {task_id}")
                        final_result = await self._poll_async_result(task_id, 'video')
                        if final_result:
                            return self._parse_async_result(final_result)
                        return CheckResult(CheckStatus.TIMEOUT, "Moderation timed out")
                    else:
                        return self._parse_async_result(result.data)
                else:
                    error_msg = getattr(result, 'msg', getattr(result, 'message', 'Unknown error'))
                    return CheckResult(CheckStatus.ERROR, f"Moderation failed: {error_msg}")
            else:
                return CheckResult(CheckStatus.ERROR, f"Request failed: {response.status_code}")
        except Exception as e:
            logger.error(f"[AliyunModeration] Video moderation error: {e}")
            return CheckResult(CheckStatus.ERROR, str(e))

    def _parse_video_result(self, result_data: Any) -> CheckResult:
        """Parse video moderation result (kept for compatibility)"""
        return self._parse_async_result(result_data)

    async def check_document(self, doc_url: str) -> CheckResult:
        """
        Moderate a document

        Args:
            doc_url: Public URL of the document
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
                        logger.info(f"[AliyunModeration] Document task submitted, task ID: {task_id}")
                        final_result = await self._poll_async_result(task_id, 'document')
                        if final_result:
                            return self._parse_async_result(final_result)
                        return CheckResult(CheckStatus.TIMEOUT, "Moderation timed out")
                    else:
                        return self._parse_async_result(result.data)
                else:
                    error_msg = getattr(result, 'msg', getattr(result, 'message', 'Unknown error'))
                    return CheckResult(CheckStatus.ERROR, f"Moderation failed: {error_msg}")
            else:
                return CheckResult(CheckStatus.ERROR, f"Request failed: {response.status_code}")
        except Exception as e:
            logger.error(f"[AliyunModeration] Document moderation error: {e}")
            return CheckResult(CheckStatus.ERROR, str(e))

    def _parse_document_result(self, result_data: Any) -> CheckResult:
        """Parse document moderation result (kept for compatibility)"""
        return self._parse_async_result(result_data)