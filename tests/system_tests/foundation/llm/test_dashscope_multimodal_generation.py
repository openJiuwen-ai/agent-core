#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
DashScope Multimodal Generation System Tests

This module contains system tests for the DashScope model client's multimodal generation methods:
- generate_image: Text-to-image generation using Wanx (通义万相) API
- generate_speech: Text-to-speech generation using Cosyvoice API
- generate_video: Text-to-video and image-to-video generation API
"""
import os
import unittest

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ModelClientConfig, Model, UserMessage, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.generation_response import (
    ImageGenerationResponse,
    AudioGenerationResponse,
    VideoGenerationResponse
)


# Environment variables for API configuration
API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-your-api-key")
API_BASE = os.getenv("DASHSCOPE_API_BASE", "dashscope-url")


class TestDashScopeImageGeneration(unittest.IsolatedAsyncioTestCase):
    """Test cases for DashScope generate_image method"""

    def _create_model_client_config(self) -> ModelClientConfig:
        """Create model client config for DashScope"""
        return ModelClientConfig(
            client_id="test_image_client",
            client_provider="DashScope",
            api_key=API_KEY,
            api_base=API_BASE,
            verify_ssl=False
        )

    def _create_model_config(self, model_name: str = "qwen-image-max") -> ModelRequestConfig:
        """Create model request config"""
        return ModelRequestConfig(
            model=model_name,
        )

    @unittest.skip("require network and API key")
    async def test_generate_image_basic(self):
        """Test basic image generation with text prompt"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen-image-max")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="小姑娘在花丛中的照片")
        ]
        
        response = await model.generate_image(messages=messages)
        
        # Verify response type
        self.assertIsInstance(response, ImageGenerationResponse)
        # Verify images are returned
        self.assertIsNotNone(response.images)
        self.assertGreater(len(response.images), 0)
        # Verify model name
        self.assertEqual(response.model, "qwen-image-max")

        logger.info(f"Generated images: {response.images}")

    @unittest.skip("require network and API key")
    async def test_generate_image_with_size(self):
        """Test image generation with custom size"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen-image-max")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="一只可爱的小猫在阳光下玩耍")
        ]
        
        response = await model.generate_image(
            messages=messages,
            size="1024*1024",
            prompt_extend=True,
            watermark=False
        )
        
        self.assertIsInstance(response, ImageGenerationResponse)
        self.assertIsNotNone(response.images)
        self.assertGreater(len(response.images), 0)
        
        logger.info(f"Generated images with custom size: {response.images}")

    @unittest.skip("require network and API key")
    async def test_generate_image_with_negative_prompt(self):
        """Test image generation with negative prompt"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen-image-max")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="美丽的山水风景画")
        ]
        
        response = await model.generate_image(
            messages=messages,
            negative_prompt="模糊, 低质量, 水印",
            seed=12345
        )
        
        self.assertIsInstance(response, ImageGenerationResponse)
        self.assertIsNotNone(response.images)
        
        logger.info(f"Generated images with negative prompt: {response.images}")

    @unittest.skip("require network and API key")
    async def test_generate_image_with_reference_image(self):
        """Test image generation with reference image (image-to-image)"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-image")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        # Content with both text and image reference
        messages = [
            UserMessage(content=[
                {"text": "将这张图片转换为水彩画风格"},
                {"image": "https://cdn.wanx.aliyuncs.com/tmp/pressure/umbrella1.png"}
            ])
        ]
        
        response = await model.generate_image(messages=messages)
        
        self.assertIsInstance(response, ImageGenerationResponse)
        self.assertIsNotNone(response.images)
        
        logger.info(f"Generated images with reference: {response.images}")

    @unittest.skip("require network and API key")
    async def test_generate_image_batch_generation(self):
        """Test batch image generation with n parameter

        Note: This test is skipped because qwen-image-max only supports n=1.
        DashScope API returns error: "num_images_per_prompt must be 1"
        """
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen-image-max")

        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )

        messages = [
            UserMessage(content="可爱的卡通小动物")
        ]

        response = await model.generate_image(
            messages=messages,
            n=1
        )

        self.assertIsInstance(response, ImageGenerationResponse)
        self.assertIsNotNone(response.images)
        self.assertGreaterEqual(len(response.images), 1)

        logger.info(f"Generated {len(response.images)} images in batch")

    @unittest.skip("require network and API key")
    async def test_generate_image_multiple_reference_images(self):
        """Test image generation with multiple reference images (up to 3)"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-image")

        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )

        # Content with text and multiple reference images
        messages = [
            UserMessage(content=[
                {"text": "融合这些图片的风格元素"},
                {"image": "https://cdn.wanx.aliyuncs.com/tmp/pressure/umbrella1.png"},
                {"image": "https://img.alicdn.com/imgextra/i3/O1CN01SfG4J41UYn9WNt4X1_"
                          "!!6000000002530-49-tps-1696-960.webp"}
            ])
        ]

        response = await model.generate_image(messages=messages)

        self.assertIsInstance(response, ImageGenerationResponse)
        self.assertIsNotNone(response.images)

        logger.info(f"Generated images with multiple references: {response.images}")

    async def test_generate_image_empty_messages_validation(self):
        """Test validation error when messages list is empty"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen-image-max")

        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )

        messages = []  # Empty messages

        from openjiuwen.core.common.exception.errors import ModelError
        with self.assertRaises(ModelError) as context:
            await model.generate_image(messages=messages)

        logger.info(f"Validation error caught as expected: {context.exception}")

    async def test_generate_image_too_many_images_validation(self):
        """Test validation error when input images exceed limit (>3)"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-image")

        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )

        # Content with 4 images (exceeds limit of 3)
        messages = [
            UserMessage(content=[
                {"text": "融合风格"},
                {"image": "https://example.com/img1.png"},
                {"image": "https://example.com/img2.png"},
                {"image": "https://example.com/img3.png"},
                {"image": "https://example.com/img4.png"}  # 4th image exceeds limit
            ])
        ]

        from openjiuwen.core.common.exception.errors import ModelError
        with self.assertRaises(ModelError) as context:
            await model.generate_image(messages=messages)

        self.assertIn("at most 3", str(context.exception))
        logger.info(f"Validation error caught as expected: {context.exception}")


