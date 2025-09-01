import os
import importlib.util
import asyncio

print("使用前请根据doc/performance_testerer.md的说明准备配置。")


def list_performance_tester_modules():
    performance_tester_dir = os.path.join(
        os.path.dirname(__file__), "performance_tester"
    )
    modules = []
    for file in os.listdir(performance_tester_dir):
        if file.endswith(".py"):
            modules.append(file[:-3])
    return modules


async def load_and_execute_module(module_name):
    module_path = os.path.join(
        os.path.dirname(__file__), "performance_tester", f"{module_name}.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def _check_ollama_service(self, base_url: str, model_name: str) -> bool:
        """异步检查Ollama服务状态"""
        async with aiohttp.ClientSession() as session:
            try:
                # 检查服务是否可用
                async with session.get(f"{base_url}/api/version") as response:
                    if response.status != 200:
                        print(f"🚫 Ollama服务未启动或无法访问: {base_url}")
                        return False

                # 检查模型是否存在
                async with session.get(f"{base_url}/api/tags") as response:
                    if response.status == 200:
                        data = await response.json()
                        models = data.get("models", [])
                        if not any(model["name"] == model_name for model in models):
                            print(
                                f"🚫 Ollama模型 {model_name} 未找到，请先使用 ollama pull {model_name} 下载"
                            )
                            return False
                    else:
                        print(f"🚫 无法获取Ollama模型列表")
                        return False
                return True
            except Exception as e:
                print(f"🚫 无法连接到Ollama服务: {str(e)}")
                return False

    async def _test_tts(self, tts_name: str, config: Dict) -> Dict:
        """异步测试单个TTS性能"""
        try:
            logging.getLogger("core.providers.tts.base").setLevel(logging.WARNING)

            token_fields = ["access_token", "api_key", "token"]
            if any(
                field in config
                and any(x in config[field] for x in ["你的", "placeholder"])
                for field in token_fields
            ):
                print(f"⏭️  TTS {tts_name} 未配置access_token/api_key，已跳过")
                return {"name": tts_name, "type": "tts", "errors": 1}

            module_type = config.get("type", tts_name)
            tts = create_tts_instance(module_type, config, delete_audio_file=True)

            print(f"🎵 测试 TTS: {tts_name}")

            tmp_file = tts.generate_filename()
            await tts.text_to_speak("连接测试", tmp_file)

            if not tmp_file or not os.path.exists(tmp_file):
                print(f"❌ {tts_name} 连接失败")
                return {"name": tts_name, "type": "tts", "errors": 1}

            total_time = 0
            test_count = len(self.test_sentences[:2])

            for i, sentence in enumerate(self.test_sentences[:2], 1):
                start = time.time()
                tmp_file = tts.generate_filename()
                await tts.text_to_speak(sentence, tmp_file)
                duration = time.time() - start
                total_time += duration

                if tmp_file and os.path.exists(tmp_file):
                    print(f"✓ {tts_name} [{i}/{test_count}]")
                else:
                    print(f"✗ {tts_name} [{i}/{test_count}]")
                    return {"name": tts_name, "type": "tts", "errors": 1}

            return {
                "name": tts_name,
                "type": "tts",
                "avg_time": total_time / test_count,
                "errors": 0,
            }

        except Exception as e:
            print(f"⚠️ {tts_name} 测试失败: {str(e)}")
            return {"name": tts_name, "type": "tts", "errors": 1}

    async def _test_stt(self, stt_name: str, config: Dict) -> Dict:
        """异步测试单个STT性能"""
        try:
            logging.getLogger("core.providers.asr.base").setLevel(logging.WARNING)
            token_fields = ["access_token", "api_key", "token"]
            if any(
                field in config
                and any(x in config[field] for x in ["你的", "placeholder"])
                for field in token_fields
            ):
                print(f"⏭️  STT {stt_name} 未配置access_token/api_key，已跳过")
                return {"name": stt_name, "type": "stt", "errors": 1}

            module_type = config.get("type", stt_name)
            stt = create_stt_instance(module_type, config, delete_audio_file=True)
            stt.audio_format = "pcm"

            print(f"🎵 测试 STT: {stt_name}")

            text, _ = await stt.speech_to_text([self.test_wav_list[0]], "1")

            if text is None:
                print(f"❌ {stt_name} 连接失败")
                return {"name": stt_name, "type": "stt", "errors": 1}

            total_time = 0
            test_count = len(self.test_wav_list)

            for i, sentence in enumerate(self.test_wav_list, 1):
                start = time.time()
                text, _ = await stt.speech_to_text([sentence], "1")
                duration = time.time() - start
                total_time += duration

                if text:
                    print(f"✓ {stt_name} [{i}/{test_count}]")
                else:
                    print(f"✗ {stt_name} [{i}/{test_count}]")
                    return {"name": stt_name, "type": "stt", "errors": 1}

            return {
                "name": stt_name,
                "type": "stt",
                "avg_time": total_time / test_count,
                "errors": 0,
            }

        except Exception as e:
            print(f"⚠️ {stt_name} 测试失败: {str(e)}")
            return {"name": stt_name, "type": "stt", "errors": 1}

    async def _test_llm(self, llm_name: str, config: Dict) -> Dict:
        """异步测试单个LLM性能"""
        try:
            # 对于Ollama，跳过api_key检查并进行特殊处理
            if llm_name == "Ollama":
                base_url = config.get("base_url", "http://localhost:11434")
                model_name = config.get("model_name")
                if not model_name:
                    print(f"🚫 Ollama未配置model_name")
                    return {"name": llm_name, "type": "llm", "errors": 1}

                if not await self._check_ollama_service(base_url, model_name):
                    return {"name": llm_name, "type": "llm", "errors": 1}
            else:
                if "api_key" in config and any(
                    x in config["api_key"] for x in ["你的", "placeholder", "sk-xxx"]
                ):
                    print(f"🚫 跳过未配置的LLM: {llm_name}")
                    return {"name": llm_name, "type": "llm", "errors": 1}

            # 获取实际类型（兼容旧配置）
            module_type = config.get("type", llm_name)
            llm = create_llm_instance(module_type, config)

            # 统一使用UTF-8编码
            test_sentences = [
                s.encode("utf-8").decode("utf-8") for s in self.test_sentences
            ]

            # 创建所有句子的测试任务
            sentence_tasks = []
            for sentence in test_sentences:
                sentence_tasks.append(
                    self._test_single_sentence(llm_name, llm, sentence)
                )

            # 并发执行所有句子测试
            sentence_results = await asyncio.gather(*sentence_tasks)

            # 处理结果
            valid_results = [r for r in sentence_results if r is not None]
            if not valid_results:
                print(f"⚠️  {llm_name} 无有效数据，可能配置错误")
                return {"name": llm_name, "type": "llm", "errors": 1}

            first_token_times = [r["first_token_time"] for r in valid_results]
            response_times = [r["response_time"] for r in valid_results]

            # 过滤异常数据
            mean = statistics.mean(response_times)
            stdev = statistics.stdev(response_times) if len(response_times) > 1 else 0
            filtered_times = [t for t in response_times if t <= mean + 3 * stdev]

            if len(filtered_times) < len(test_sentences) * 0.5:
                print(f"⚠️  {llm_name} 有效数据不足，可能网络不稳定")
                return {"name": llm_name, "type": "llm", "errors": 1}

            return {
                "name": llm_name,
                "type": "llm",
                "avg_response": sum(response_times) / len(response_times),
                "avg_first_token": sum(first_token_times) / len(first_token_times),
                "std_first_token": (
                    statistics.stdev(first_token_times)
                    if len(first_token_times) > 1
                    else 0
                ),
                "std_response": (
                    statistics.stdev(response_times) if len(response_times) > 1 else 0
                ),
                "errors": 0,
            }
        except Exception as e:
            print(f"LLM {llm_name} 测试失败: {str(e)}")
            return {"name": llm_name, "type": "llm", "errors": 1}

    async def _test_single_sentence(self, llm_name: str, llm, sentence: str) -> Dict:
        """测试单个句子的性能"""
        try:
            print(f"📝 {llm_name} 开始测试: {sentence[:20]}...")
            sentence_start = time.time()
            first_token_received = False
            first_token_time = None

            async def process_response():
                nonlocal first_token_received, first_token_time
                for chunk in llm.response(
                    "perf_test", [{"role": "user", "content": sentence}]
                ):
                    if not first_token_received and chunk.strip() != "":
                        first_token_time = time.time() - sentence_start
                        first_token_received = True
                        print(f"✓ {llm_name} 首个Token: {first_token_time:.3f}s")
                    yield chunk

            response_chunks = []
            async for chunk in process_response():
                response_chunks.append(chunk)

            response_time = time.time() - sentence_start
            print(f"✓ {llm_name} 完成响应: {response_time:.3f}s")

            if first_token_time is None:
                first_token_time = (
                    response_time  # 如果没有检测到first token，使用总响应时间
                )

            return {
                "name": llm_name,
                "type": "llm",
                "first_token_time": first_token_time,
                "response_time": response_time,
            }
        except Exception as e:
            print(f"⚠️ {llm_name} 句子测试失败: {str(e)}")
            return None

    def _generate_combinations(self):
        """生成最佳组合建议"""
        valid_llms = [
            k
            for k, v in self.results["llm"].items()
            if v["errors"] == 0 and v["avg_first_token"] >= 0.05
        ]
        valid_tts = [k for k, v in self.results["tts"].items() if v["errors"] == 0]
        valid_stt = [k for k, v in self.results["stt"].items() if v["errors"] == 0]

        # 找出基准值
        min_first_token = (
            min([self.results["llm"][llm]["avg_first_token"] for llm in valid_llms])
            if valid_llms
            else 1
        )
        min_tts_time = (
            min([self.results["tts"][tts]["avg_time"] for tts in valid_tts])
            if valid_tts
            else 1
        )
        min_stt_time = (
            min([self.results["stt"][stt]["avg_time"] for stt in valid_stt])
            if valid_stt
            else 1
        )

        for llm in valid_llms:
            for tts in valid_tts:
                for stt in valid_stt:
                    # 计算相对性能分数（越小越好）
                    llm_score = (
                        self.results["llm"][llm]["avg_first_token"] / min_first_token
                    )
                    tts_score = self.results["tts"][tts]["avg_time"] / min_tts_time
                    stt_score = self.results["stt"][stt]["avg_time"] / min_stt_time

                    # 计算稳定性分数（标准差/平均值，越小越稳定）
                    llm_stability = (
                        self.results["llm"][llm]["std_first_token"]
                        / self.results["llm"][llm]["avg_first_token"]
                    )

                    # 综合得分（考虑性能和稳定性）
                    # LLM得分： 性能权重(70%) + 稳定性权重(30%)
                    llm_final_score = llm_score * 0.7 + llm_stability * 0.3

                    # 总分 = LLM得分(70%) + TTS得分(30%) + STT得分(30%)
                    total_score = (
                        llm_final_score * 0.7 + tts_score * 0.3 + stt_score * 0.3
                    )

                    self.results["combinations"].append(
                        {
                            "llm": llm,
                            "tts": tts,
                            "stt": stt,
                            "score": total_score,
                            "details": {
                                "llm_first_token": self.results["llm"][llm][
                                    "avg_first_token"
                                ],
                                "llm_stability": llm_stability,
                                "tts_time": self.results["tts"][tts]["avg_time"],
                                "stt_time": self.results["stt"][stt]["avg_time"],
                            },
                        }
                    )

        # 分数越小越好
        self.results["combinations"].sort(key=lambda x: x["score"])

    def _print_results(self):
        """打印测试结果"""
        llm_table = []
        for name, data in self.results["llm"].items():
            if data["errors"] == 0:
                stability = data["std_first_token"] / data["avg_first_token"]
                llm_table.append(
                    [
                        name,  # 不需要固定宽度，让tabulate自己处理对齐
                        f"{data['avg_first_token']:.3f}秒",
                        f"{data['avg_response']:.3f}秒",
                        f"{stability:.3f}",
                    ]
                )

        if llm_table:
            print("\nLLM 性能排行:\n")
            print(
                tabulate(
                    llm_table,
                    headers=["模型名称", "首字耗时", "总耗时", "稳定性"],
                    tablefmt="github",
                    colalign=("left", "right", "right", "right"),
                    disable_numparse=True,
                )
            )

    if hasattr(module, "main"):
        main_func = module.main
        if asyncio.iscoroutinefunction(main_func):
            await main_func()
        else:
            main_func()
    else:
        print(f"模块 {module_name} 中没有找到 main 函数。")


def get_module_description(module_name):
    module_path = os.path.join(
        os.path.dirname(__file__), "performance_tester", f"{module_name}.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "description", "暂无描述")


def main():
    modules = list_performance_tester_modules()
    if not modules:
        print("performance_tester 目录中没有可用的性能测试工具。")
        return

    print("可用的性能测试工具：")
    for idx, module in enumerate(modules, 1):
        description = get_module_description(module)
        print(f"{idx}. {module} - {description}")

    try:
        choice = int(input("请选择要调用的性能测试工具编号：")) - 1
        if 0 <= choice < len(modules):
            asyncio.run(load_and_execute_module(modules[choice]))
        else:
            print("无效的选择。")
    except ValueError:
        print("请输入有效的数字。")


if __name__ == "__main__":
    main()
