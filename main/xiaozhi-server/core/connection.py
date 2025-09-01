import os
import copy
import json
import subprocess
import sys
import uuid
import time
import queue
import asyncio
import traceback

import threading
import websockets
from typing import Dict, Any
from plugins_func.loadplugins import auto_import_modules
from config.logger import setup_logging
from core.utils.dialogue import Message, Dialogue
from core.handle.textHandle import handleTextMessage
from core.utils.util import (
    get_string_no_punctuation_or_emoji,
    extract_json_from_string,
    initialize_modules,
    check_vad_update,
    check_asr_update,
    filter_sensitive_info,
)
from typing import Dict, Any
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from core.utils.modules_initialize import (
    initialize_modules,
    initialize_tts,
    initialize_asr,
)
from core.handle.reportHandle import report
from core.providers.tts.default import DefaultTTS
from core.handle.sendAudioHandle import sendAudioMessage
from core.handle.receiveAudioHandle import handleAudioMessage
from core.handle.functionHandler import FunctionHandler
from core.utils.dialogue import Message, Dialogue
from core.providers.asr.dto.dto import InterfaceType
from core.handle.textHandle import handleTextMessage
from core.providers.tools.unified_tool_handler import UnifiedToolHandler
from plugins_func.loadplugins import auto_import_modules
from plugins_func.register import Action, ActionResponse
from core.auth import AuthMiddleware, AuthenticationError
from core.mcp.manager import MCPManager
from config.config_loader import get_private_config_from_api
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
from config.logger import setup_logging, build_module_string, create_connection_logger
from config.manage_api_client import DeviceNotFoundException, DeviceBindException
from core.utils.output_counter import add_device_output
from core.utils.prompt_manager import PromptManager
from core.utils.voiceprint_provider import VoiceprintProvider
from core.utils import textUtils
from core.handle.reportHandle import enqueue_tts_report, report

TAG = __name__

auto_import_modules("plugins_func.functions")


class TTSException(RuntimeError):
    pass


