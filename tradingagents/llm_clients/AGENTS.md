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
- `NormalizedChatOpenAI` 负责 Responses API 归一化
- ModelScope 的 `deepseek-ai/DeepSeek-V4-Pro` / `Qwen/Qwen3.5-397B-A17B` 自由文本正常，
  但带 `tool_choice` 的结构化请求返回 `choices: null`（langchain 抛 TypeError）——
  不能放进会服务结构化角色的 fallback 链；`deepseek-ai/DeepSeek-V4-Flash` 与
  `MiniMax/MiniMax-M3` 已被 ModelScope 下架（400 "no provider supported"）
- 新提供商密钥入 `.env`：如 `AGNES_API_KEY`、`MODELSCOPE_API_KEY`、`NVIDIA_API_KEY`；
  远程 Ollama 用 `OLLAMA_BASE_URL`

## 验证

- `uv run python -m pytest tests/ -k "client or capabilities or pricing or rate_limit" -m unit`
