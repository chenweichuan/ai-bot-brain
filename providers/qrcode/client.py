import qrcode
import cv2
import io
from common.storage import Storage
from common.log import logger
from common.tmp_dir import TmpDir

class QrcodeClient:
    """二维码客户端 - 直接实现，无需适配器模式"""
    
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def generate_qrcode(self, data: str):
        """
        生成二维码
        
        Args:
            data: 二维码包含的数据字符
        
        Returns:
            二维码图片的URL地址
        """
        result = None
        try:
            # 创建二维码对象
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            
            # 添加数据
            qr.add_data(data)
            qr.make(fit=True)
            
            # 生成二维码图像
            img = qr.make_image(
                fill_color='black',
                back_color='white'
            )
            
            # 将图像转换为字节流
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            img_byte_arr = img_byte_arr.getvalue()
            
            # 保存到Storage并返回URL
            result = Storage.path_to_url(await Storage.save(img_byte_arr))
        except Exception as e:
            logger.info("[QRCode] generate_qrcodes error: {}".format(e))
            logger.exception(e)
            raise e

        return result
    
    async def recognize_qrcode(self, image_file: str):
        """
        识别二维码
        
        Args:
            image_file: 图像文件
        
        Returns:
            识别到的二维码数据字符串
        """

        result = None
        try:
            path = await TmpDir.save(image_file)
            img = cv2.imread(path)

            # 检查图像是否加载成功
            if img is None:
                return
            
            # 创建二维码检测器
            detector = cv2.QRCodeDetector()
            
            # 检测并解码二维码
            result, _, _ = detector.detectAndDecode(img)
        except Exception as e:
            logger.info("[QRCode] recognize_qrcodes error: {}".format(e))
            logger.exception(e)
            raise e

        return result