class ConnectionHandler:
    def __init__(
        self,
        config: Dict[str, Any],
        _vad,
        _asr,
        _llm,
        _tts,
        _memory,
        _intent,
        server=None,
    ):
        self.common_config = config
        self.config = copy.deepcopy(config)
        self.session_id = str(uuid.uuid4())
        self.logger = setup_logging()
        self.server = server  # 保存server实例的引用

        self.auth = AuthMiddleware(config)
        self.need_bind = False
        self.bind_code = None
        self.read_config_from_api = self.config.get("read_config_from_api", False)

        self.websocket = None
        self.headers = None
        self.device_id = None
        self.client_ip = None
        self.prompt = None
        self.welcome_msg = None
        self.max_output_size = 0
        self.chat_history_conf = 0

        # 客户端状态相关
        self.client_abort = False
        self.client_listen_mode = "auto"

        # 线程任务相关
        self.loop = asyncio.get_event_loop()
        self.stop_event = threading.Event()
        self.tts_queue = queue.Queue()
        self.audio_play_queue = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=10)

        # 上报线程
        self.report_queue = queue.Queue()
        self.report_thread = None
        # TODO(haotian): 2025/5/12 可以通过修改此处，调节asr的上报和tts的上报
        self.report_asr_enable = self.read_config_from_api
        self.report_tts_enable = self.read_config_from_api

        # 依赖的组件
        self.vad = None
        self.asr = None
        self._asr = _asr
        self._vad = _vad
        self.llm = _llm
        self.tts = _tts
        self.memory = _memory
        self.intent = _intent

        # 为每个连接单独管理声纹识别
        self.voiceprint_provider = None

        # vad相关变量
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_have_voice_last_time = 0.0
        self.client_no_voice_last_time = 0.0
        self.client_voice_stop = False
        self.client_voice_window = deque(maxlen=5)
        self.last_is_voice = False

        # asr相关变量
        self.asr_audio = []
        self.asr_server_receive = True

        # llm相关变量
        self.llm_finish_task = False
        self.dialogue = Dialogue()

        # tts相关变量
        self.sentence_id = None
        # 处理TTS响应没有文本返回
        self.tts_MessageText = ""

        # iot相关变量
        self.iot_descriptors = {}
        self.func_handler = None

        self.cmd_exit = self.config["exit_commands"]

        # 是否在聊天结束后关闭连接
        self.close_after_chat = False
        self.load_function_plugin = False
        self.intent_type = "nointent"

        self.timeout_task = None
        self.timeout_seconds = (
            int(self.config.get("close_connection_no_voice_time", 120)) + 60
        )  # 在原来第一道关闭的基础上加60秒，进行二道关闭

        self.audio_format = "opus"

        # 初始化提示词管理器
        self.prompt_manager = PromptManager(config, self.logger)

    async def handle_connection(self, ws):
        try:
            # 获取并验证headers
            self.headers = dict(ws.request.headers)

            if self.headers.get("device-id", None) is None:
                # 尝试从 URL 的查询参数中获取 device-id
                from urllib.parse import parse_qs, urlparse

                # 从 WebSocket 请求中获取路径
                request_path = ws.request.path
                if not request_path:
                    self.logger.bind(tag=TAG).error("无法获取请求路径")
                    return
                parsed_url = urlparse(request_path)
                query_params = parse_qs(parsed_url.query)
                if "device-id" in query_params:
                    self.headers["device-id"] = query_params["device-id"][0]
                    self.headers["client-id"] = query_params["client-id"][0]
                else:
                    await ws.send("端口正常，如需测试连接，请使用test_page.html")
                    await self.close(ws)
                    return
            real_ip = self.headers.get("x-real-ip") or self.headers.get(
                "x-forwarded-for"
            )
            if real_ip:
                self.client_ip = real_ip.split(",")[0].strip()
            else:
                self.client_ip = ws.remote_address[0]
            self.logger.bind(tag=TAG).info(
                f"{self.client_ip} conn - Headers: {self.headers}"
            )

            # 进行认证
            await self.auth.authenticate(self.headers)

            # 认证通过,继续处理
            self.websocket = ws
            self.device_id = self.headers.get("device-id", None)

            # 启动超时检查任务
            self.timeout_task = asyncio.create_task(self._check_timeout())

            self.welcome_msg = self.config["xiaozhi"]
            self.welcome_msg["session_id"] = self.session_id

            # 获取差异化配置
            self._initialize_private_config()
            # 异步初始化
            self.executor.submit(self._initialize_components)
            # tts 消化线程
            self.tts_priority_thread = threading.Thread(
                target=self._tts_priority_thread, daemon=True
            )
            self.tts_priority_thread.start()

            # 音频播放 消化线程
            self.audio_play_priority_thread = threading.Thread(
                target=self._audio_play_priority_thread, daemon=True
            )
            self.audio_play_priority_thread.start()

            try:
                async for message in self.websocket:
                    await self._route_message(message)
            except websockets.exceptions.ConnectionClosed:
                self.logger.bind(tag=TAG).info("客户端断开连接")

        except AuthenticationError as e:
            self.logger.bind(tag=TAG).error(f"Authentication failed: {str(e)}")
            return
        except Exception as e:
            stack_trace = traceback.format_exc()
            self.logger.bind(tag=TAG).error(f"Connection error: {str(e)}-{stack_trace}")
            return
        finally:
            try:
                await self._save_and_close(ws)
            except Exception as final_error:
                self.logger.bind(tag=TAG).error(f"最终清理时出错: {final_error}")
                # 确保即使保存记忆失败，也要关闭连接
                try:
                    await self.close(ws)
                except Exception as close_error:
                    self.logger.bind(tag=TAG).error(
                        f"强制关闭连接时出错: {close_error}"
                    )

    async def _save_and_close(self, ws):
        """保存记忆并关闭连接"""
        try:
            if self.memory:
                # 使用线程池异步保存记忆
                def save_memory_task():
                    try:
                        # 创建新事件循环（避免与主循环冲突）
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            self.memory.save_memory(self.dialogue.dialogue)
                        )
                    except Exception as e:
                        self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
                    finally:
                        try:
                            loop.close()
                        except Exception:
                            pass

                # 启动线程保存记忆，不等待完成
                threading.Thread(target=save_memory_task, daemon=True).start()
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
        finally:
            # 立即关闭连接，不等待记忆保存完成
            try:
                await self.close(ws)
            except Exception as close_error:
                self.logger.bind(tag=TAG).error(
                    f"保存记忆后关闭连接失败: {close_error}"
                )

    async def reset_timeout(self):
        """重置超时计时器"""
        if self.timeout_task:
            self.timeout_task.cancel()
        self.timeout_task = asyncio.create_task(self._check_timeout())

    async def _route_message(self, message):
        """消息路由"""
        # 重置超时计时器
        await self.reset_timeout()

        if isinstance(message, str):
            await handleTextMessage(self, message)
        elif isinstance(message, bytes):
            await handleAudioMessage(self, message)

    async def handle_restart(self, message):
        """处理服务器重启请求"""
        try:

            self.logger.bind(tag=TAG).info("收到服务器重启指令，准备执行...")

            # 发送确认响应
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "success",
                        "message": "服务器重启中...",
                        "content": {"action": "restart"},
                    }
                )
            )

            # 异步执行重启操作
            def restart_server():
                """实际执行重启的方法"""
                time.sleep(1)
                self.logger.bind(tag=TAG).info("执行服务器重启...")
                subprocess.Popen(
                    [sys.executable, "app.py"],
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    start_new_session=True,
                )
                os._exit(0)

            # 使用线程执行重启避免阻塞事件循环
            threading.Thread(target=restart_server, daemon=True).start()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"重启失败: {str(e)}")
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": f"Restart failed: {str(e)}",
                        "content": {"action": "restart"},
                    }
                )
            )

    def _initialize_components(self):
        try:
            self.selected_module_str = build_module_string(
                self.config.get("selected_module", {})
            )
            self.logger = create_connection_logger(self.selected_module_str)

            """初始化组件"""
            if self.config.get("prompt") is not None:
                user_prompt = self.config["prompt"]
                # 使用快速提示词进行初始化
                prompt = self.prompt_manager.get_quick_prompt(user_prompt)
                self.change_system_prompt(prompt)
                self.logger.bind(tag=TAG).info(
                    f"快速初始化组件: prompt成功 {prompt[:50]}..."
                )

            """初始化本地组件"""
            if self.vad is None:
                self.vad = self._vad
            if self.asr is None:
                self.asr = self._initialize_asr()

            # 初始化声纹识别
            self._initialize_voiceprint()

            # 打开语音识别通道
            asyncio.run_coroutine_threadsafe(
                self.asr.open_audio_channels(self), self.loop
            )
            if self.tts is None:
                self.tts = self._initialize_tts()
            # 打开语音合成通道
            asyncio.run_coroutine_threadsafe(
                self.tts.open_audio_channels(self), self.loop
            )

            """加载记忆"""
            self._initialize_memory()
            """加载意图识别"""
            self._initialize_intent()
            """初始化上报线程"""
            self._init_report_threads()
            """更新系统提示词"""
            self._init_prompt_enhancement()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"实例化组件失败: {e}")

    def _init_prompt_enhancement(self):
        # 更新上下文信息
        self.prompt_manager.update_context_info(self, self.client_ip)
        enhanced_prompt = self.prompt_manager.build_enhanced_prompt(
            self.config["prompt"], self.device_id, self.client_ip
        )
        if enhanced_prompt:
            self.change_system_prompt(enhanced_prompt)
            self.logger.bind(tag=TAG).info("系统提示词已增强更新")

    def _init_report_threads(self):
        """初始化ASR和TTS上报线程"""
        if not self.read_config_from_api or self.need_bind:
            return
        if self.chat_history_conf == 0:
            return
        if self.report_thread is None or not self.report_thread.is_alive():
            self.report_thread = threading.Thread(
                target=self._report_worker, daemon=True
            )
            self.report_thread.start()
            self.logger.bind(tag=TAG).info("TTS上报线程已启动")

    def _initialize_tts(self):
        """初始化TTS"""
        tts = None
        if not self.need_bind:
            tts = initialize_tts(self.config)

        if tts is None:
            tts = DefaultTTS(self.config, delete_audio_file=True)

        return tts

    def _initialize_asr(self):
        """初始化ASR"""
        if self._asr.interface_type == InterfaceType.LOCAL:
            # 如果公共ASR是本地服务，则直接返回
            # 因为本地一个实例ASR，可以被多个连接共享
            asr = self._asr
        else:
            # 如果公共ASR是远程服务，则初始化一个新实例
            # 因为远程ASR，涉及到websocket连接和接收线程，需要每个连接一个实例
            asr = initialize_asr(self.config)

        return asr

    def _initialize_voiceprint(self):
        """为当前连接初始化声纹识别"""
        try:
            voiceprint_config = self.config.get("voiceprint", {})
            if voiceprint_config:
                self.voiceprint_provider = VoiceprintProvider(voiceprint_config)
                self.logger.bind(tag=TAG).info("声纹识别功能已在连接时动态启用")
            else:
                self.logger.bind(tag=TAG).info("声纹识别功能未启用或配置不完整")
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"声纹识别初始化失败: {str(e)}")

    def _initialize_private_config(self):
        """如果是从配置文件获取，则进行二次实例化"""
        if not self.read_config_from_api:
            return
        """从接口获取差异化的配置进行二次实例化，非全量重新实例化"""
        try:
            begin_time = time.time()
            private_config = get_private_config_from_api(
                self.config,
                self.headers.get("device-id"),
                self.headers.get("client-id", self.headers.get("device-id")),
            )
            private_config["delete_audio"] = bool(self.config.get("delete_audio", True))
            self.logger.bind(tag=TAG).info(
                f"{time.time() - begin_time} 秒，获取差异化配置成功: {json.dumps(filter_sensitive_info(private_config), ensure_ascii=False)}"
            )
        except DeviceNotFoundException as e:
            self.need_bind = True
            private_config = {}
        except DeviceBindException as e:
            self.need_bind = True
            self.bind_code = e.bind_code
            private_config = {}
        except Exception as e:
            self.need_bind = True
            self.logger.bind(tag=TAG).error(f"获取差异化配置失败: {e}")
            private_config = {}

        init_llm, init_tts, init_memory, init_intent = (
            False,
            False,
            False,
            False,
        )

        init_vad = check_vad_update(self.common_config, private_config)
        init_asr = check_asr_update(self.common_config, private_config)

        if private_config.get("TTS", None) is not None:
            init_tts = True
            self.config["TTS"] = private_config["TTS"]
            self.config["selected_module"]["TTS"] = private_config["selected_module"][
                "TTS"
            ]
        if private_config.get("LLM", None) is not None:
            init_llm = True
            self.config["LLM"] = private_config["LLM"]
            self.config["selected_module"]["LLM"] = private_config["selected_module"][
                "LLM"
            ]
        if private_config.get("VLLM", None) is not None:
            self.config["VLLM"] = private_config["VLLM"]
            self.config["selected_module"]["VLLM"] = private_config["selected_module"][
                "VLLM"
            ]
        if private_config.get("Memory", None) is not None:
            init_memory = True
            self.config["Memory"] = private_config["Memory"]
            self.config["selected_module"]["Memory"] = private_config[
                "selected_module"
            ]["Memory"]
        if private_config.get("Intent", None) is not None:
            init_intent = True
            self.config["Intent"] = private_config["Intent"]
            self.config["selected_module"]["Intent"] = private_config[
                "selected_module"
            ]["Intent"]
        if private_config.get("prompt", None) is not None:
            self.config["prompt"] = private_config["prompt"]
        # 获取声纹信息
        if private_config.get("voiceprint", None) is not None:
            self.config["voiceprint"] = private_config["voiceprint"]
        if private_config.get("summaryMemory", None) is not None:
            self.config["summaryMemory"] = private_config["summaryMemory"]
        if private_config.get("device_max_output_size", None) is not None:
            self.max_output_size = int(private_config["device_max_output_size"])
        if private_config.get("chat_history_conf", None) is not None:
            self.chat_history_conf = int(private_config["chat_history_conf"])
        try:
            modules = initialize_modules(
                self.logger,
                private_config,
                init_vad,
                init_asr,
                init_llm,
                init_tts,
                init_memory,
                init_intent,
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"初始化组件失败: {e}")
            modules = {}
        if modules.get("tts", None) is not None:
            self.tts = modules["tts"]
        if modules.get("vad", None) is not None:
            self.vad = modules["vad"]
        if modules.get("asr", None) is not None:
            self.asr = modules["asr"]
        if modules.get("llm", None) is not None:
            self.llm = modules["llm"]
        if modules.get("intent", None) is not None:
            self.intent = modules["intent"]
        if modules.get("memory", None) is not None:
            self.memory = modules["memory"]

    def _initialize_memory(self):
        """初始化记忆模块"""
        self.memory.init_memory(
            role_id=self.device_id,
            llm=self.llm,
            summary_memory=self.config.get("summaryMemory", None),
            save_to_file=not self.read_config_from_api,
        )

    def _initialize_intent(self):
        self.intent_type = self.config["Intent"][
            self.config["selected_module"]["Intent"]
        ]["type"]
        if self.intent_type == "function_call" or self.intent_type == "intent_llm":
            self.load_function_plugin = True
        """初始化意图识别模块"""
        # 获取意图识别配置
        intent_config = self.config["Intent"]
        intent_type = self.config["Intent"][self.config["selected_module"]["Intent"]][
            "type"
        ]

        # 如果使用 nointent，直接返回
        if intent_type == "nointent":
            return
        # 使用 intent_llm 模式
        elif intent_type == "intent_llm":
            intent_llm_name = intent_config[self.config["selected_module"]["Intent"]][
                "llm"
            ]

            if intent_llm_name and intent_llm_name in self.config["LLM"]:
                # 如果配置了专用LLM，则创建独立的LLM实例
                from core.utils import llm as llm_utils

                intent_llm_config = self.config["LLM"][intent_llm_name]
                intent_llm_type = intent_llm_config.get("type", intent_llm_name)
                intent_llm = llm_utils.create_instance(
                    intent_llm_type, intent_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"为意图识别创建了专用LLM: {intent_llm_name}, 类型: {intent_llm_type}"
                )
                self.intent.set_llm(intent_llm)
            else:
                # 否则使用主LLM
                self.intent.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

        """加载插件"""
        self.func_handler = FunctionHandler(self)
        self.mcp_manager = MCPManager(self)

        """加载MCP工具"""
        asyncio.run_coroutine_threadsafe(
            self.mcp_manager.initialize_servers(), self.loop
        )

    def change_system_prompt(self, prompt):
        self.prompt = prompt
        # 更新系统prompt至上下文
        self.dialogue.update_system_message(self.prompt)

    def _process_text_segment(self, current_text, processed_chars, text_index):
        """处理文本分段，根据标点符号或字符长度进行分段"""
        # 查找最后一个有效标点
        punctuations = ("。", ".", "？", "?", "！", "!", "；", ";", "：")
        last_punct_pos = -1
        number_flag = True
        for punct in punctuations:
            pos = current_text.rfind(punct)
            prev_char = current_text[pos - 1] if pos - 1 >= 0 else ""
            # 如果.前面是数字统一判断为小数
            if prev_char.isdigit() and punct == ".":
                number_flag = False
            if pos > last_punct_pos and number_flag:
                last_punct_pos = pos

        # 如果找到标点符号，按标点符号分段
        if last_punct_pos != -1:
            segment_text_raw = current_text[: last_punct_pos + 1]
            segment_text = get_string_no_punctuation_or_emoji(segment_text_raw)
            if segment_text:
                text_index += 1
                self.recode_first_last_text(segment_text, text_index)
                future = self.executor.submit(
                    self.speak_and_play, segment_text, text_index
                )
                self.tts_queue.put(future)
                return len(segment_text_raw), text_index
        # 如果没有找到标点符号，但文本长度超过10个字符，也进行分段
        elif len(current_text) > 10:
            segment_text_raw = current_text[:10]
            segment_text = get_string_no_punctuation_or_emoji(segment_text_raw)
            if segment_text:
                text_index += 1
                self.recode_first_last_text(segment_text, text_index)
                future = self.executor.submit(
                    self.speak_and_play, segment_text, text_index
                )
                self.tts_queue.put(future)
                return len(segment_text_raw), text_index
        
        return 0, text_index

    def chat(self, query, depth=0):
        self.logger.bind(tag=TAG).info(f"大模型收到用户消息: {query}")
        self.llm_finish_task = False

        # 为最顶层时新建会话ID和发送FIRST请求
        if depth == 0:
            self.sentence_id = str(uuid.uuid4().hex)
            self.dialogue.put(Message(role="user", content=query))
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=self.sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )

        response_message = []
        processed_chars = 0  # 跟踪已处理的字符位置
        try:
            # 使用带记忆的对话
            memory_str = None
            if self.memory is not None:
                future = asyncio.run_coroutine_threadsafe(
                    self.memory.query_memory(query), self.loop
                )
                memory_str = future.result()

            if self.intent_type == "function_call" and functions is not None:
                # 使用支持functions的streaming接口
                llm_responses = self.llm.response_with_functions(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str, self.config.get("voiceprint", {})
                    ),
                    functions=functions,
                )
            else:
                llm_responses = self.llm.response(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str, self.config.get("voiceprint", {})
                    ),
                )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")
            return None

        self.llm_finish_task = False
        text_index = 0
        for content in llm_responses:
            response_message.append(content)
            if self.client_abort:
                break

            # 合并当前全部文本并处理未分割部分
            full_text = "".join(response_message)
            current_text = full_text[processed_chars:]  # 从未处理的位置开始

            # 查找最后一个有效标点
            punctuations = ("。", ".", "？", "?", "！", "!", "；", ";", "：")
            last_punct_pos = -1
            number_flag = True
            for punct in punctuations:
                pos = current_text.rfind(punct)
                prev_char = current_text[pos - 1] if pos - 1 >= 0 else ""
                # 如果.前面是数字统一判断为小数
                if prev_char.isdigit() and punct == ".":
                    number_flag = False
                if pos > last_punct_pos and number_flag:
                    last_punct_pos = pos

            # 找到分割点则处理
            if last_punct_pos != -1:
                segment_text_raw = current_text[: last_punct_pos + 1]
                segment_text = get_string_no_punctuation_or_emoji(segment_text_raw)
                if segment_text:
                    # 强制设置空字符，测试TTS出错返回语音的健壮性
                    # if text_index % 2 == 0:
                    #     segment_text = " "
                    text_index += 1
                    self.recode_first_last_text(segment_text, text_index)
                    future = self.executor.submit(
                        self.speak_and_play, segment_text, text_index
                    )
                    self.tts_queue.put(future)
                    processed_chars += len(segment_text_raw)  # 更新已处理字符位置

        # 处理最后剩余的文本
        full_text = "".join(response_message)
        remaining_text = full_text[processed_chars:]
        if remaining_text:
            segment_text = get_string_no_punctuation_or_emoji(remaining_text)
            if segment_text:
                text_index += 1
                self.recode_first_last_text(segment_text, text_index)
                future = self.executor.submit(
                    self.speak_and_play, segment_text, text_index
                )
                self.tts_queue.put((future, text_index))

        self.llm_finish_task = True
        self.dialogue.put(Message(role="assistant", content="".join(response_message)))
        self.logger.bind(tag=TAG).debug(
            json.dumps(self.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False)
        )
        return True

    def chat_with_function_calling(self, query, tool_call=False):
        self.logger.bind(tag=TAG).debug(f"Chat with function calling start: {query}")
        """Chat with function calling for intent detection using streaming"""

        if not tool_call:
            self.dialogue.put(Message(role="user", content=query))

        # Define intent functions
        functions = None
        if hasattr(self, "func_handler"):
            functions = self.func_handler.get_functions()
        response_message = []
        processed_chars = 0  # 跟踪已处理的字符位置

        try:
            start_time = time.time()

            # 使用带记忆的对话
            memory_str = None
            if self.memory is not None:
                future = asyncio.run_coroutine_threadsafe(
                    self.memory.query_memory(query), self.loop
                )
                memory_str = future.result()

            # self.logger.bind(tag=TAG).info(f"对话记录: {self.dialogue.get_llm_dialogue_with_memory(memory_str)}")

            # 使用支持functions的streaming接口
            llm_responses = self.llm.response_with_functions(
                self.session_id,
                self.dialogue.get_llm_dialogue_with_memory(memory_str),
                functions=functions,
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")
            return None

        self.llm_finish_task = False
        text_index = 0

        # 处理流式响应
        tool_call_flag = False
        function_name = None
        function_id = None
        function_arguments = ""
        content_arguments = ""
        self.client_abort = False
        emotion_flag = True
        for response in llm_responses:
            content, tools_call = response

            if "content" in response:
                content = response["content"]
                tools_call = None
            if content is not None and len(content) > 0:
                content_arguments += content

            if not tool_call_flag and content_arguments.startswith("<tool_call>"):
                # print("content_arguments", content_arguments)
                tool_call_flag = True

            if tools_call is not None:
                tool_call_flag = True
                if tools_call[0].id is not None:
                    function_id = tools_call[0].id
                if tools_call[0].function.name is not None:
                    function_name = tools_call[0].function.name
                if tools_call[0].function.arguments is not None:
                    function_arguments += tools_call[0].function.arguments
            else:
                content = response

            # 在llm回复中获取情绪表情，一轮对话只在开头获取一次
            if emotion_flag and content is not None and content.strip():
                asyncio.run_coroutine_threadsafe(
                    textUtils.get_emotion(self, content),
                    self.loop,
                )
                emotion_flag = False

            if content is not None and len(content) > 0:
                if not tool_call_flag:
                    response_message.append(content)
                    self.tts.tts_text_queue.put(
                        TTSMessageDTO(
                            sentence_id=self.sentence_id,
                            sentence_type=SentenceType.MIDDLE,
                            content_type=ContentType.TEXT,
                            content_detail=content,
                        )
                    )
        # 处理function call
        if tool_call_flag:
            bHasError = False
            if function_id is None:
                a = extract_json_from_string(content_arguments)
                if a is not None:
                    try:
                        content_arguments_json = json.loads(a)
                        function_name = content_arguments_json["name"]
                        function_arguments = json.dumps(
                            content_arguments_json["arguments"], ensure_ascii=False
                        )
                        function_id = str(uuid.uuid4().hex)
                    except Exception as e:
                        bHasError = True
                        response_message.append(a)
                else:
                    bHasError = True
                    response_message.append(content_arguments)
                if bHasError:
                    self.logger.bind(tag=TAG).error(
                        f"function call error: {content_arguments}"
                    )
            if not bHasError:
                # 如需要大模型先处理一轮，添加相关处理后的日志情况
                if len(response_message) > 0:
                    text_buff = "".join(response_message)
                    self.tts_MessageText = text_buff
                    self.dialogue.put(Message(role="assistant", content=text_buff))
                response_message.clear()
                self.logger.bind(tag=TAG).debug(
                    f"function_name={function_name}, function_id={function_id}, function_arguments={function_arguments}"
                )
                function_call_data = {
                    "name": function_name,
                    "id": function_id,
                    "arguments": function_arguments,
                }

                # 处理MCP工具调用
                if self.mcp_manager.is_mcp_tool(function_name):
                    result = self._handle_mcp_tool_call(function_call_data)
                else:
                    # 处理系统函数
                    result = self.func_handler.handle_llm_function_call(
                        self, function_call_data
                    ),
                    self.loop,
                ).result()
                self._handle_function_result(result, function_call_data, depth=depth)

        # 存储对话内容
        if len(response_message) > 0:
            text_buff = "".join(response_message)
            self.tts_MessageText = text_buff
            self.dialogue.put(Message(role="assistant", content=text_buff))
        if depth == 0:
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=self.sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
        self.llm_finish_task = True
        # 使用lambda延迟计算，只有在DEBUG级别时才执行get_llm_dialogue()
        self.logger.bind(tag=TAG).debug(
            lambda: json.dumps(
                self.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False
            )
        )

        return True

    def _handle_mcp_tool_call(self, function_call_data):
        function_arguments = function_call_data["arguments"]
        function_name = function_call_data["name"]
        try:
            args_dict = function_arguments
            if isinstance(function_arguments, str):
                try:
                    args_dict = json.loads(function_arguments)
                except json.JSONDecodeError:
                    self.logger.bind(tag=TAG).error(
                        f"无法解析 function_arguments: {function_arguments}"
                    )
                    return ActionResponse(
                        action=Action.REQLLM, result="参数解析失败", response=""
                    )

            tool_result = asyncio.run_coroutine_threadsafe(
                self.mcp_manager.execute_tool(function_name, args_dict), self.loop
            ).result()
            # meta=None content=[TextContent(type='text', text='北京当前天气:\n温度: 21°C\n天气: 晴\n湿度: 6%\n风向: 西北 风\n风力等级: 5级', annotations=None)] isError=False
            content_text = ""
            if tool_result is not None and tool_result.content is not None:
                for content in tool_result.content:
                    content_type = content.type
                    if content_type == "text":
                        content_text = content.text
                    elif content_type == "image":
                        pass

            if len(content_text) > 0:
                return ActionResponse(
                    action=Action.REQLLM, result=content_text, response=""
                )

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"MCP工具调用错误: {e}")
            return ActionResponse(
                action=Action.REQLLM, result="工具调用出错", response=""
            )

        return ActionResponse(action=Action.REQLLM, result="工具调用出错", response="")

    def _handle_function_result(self, result, function_call_data, depth):
        if result.action == Action.RESPONSE:  # 直接回复前端
            text = result.response
            self.recode_first_last_text(text, text_index)
            future = self.executor.submit(self.speak_and_play, text, text_index)
            self.tts_queue.put((future, text_index))
            self.dialogue.put(Message(role="assistant", content=text))
        elif result.action == Action.REQLLM:  # 调用函数后再请求llm生成回复
            text = result.result
            if text is not None and len(text) > 0:
                function_id = function_call_data["id"]
                function_name = function_call_data["name"]
                function_arguments = function_call_data["arguments"]
                self.dialogue.put(
                    Message(
                        role="assistant",
                        tool_calls=[
                            {
                                "id": function_id,
                                "function": {
                                    "arguments": "{}" if function_arguments == "" else function_arguments,
                                    "name": function_name,
                                },
                                "type": "function",
                                "index": 0,
                            }
                        ],
                    )
                )

                self.dialogue.put(
                    Message(
                        role="tool",
                        tool_call_id=(
                            str(uuid.uuid4()) if function_id is None else function_id
                        ),
                        content=text,
                    )
                )
                self.chat(text, depth=depth + 1)
        elif result.action == Action.NOTFOUND or result.action == Action.ERROR:
            text = result.result
            self.recode_first_last_text(text, text_index)
            future = self.executor.submit(self.speak_and_play, text, text_index)
            self.tts_queue.put((future, text_index))
            self.dialogue.put(Message(role="assistant", content=text))
        else:
            pass

    def _tts_priority_thread(self):
        while not self.stop_event.is_set():
            text = None
            try:
                try:
                    item = self.tts_queue.get(timeout=1)
                    if item is None:
                        continue
                    future, text_index = item  # 解包获取 Future 和 text_index
                except queue.Empty:
                    if self.stop_event.is_set():
                        break
                    continue
                if future is None:
                    continue
                text = None
                audio_datas, tts_file = [], None
                try:
                    self.logger.bind(tag=TAG).debug("正在处理TTS任务...")
                    tts_timeout = int(self.config.get("tts_timeout", 10))
                    tts_file, text, _ = future.result(timeout=tts_timeout)
                    if text is None or len(text) <= 0:
                        self.logger.bind(tag=TAG).error(
                            f"TTS出错：{text_index}: tts text is empty"
                        )
                    elif tts_file is None:
                        self.logger.bind(tag=TAG).error(
                            f"TTS出错： file is empty: {text_index}: {text}"
                        )
                    else:
                        self.logger.bind(tag=TAG).debug(
                            f"TTS生成：文件路径: {tts_file}"
                        )
                        if os.path.exists(tts_file):
                            if self.audio_format == "pcm":
                                audio_datas, _ = self.tts.audio_to_pcm_data(tts_file)
                            else:
                                audio_datas, _ = self.tts.audio_to_opus_data(tts_file)
                            # 在这里上报TTS数据
                            enqueue_tts_report(self, text, audio_datas)
                        else:
                            self.logger.bind(tag=TAG).error(
                                f"TTS出错：文件不存在{tts_file}"
                            )
                except TimeoutError:
                    self.logger.bind(tag=TAG).error("TTS超时")
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"TTS出错: {e}")
                if not self.client_abort:
                    # 如果没有中途打断就发送语音
                    self.audio_play_queue.put((audio_datas, text, text_index))
                if (
                    self.tts.delete_audio_file
                    and tts_file is not None
                    and os.path.exists(tts_file)
                ):
                    os.remove(tts_file)
            except Exception as e:
                self.logger.bind(tag=TAG).error(f"TTS任务处理错误: {e}")
                self.clearSpeakStatus()
                asyncio.run_coroutine_threadsafe(
                    self.websocket.send(
                        json.dumps(
                            {
                                "type": "tts",
                                "state": "stop",
                                "session_id": self.session_id,
                            }
                        )
                    ),
                    self.loop,
                )
                self.logger.bind(tag=TAG).error(
                    f"tts_priority priority_thread: {text} {e}"
                )

    def _audio_play_priority_thread(self):
        while not self.stop_event.is_set():
            text = None
            try:
                try:
                    audio_datas, text, text_index = self.audio_play_queue.get(timeout=1)
                except queue.Empty:
                    if self.stop_event.is_set():
                        break
                    continue
                future = asyncio.run_coroutine_threadsafe(
                    sendAudioMessage(self, audio_datas, text, text_index), self.loop
                )
                future.result()
            except Exception as e:
                self.logger.bind(tag=TAG).error(
                    f"audio_play_priority priority_thread: {text} {e}"
                )

    def _report_worker(self):
        """聊天记录上报工作线程"""
        while not self.stop_event.is_set():
            try:
                # 从队列获取数据，设置超时以便定期检查停止事件
                item = self.report_queue.get(timeout=1)
                if item is None:  # 检测毒丸对象
                    break
                try:
                    # 检查线程池状态
                    if self.executor is None:
                        continue
                    # 提交任务到线程池
                    self.executor.submit(self._process_report, *item)
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"聊天记录上报线程异常: {e}")
                finally:
                    # 标记任务完成
                    self.report_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.bind(tag=TAG).error(f"聊天记录上报工作线程异常: {e}")

        self.logger.bind(tag=TAG).info("聊天记录上报线程已退出")

    def speak_and_play(self, text, text_index=0):
        if text is None or len(text) <= 0:
            self.logger.bind(tag=TAG).info(f"无需tts转换，query为空，{text}")
            return None, text, text_index
        tts_file = self.tts.to_tts(text)
        if tts_file is None:
            self.logger.bind(tag=TAG).error(f"tts转换失败，{text}")
            return None, text, text_index
        self.logger.bind(tag=TAG).debug(f"TTS 文件生成完毕: {tts_file}")
        if self.max_output_size > 0:
            add_device_output(self.headers.get("device-id"), len(text))
        return tts_file, text, text_index

    def clearSpeakStatus(self):
        self.logger.bind(tag=TAG).debug(f"清除服务端讲话状态")
        self.asr_server_receive = True
        self.tts_last_text_index = -1
        self.tts_first_text_index = -1

    def recode_first_last_text(self, text, text_index=0):
        if self.tts_first_text_index == -1:
            self.logger.bind(tag=TAG).info(f"大模型说出第一句话: {text}")
            self.tts_first_text_index = text_index
        self.tts_last_text_index = text_index

    async def close(self, ws=None):
        """资源清理方法"""
        try:
            # 取消超时任务
            if self.timeout_task and not self.timeout_task.done():
                self.timeout_task.cancel()
                try:
                    await self.timeout_task
                except asyncio.CancelledError:
                    pass
                self.timeout_task = None

            # 清理工具处理器资源
            if hasattr(self, "func_handler") and self.func_handler:
                try:
                    await self.func_handler.cleanup()
                except Exception as cleanup_error:
                    self.logger.bind(tag=TAG).error(
                        f"清理工具处理器时出错: {cleanup_error}"
                    )

        # 清理MCP资源
        if hasattr(self, "mcp_manager") and self.mcp_manager:
            await self.mcp_manager.cleanup_all()

        # 触发停止事件
        if self.stop_event:
            self.stop_event.set()

        # 清空任务队列
        self.clear_queues()

        # 关闭WebSocket连接
        try:
            if ws:
                # 安全地检查WebSocket状态并关闭
                try:
                    if hasattr(ws, "closed") and not ws.closed:
                        await ws.close()
                    elif hasattr(ws, "state") and ws.state.name != "CLOSED":
                        await ws.close()
                    else:
                        # 如果没有closed属性，直接尝试关闭
                        await ws.close()
                except Exception:
                    # 如果关闭失败，忽略错误
                    pass
            elif self.websocket:
                try:
                    if (
                        hasattr(self.websocket, "closed")
                        and not self.websocket.closed
                    ):
                        await self.websocket.close()
                    elif (
                        hasattr(self.websocket, "state")
                        and self.websocket.state.name != "CLOSED"
                    ):
                        await self.websocket.close()
                    else:
                        # 如果没有closed属性，直接尝试关闭
                        await self.websocket.close()
                except Exception:
                    # 如果关闭失败，忽略错误
                    pass
        except Exception as ws_error:
            self.logger.bind(tag=TAG).error(f"关闭WebSocket连接时出错: {ws_error}")

        if self.tts:
            await self.tts.close()

        # 最后关闭线程池（避免阻塞）
        if self.executor:
            try:
                self.executor.shutdown(wait=False)
            except Exception as executor_error:
                self.logger.bind(tag=TAG).error(
                    f"关闭线程池时出错: {executor_error}"
                )
            self.executor = None

        self.logger.bind(tag=TAG).info("连接资源已释放")

    def clear_queues(self):
        """清空所有任务队列"""
        self.logger.bind(tag=TAG).debug(
            f"开始清理: TTS队列大小={self.tts_queue.qsize()}, 音频队列大小={self.audio_play_queue.qsize()}"
        )

        # 使用非阻塞方式清空队列
        for q in [self.tts_queue, self.audio_play_queue]:
            if not q:
                continue
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

        self.logger.bind(tag=TAG).debug(
            f"清理结束: TTS队列大小={self.tts_queue.qsize()}, 音频队列大小={self.audio_play_queue.qsize()}"
        )

    def reset_vad_states(self):
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_have_voice_last_time = 0
        self.client_voice_stop = False
        self.logger.bind(tag=TAG).debug("VAD states reset.")

    def chat_and_close(self, text):
        """Chat with the user and then close the connection"""
        try:
            # Use the existing chat method
            self.chat(text)

            # After chat is complete, close the connection
            self.close_after_chat = True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Chat and close error: {str(e)}")

    async def _check_timeout(self):
        """检查连接超时"""
        try:
            while not self.stop_event.is_set():
                # 检查是否超时（只有在时间戳已初始化的情况下）
                if self.last_activity_time > 0.0:
                    current_time = time.time() * 1000
                    if (
                        current_time - self.last_activity_time
                        > self.timeout_seconds * 1000
                    ):
                        if not self.stop_event.is_set():
                            self.logger.bind(tag=TAG).info("连接超时，准备关闭")
                            # 设置停止事件，防止重复处理
                            self.stop_event.set()
                            # 使用 try-except 包装关闭操作，确保不会因为异常而阻塞
                            try:
                                await self.close(self.websocket)
                            except Exception as close_error:
                                self.logger.bind(tag=TAG).error(
                                    f"超时关闭连接时出错: {close_error}"
                                )
                        break
                # 每10秒检查一次，避免过于频繁
                await asyncio.sleep(10)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"超时检查任务出错: {e}")
        finally:
            self.logger.bind(tag=TAG).info("超时检查任务已退出")