class TestDashScopeSpeechGeneration(unittest.IsolatedAsyncioTestCase):
    """Test cases for DashScope generate_speech method"""

    def _create_model_client_config(self) -> ModelClientConfig:
        """Create model client config for DashScope"""
        return ModelClientConfig(
            client_id="test_speech_client",
            client_provider="DashScope",
            api_key=API_KEY,
            api_base=API_BASE,
            verify_ssl=False
        )

    def _create_model_config(self, model_name: str = "qwen3-tts-flash") -> ModelRequestConfig:
        """Create model request config"""
        return ModelRequestConfig(
            model=model_name,
        )

    @unittest.skip("require network and API key")
    async def test_generate_speech_basic(self):
        """Test basic speech generation with default voice"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen3-tts-flash")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="你好，我是通义千问语音合成服务。")
        ]
        
        response = await model.generate_speech(messages=messages)
        
        # Verify response type
        self.assertIsInstance(response, AudioGenerationResponse)
        # Verify audio URL or data is returned
        self.assertTrue(response.audio_url is not None or response.audio_data is not None)
        # Verify model name
        self.assertEqual(response.model, "qwen3-tts-flash")
        
        logger.info(f"Generated speech URL: {response.audio_url}")

    @unittest.skip("require network and API key")
    async def test_generate_speech_with_custom_voice(self):
        """Test speech generation with custom voice"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen3-tts-flash")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="这是一段使用自定义声音的语音合成测试。")
        ]
        
        response = await model.generate_speech(
            messages=messages,
            voice="Serena",
            language_type="Chinese"
        )
        
        self.assertIsInstance(response, AudioGenerationResponse)
        self.assertTrue(response.audio_url is not None or response.audio_data is not None)
        
        logger.info(f"Generated speech with custom voice: {response.audio_url}")

    @unittest.skip("require network and API key")
    async def test_generate_speech_long_text(self):
        """Test speech generation with longer text content"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen3-tts-flash")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        long_text = (
            "那我来给大家推荐一款T恤，这款呢真的是超级好看，这个颜色呢很显气质，"
            "而且呢也是搭配的绝佳单品，大家可以闭眼入，真的是非常好看，"
            "对身材的包容性也很好，不管啥身材的宝宝呢，穿上去都是很好看的。"
            "推荐宝宝们下单哦。"
        )
        
        messages = [
            UserMessage(content=long_text)
        ]
        
        response = await model.generate_speech(messages=messages)
        
        self.assertIsInstance(response, AudioGenerationResponse)
        self.assertTrue(response.audio_url is not None or response.audio_data is not None)
        
        logger.info(f"Generated speech for long text: {response.audio_url}")

    @unittest.skip("require network and API key")
    async def test_generate_speech_english(self):
        """Test speech generation with English text"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen3-tts-flash")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="Hello, welcome to use the Qianwen text-to-speech service.")
        ]
        
        response = await model.generate_speech(
            messages=messages,
            voice="Ethan",
            language_type="English"
        )
        
        self.assertIsInstance(response, AudioGenerationResponse)
        self.assertTrue(response.audio_url is not None or response.audio_data is not None)
        
        logger.info(f"Generated English speech: {response.audio_url}")

    async def test_generate_speech_empty_content_validation(self):
        """Test validation error when text content is empty"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("qwen3-tts-flash")

        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )

        messages = [
            UserMessage(content="")  # Empty content
        ]

        from openjiuwen.core.common.exception.errors import ModelError
        with self.assertRaises(ModelError) as context:
            await model.generate_speech(messages=messages)

        self.assertIn("non-empty", str(context.exception))
        logger.info(f"Validation error caught as expected: {context.exception}")


class TestDashScopeVideoGeneration(unittest.IsolatedAsyncioTestCase):
    """Test cases for DashScope generate_video method"""

    def _create_model_client_config(self) -> ModelClientConfig:
        """Create model client config for DashScope"""
        return ModelClientConfig(
            client_id="test_video_client",
            client_provider="DashScope",
            api_key=API_KEY,
            api_base=API_BASE,
            verify_ssl=False
        )

    def _create_model_config(self, model_name: str = "wan2.6-t2v") -> ModelRequestConfig:
        """Create model request config"""
        return ModelRequestConfig(
            model=model_name,
        )

    @unittest.skip("require network and API key")
    async def test_generate_video_text_to_video_basic(self):
        """Test basic text-to-video generation"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-t2v")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="生成一个小白兔在草地上奔跑的视频")
        ]
        
        response = await model.generate_video(messages=messages)
        
        # Verify response type
        self.assertIsInstance(response, VideoGenerationResponse)
        # Verify video URL is returned
        self.assertIsNotNone(response.video_url)
        # Verify model name
        self.assertEqual(response.model, "wan2.6-t2v")
        # Verify format
        self.assertEqual(response.format, "mp4")
        
        logger.info(f"Generated video URL: {response.video_url}")

    @unittest.skip("require network and API key")
    async def test_generate_video_with_custom_size_and_duration(self):
        """Test video generation with custom size and duration"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-t2v")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="夕阳下的海边，海浪轻轻拍打沙滩")
        ]
        
        response = await model.generate_video(
            messages=messages,
            size="1280*720",
            duration=5,
            prompt_extend=True,
            watermark=False
        )
        
        self.assertIsInstance(response, VideoGenerationResponse)
        self.assertIsNotNone(response.video_url)
        
        logger.info(f"Generated video with custom settings: {response.video_url}")

    @unittest.skip("require network and API key")
    async def test_generate_video_image_to_video(self):
        """Test image-to-video generation"""
        model_client_config = self._create_model_client_config()
        # Use i2v model for image-to-video
        model_config = self._create_model_config("wan2.6-i2v-flash")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="让图片中的伞改成五颜六色的黑")
        ]
        
        response = await model.generate_video(
            messages=messages,
            img_url="https://cdn.wanx.aliyuncs.com/tmp/pressure/umbrella1.png",
            resolution="720P",
            duration=5
        )
        
        self.assertIsInstance(response, VideoGenerationResponse)
        self.assertIsNotNone(response.video_url)
        
        logger.info(f"Generated i2v video: {response.video_url}")

    @unittest.skip("require network and API key")
    async def test_generate_video_with_negative_prompt(self):
        """Test video generation with negative prompt"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-t2v")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="一只小鸟在蓝天中飞翔")
        ]
        
        response = await model.generate_video(
            messages=messages,
            negative_prompt="模糊, 低质量, 抖动",
            seed=42,
            prompt_extend=True
        )
        
        self.assertIsInstance(response, VideoGenerationResponse)
        self.assertIsNotNone(response.video_url)
        
        logger.info(f"Generated video with negative prompt: {response.video_url}")

    @unittest.skip("require network and API key")
    async def test_generate_video_with_audio(self):
        """Test video generation with audio"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-t2v")
        
        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )
        
        messages = [
            UserMessage(content="一个人在舞台上唱歌")
        ]
        
        response = await model.generate_video(
            messages=messages,
            audio_url="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/ozwpvi/rap.mp3",
            duration=10
        )
        
        self.assertIsInstance(response, VideoGenerationResponse)
        self.assertIsNotNone(response.video_url)
        
        logger.info(f"Generated video with audio: {response.video_url}")

    @unittest.skip("require network and API key")
    async def test_generate_video_image_to_video_with_audio(self):
        """Test image-to-video generation with audio (i2v + audio combination)"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-i2v-flash")

        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )

        messages = [
            UserMessage(content="让图片中的场景动起来，配合音乐节奏")
        ]

        response = await model.generate_video(
            messages=messages,
            img_url="https://cdn.wanx.aliyuncs.com/tmp/pressure/umbrella1.png",
            audio_url="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/ozwpvi/rap.mp3",
            resolution="720P",
            duration=8
        )

        self.assertIsInstance(response, VideoGenerationResponse)
        self.assertIsNotNone(response.video_url)

        logger.info(f"Generated i2v video with audio: {response.video_url}")

    async def test_generate_video_empty_messages_validation(self):
        """Test validation error when messages list is empty"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-t2v")

        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )

        messages = []  # Empty messages

        from openjiuwen.core.common.exception.errors import ModelError
        with self.assertRaises(ModelError) as context:
            await model.generate_video(messages=messages)

        logger.info(f"Validation error caught as expected: {context.exception}")

    async def test_generate_video_empty_content_validation(self):
        """Test validation error when text content is empty"""
        model_client_config = self._create_model_client_config()
        model_config = self._create_model_config("wan2.6-t2v")

        model = Model(
            model_config=model_config,
            model_client_config=model_client_config
        )

        messages = [
            UserMessage(content="")  # Empty content
        ]

        from openjiuwen.core.common.exception.errors import ModelError
        with self.assertRaises(ModelError) as context:
            await model.generate_video(messages=messages)

        self.assertIn("non-empty", str(context.exception))
        logger.info(f"Validation error caught as expected: {context.exception}")


if __name__ == "__main__":
    unittest.main()

