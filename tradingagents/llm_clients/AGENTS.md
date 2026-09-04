# tradingagents/llm_clients

LLM 客户端层：多提供商接入、能力探测、定价与限流。

## 边界

- `factory.py` — 懒加载路由（新增提供商从这里注册）
- `openai_client.py` — OpenAI 兼容提供商（OpenAI、xAI、DeepSeek、Qwen、GLM、OpenRouter、
  Ollama、SenseNova、Agnes AI、ModelScope、NVIDIA NIM、MiniMax）
- 专属客户端：`anthropic_client.py`、`google_client.py`、`azure_client.py`
- `capabilities.py` / `pricing.py`（对应根目录 `pricing.yaml`）/ `rate_limit.py` / `retry_utils.py`

## 已知坑（改动前必读）

- `ChatPromptTemplate` 会剥离 `additional_kwargs`（含 `reasoning_content`）。
  `DeepSeekChatOpenAI` 用以 `message.id` 为键的 sidecar 缓存来跨模板重建存活
- `deepseek-reasoner` 不支持 `tool_choice`：`with_structured_output` 抛
  `NotImplementedError`，上层必须走自由文本回退
- MiniMax M2.x 用 `reasoning_split` 提取 `reasoning_content`（不是 sidecar 模式）
- DeepSeek 系模型（含 sensenova/modelscope 镜像）有峰谷价：峰值 01:00-04:00、06:00-10:00
  UTC 周一至周五，其余半价；`get_price*` 传 `at=` 才启用折扣，`stats_handler` 已接入
- `NormalizedChatOpenAI` 负责 Responses API 归一化
- ModelScope 的 DeepSeek 部署已改名：无日期的 `deepseek-ai/DeepSeek-V4-Flash` / `-Pro`
  已下架（400 "no provider"），新 ID `DeepSeek-V4-Flash-0731` / `DeepSeek-V4-Pro-0813`
  实测自由文本与 `tool_choice` 结构化均正常，已回配到 fallback 链。
  其余实测（2026-08-26）：`Qwen/Qwen3.8-27B` 结构化正常（quick 链可用）；
  `Qwen/Qwen3.5-397B-A17B` 自由文本正常但 `tool_choice` 返回 `choices: null`；
  `Tencent-Hunyuan/Hy3` 连自由文本都返回 `choices: null`；`moonshotai/Kimi-K3` 无推理
  服务（400）、`MiniMax/MiniMax-M3` 已下架（400）——这些都不要放进 fallback 链
- 新提供商密钥入 `.env`：如 `AGNES_API_KEY`、`MODELSCOPE_API_KEY`、`NVIDIA_API_KEY`；
  远程 Ollama 用 `OLLAMA_BASE_URL`
- 每次 LLM 请求带 `llm_request_timeout`（默认 600s，env
  `TRADINGAGENTS_LLM_REQUEST_TIMEOUT`），防止半开连接无限挂起
- 客户端 pacing 按 `provider/model` 作用域（SenseNova Token Plan 2026-08 起按
  积分池滚动 5h/周窗口计量，不同模型费率不同）；配额耗尽类错误不在同档重试，
  直接进 fallback 链下一档

## 验证

- `uv run python -m pytest tests/ -k "client or capabilities or pricing or rate_limit" -m unit`
