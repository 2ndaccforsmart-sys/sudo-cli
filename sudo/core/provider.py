"""Provider registry, factory, and base classes for sudo CLI.

60+ LLM providers across 6 tiers: S (free), A (cheap), B (premium),
C (aggregators), L (local), Z (regional/Asia).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generator, Optional


@dataclass
class ProviderDef:
    """Definition of a single LLM provider."""
    name: str
    display: str
    api_type: str       # 'openai' | 'anthropic' | 'google'
    base_url: str
    env_key: str
    docs_url: str
    website: str
    default_model: str
    tier: str           # S A B C L Z
    free_tier: bool = False
    notes: str = ""


PROVIDER_REGISTRY: dict[str, ProviderDef] = {}
TIER_ORDER = ["S", "A", "B", "C", "L", "Z"]
TIER_LABELS = {
    "S": "✨ Tier S — Free (no credit card)",
    "A": "💰 Tier A — Cheap (<$1/MTok)",
    "B": "💎 Tier B — Premium",
    "C": "🔀 Tier C — Aggregators",
    "L": "🏠 Tier L — Local",
    "Z": "🌍 Tier Z — Regional (Asia)",
}


def _reg(p: ProviderDef) -> None:
    PROVIDER_REGISTRY[p.name] = p


# ── Tier S: Free (no credit card required) ──────────────────────────────────

_reg(ProviderDef("groq", "Groq", "openai",
    "https://api.groq.com/openai/v1", "GROQ_API_KEY",
    "https://console.groq.com/keys", "https://groq.com",
    "llama-3.3-70b-versatile", "S", free_tier=True,
    notes="Fast inference on LPUs"))

_reg(ProviderDef("google/gemini", "Google Gemini", "google",
    "https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY",
    "https://aistudio.google.com/app/apikey", "https://deepmind.google/gemini",
    "gemini-2.0-flash", "S", free_tier=True,
    notes="Free tier with rate limits"))

_reg(ProviderDef("openrouter", "OpenRouter", "openai",
    "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
    "https://openrouter.ai/keys", "https://openrouter.ai",
    "openai/gpt-4o", "S", free_tier=True,
    notes="Routes to many models, free tier available"))

_reg(ProviderDef("cerebras", "Cerebras", "openai",
    "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
    "https://cloud.cerebras.ai/", "https://cerebras.ai",
    "llama3.1-8b", "S", free_tier=True,
    notes="Fast wafer-scale inference"))

_reg(ProviderDef("github", "GitHub Models", "openai",
    "https://models.inference.ai.azure.com", "GITHUB_TOKEN",
    "https://github.com/settings/tokens", "https://github.com/marketplace/models",
    "gpt-4o", "S", free_tier=True,
    notes="Free with GH Copilot subscription"))

_reg(ProviderDef("huggingface", "HuggingFace", "openai",
    "https://api-inference.huggingface.co/v1", "HF_API_KEY",
    "https://huggingface.co/settings/tokens", "https://huggingface.co",
    "mistralai/Mistral-7B-Instruct-v0.3", "S", free_tier=True,
    notes="Community models, rate-limited free tier"))

_reg(ProviderDef("cloudflare", "Cloudflare Workers AI", "openai",
    "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/ai/v1",
    "CLOUDFLARE_API_TOKEN",
    "https://dash.cloudflare.com/profile/api-tokens", "https://ai.cloudflare.com",
    "@cf/meta/llama-3.1-8b-instruct", "S", free_tier=True,
    notes="Free tier with daily limits"))

_reg(ProviderDef("glhf", "GLHF.chat", "openai",
    "https://glhf.chat/api/openai/v1", "GLHF_API_KEY",
    "https://glhf.chat/", "https://glhf.chat",
    "meta-llama/Llama-3.3-70B-Instruct", "S", free_tier=True,
    notes="Free inference for open models"))


# ── Tier A: Cheap (<$1/MTok) ────────────────────────────────────────────────

_reg(ProviderDef("deepseek", "DeepSeek", "openai",
    "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
    "https://platform.deepseek.com/api_keys", "https://deepseek.com",
    "deepseek-chat", "A", notes="~$0.14/M input tokens"))

_reg(ProviderDef("together", "Together AI", "openai",
    "https://api.together.xyz/v1", "TOGETHER_API_KEY",
    "https://api.together.xyz/settings/api-keys", "https://together.ai",
    "mistralai/Mixtral-8x22B-Instruct-v0.1", "A",
    notes="Many open models, competitive pricing"))

_reg(ProviderDef("fireworks", "Fireworks AI", "openai",
    "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY",
    "https://fireworks.ai/api-keys", "https://fireworks.ai",
    "accounts/fireworks/models/llama-v3p1-405b-instruct", "A",
    notes="Fast inference on optimized infra"))

_reg(ProviderDef("deepinfra", "DeepInfra", "openai",
    "https://api.deepinfra.com/v1/openai", "DEEPINFRA_API_KEY",
    "https://deepinfra.com/dash/api_keys", "https://deepinfra.com",
    "meta-llama/Llama-3.3-70B-Instruct", "A",
    notes="Serverless, per-second billing"))

_reg(ProviderDef("mistral", "Mistral AI", "openai",
    "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
    "https://console.mistral.ai/api-keys/", "https://mistral.ai",
    "mistral-large-latest", "A",
    notes="Excellent European models, competitive pricing"))

_reg(ProviderDef("perplexity", "Perplexity AI", "openai",
    "https://api.perplexity.ai", "PERPLEXITY_API_KEY",
    "https://www.perplexity.ai/settings/api", "https://www.perplexity.ai",
    "sonar-pro", "A", notes="Search-grounded models, pay-as-you-go"))

_reg(ProviderDef("sambanova", "SambaNova", "openai",
    "https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY",
    "https://cloud.sambanova.ai/", "https://sambanova.ai",
    "Meta-Llama-3.1-8B-Instruct", "A",
    notes="Fast inference on RDU hardware"))

_reg(ProviderDef("novita", "Novita AI", "openai",
    "https://api.novita.ai/v1", "NOVITA_API_KEY",
    "https://novita.ai/settings", "https://novita.ai",
    "mistralai/Mixtral-8x7B-Instruct-v0.1", "A",
    notes="Cheap GPU inference, serverless"))

_reg(ProviderDef("anyscale", "Anyscale", "openai",
    "https://api.endpoints.anyscale.com/v1", "ANYSCALE_API_KEY",
    "https://console.anyscale.com/v2/api-keys", "https://anyscale.com",
    "mistralai/Mixtral-8x22B-Instruct-v0.1", "A",
    notes="Ray-powered endpoint serving"))

_reg(ProviderDef("replicate", "Replicate", "openai",
    "https://api.replicate.com/v1", "REPLICATE_API_KEY",
    "https://replicate.com/account/api-tokens", "https://replicate.com",
    "meta/meta-llama-3-70b-instruct", "A",
    notes="Pay-per-prediction, many open models"))

_reg(ProviderDef("lepton", "Lepton AI", "openai",
    "https://api.lepton.ai/v1", "LEPTON_API_KEY",
    "https://dashboard.lepton.ai/", "https://lepton.ai",
    "llama3-70b", "A", notes="Serverless GPU platform"))

_reg(ProviderDef("modal", "Modal", "openai",
    "https://api.modal.com/v1", "MODAL_API_KEY",
    "https://modal.com/settings", "https://modal.com",
    "modal-llama-3.1-70b-instruct", "A",
    notes="Serverless cloud functions + GPU"))

_reg(ProviderDef("nvidia/nim", "NVIDIA NIM", "openai",
    "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
    "https://build.nvidia.com/", "https://www.nvidia.com/en-us/ai/",
    "meta/llama-3.1-70b-instruct", "A",
    notes="NVIDIA inference microservices"))

_reg(ProviderDef("baseten", "Baseten", "openai",
    "https://bridge.baseten.co/v1", "BASETEN_API_KEY",
    "https://app.baseten.co/settings/api-keys", "https://baseten.co",
    "meta-llama/Llama-3.3-70B-Instruct", "A",
    notes="Serverless GPU inference, pay-per-second"))

_reg(ProviderDef("neuralmagic", "Neural Magic", "openai",
    "https://api.neuralmagic.com/v1", "NEURALMAGIC_API_KEY",
    "https://neuralmagic.com/accounts/login/", "https://neuralmagic.com",
    "meta-llama/Llama-3.2-8B-Instruct", "A",
    notes="DeepSparse engine, sparsified models"))

_reg(ProviderDef("nvidia/playground", "NVIDIA AI Playground", "openai",
    "https://playground.api.nvidia.com/v1", "NVIDIA_API_KEY",
    "https://build.nvidia.com/", "https://www.nvidia.com/en-us/ai/",
    "meta/llama-3.1-70b-instruct", "A",
    notes="NVIDIA hosted playground, free credits"))


# ── Tier B: Premium ─────────────────────────────────────────────────────────

_reg(ProviderDef("openai", "OpenAI", "openai",
    "https://api.openai.com/v1", "OPENAI_API_KEY",
    "https://platform.openai.com/api-keys", "https://openai.com",
    "gpt-4o", "B", notes="Industry standard, broad model range"))

_reg(ProviderDef("anthropic", "Anthropic", "anthropic",
    "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY",
    "https://console.anthropic.com/", "https://anthropic.com",
    "claude-sonnet-4-20250514", "B",
    notes="Claude models, strong reasoning"))

_reg(ProviderDef("xai/grok", "xAI Grok", "openai",
    "https://api.x.ai/v1", "XAI_API_KEY",
    "https://console.x.ai/", "https://x.ai",
    "grok-2-1212", "B", notes="Grok models via xAI platform"))

_reg(ProviderDef("cohere", "Cohere", "openai",
    "https://api.cohere.com/v1", "COHERE_API_KEY",
    "https://dashboard.cohere.com/api-keys", "https://cohere.com",
    "command-r-plus", "B",
    notes="Command & embedding models, enterprise RAG"))

_reg(ProviderDef("ai21", "AI21 Labs", "openai",
    "https://api.ai21.com/studio/v1", "AI21_API_KEY",
    "https://studio.ai21.com/account/api-keys", "https://ai21.com",
    "jamba-1.5-large", "B",
    notes="Jamba hybrid SSM-Transformer models"))

_reg(ProviderDef("writer", "Writer", "openai",
    "https://api.writer.com/v1", "WRITER_API_KEY",
    "https://app.writer.com/a/developers", "https://writer.com",
    "palmyra-x-004", "B", notes="Enterprise Palmyra models"))

# Cloud platforms (B-tier)
_reg(ProviderDef("azure/openai", "Azure OpenAI", "openai",
    "https://YOUR_RESOURCE.openai.azure.com/v1", "AZURE_OPENAI_API_KEY",
    "https://portal.azure.com/#view/Microsoft_Azure_ProjectOxford/CognitiveServicesHub/~/OpenAI",
    "https://azure.microsoft.com/en-us/products/ai-services/openai-service",
    "gpt-4o", "B", notes="Azure-hosted OpenAI models, enterprise SLA"))

_reg(ProviderDef("aws/bedrock", "AWS Bedrock", "openai",
    "https://bedrock-runtime.YOUR_REGION.amazonaws.com", "AWS_ACCESS_KEY_ID",
    "https://console.aws.amazon.com/bedrock/", "https://aws.amazon.com/bedrock/",
    "anthropic.claude-sonnet-4-20250514", "B",
    notes="AWS-managed foundation models"))

_reg(ProviderDef("google/vertex", "GCP Vertex AI", "openai",
    "https://YOUR_PROJECT_ID.vertexai.googlesapis.com/v1", "VERTEX_API_KEY",
    "https://console.cloud.google.com/vertex-ai", "https://cloud.google.com/vertex-ai",
    "claude-sonnet-4-20250514", "B",
    notes="GCP unified ML platform"))

_reg(ProviderDef("ibm/watsonx", "IBM watsonx.ai", "openai",
    "https://us-south.ml.cloud.ibm.com/ml/v1", "WATSONX_API_KEY",
    "https://dataplatform.cloud.ibm.com/wx/home", "https://www.ibm.com/watsonx",
    "meta-llama/llama-3-3-70b-instruct", "B",
    notes="Enterprise AI platform with governed data"))

_reg(ProviderDef("databricks", "Databricks FM API", "openai",
    "https://YOUR_WORKSPACE.databricks.com/serving-endpoints", "DATABRICKS_TOKEN",
    "https://console.databricks.com/",
    "https://www.databricks.com/product/machine-learning/foundation-model-apis",
    "databricks-meta-llama-3-1-70b-instruct", "B",
    notes="Databricks Foundation Model APIs"))

_reg(ProviderDef("oracle/oci", "Oracle OCI Gen AI", "openai",
    "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com", "OCI_API_KEY",
    "https://cloud.oracle.com/ Generative AI",
    "https://www.oracle.com/artificial-intelligence/generative-ai/",
    "cohere.command-r-plus", "B",
    notes="OCI generative AI service"))

_reg(ProviderDef("clarifai", "Clarifai AI Platform", "openai",
    "https://api.clarifai.com/v1", "CLARIFAI_API_KEY",
    "https://clarifai.com/settings/security", "https://clarifai.com",
    "anthropic.claude-sonnet-4", "B",
    notes="AI platform with model marketplace"))


# ── Tier C: Aggregators ────────────────────────────────────────────────────

_reg(ProviderDef("portkey", "Portkey", "openai",
    "https://api.portkey.ai/v1", "PORTKEY_API_KEY",
    "https://portkey.ai/app/api-keys", "https://portkey.ai",
    "openai/gpt-4o", "C",
    notes="Gateway with observability, multi-model routing"))

_reg(ProviderDef("kissapi", "KISS API", "openai",
    "https://kissapi.com/v1", "KISSAPI_API_KEY",
    "https://kissapi.com/keys", "https://kissapi.com",
    "gpt-4o", "C", notes="Unified API, free tier available"))

_reg(ProviderDef("brainiall", "BrainiAI", "openai",
    "https://api.brainiall.com/v1", "BRAINAI_API_KEY",
    "https://brainiall.com/keys", "https://brainiall.com",
    "gpt-4o", "C", notes="Multi-provider aggregation"))

_reg(ProviderDef("tokentrail", "TokenTrail", "openai",
    "https://api.tokentrail.dev/v1", "TOKENTRAIL_API_KEY",
    "https://tokentrail.dev/keys", "https://tokentrail.dev",
    "gpt-4o", "C", notes="Usage tracking across providers"))

_reg(ProviderDef("oneapi", "One API", "openai",
    "https://api.oneapi.com/v1", "ONEAPI_API_KEY",
    "https://oneapi.com/keys", "https://oneapi.com",
    "gpt-4o", "C", notes="Multi-provider aggregation, usage management"))


# ── Tier L: Local ───────────────────────────────────────────────────────────

_reg(ProviderDef("ollama", "Ollama", "openai",
    "http://localhost:11434/v1", "OLLAMA_API_KEY",
    "https://ollama.com/", "https://ollama.com",
    "llama3.2", "L", notes="Most popular local runner, easy setup"))

_reg(ProviderDef("vllm", "vLLM", "openai",
    "http://localhost:8000/v1", "VLLM_API_KEY",
    "https://docs.vllm.ai/", "https://vllm.ai",
    "meta-llama/Llama-3.1-8B-Instruct", "L",
    notes="High-throughput serving with PagedAttention"))

_reg(ProviderDef("lm-studio", "LM Studio", "openai",
    "http://localhost:1234/v1", "LMSTUDIO_API_KEY",
    "https://lmstudio.ai/docs", "https://lmstudio.ai",
    "local-model", "L", notes="Desktop GUI for local models, API mode"))

_reg(ProviderDef("localai", "LocalAI", "openai",
    "http://localhost:8080/v1", "LOCALAI_API_KEY",
    "https://localai.io/", "https://localai.io",
    "gpt-4", "L", notes="Self-hosted OpenAI-compatible, containerized"))

_reg(ProviderDef("llama-cpp", "llama.cpp", "openai",
    "http://localhost:8080/v1", "LLAMA_CPP_API_KEY",
    "https://github.com/ggml-org/llama.cpp",
    "https://github.com/ggml-org/llama.cpp", "local-model", "L",
    notes="Efficient CPU/GPU inference server"))

_reg(ProviderDef("tabbyapi", "TabbyAPI", "openai",
    "http://localhost:5000/v1", "TABBY_API_KEY",
    "https://github.com/theroyallab/tabbyAPI",
    "https://github.com/theroyallab/tabbyAPI", "local-model", "L",
    notes="ExLlamaV2-based fast inference API"))

_reg(ProviderDef("aphrodite", "Aphrodite Engine", "openai",
    "http://localhost:2242/v1", "APHRODITE_API_KEY",
    "https://github.com/PygmalionAI/aphrodite-engine",
    "https://github.com/PygmalionAI/aphrodite-engine", "local-model", "L",
    notes="High-throughput local engine with batching"))

_reg(ProviderDef("koboldcpp", "KoboldCPP", "openai",
    "http://localhost:5001/v1", "KOBOLDCPP_API_KEY",
    "https://github.com/LostRuins/koboldcpp",
    "https://github.com/LostRuins/koboldcpp", "local-model", "L",
    notes="GGUF runner with KoboldAI UI"))

_reg(ProviderDef("text-gen-webui", "text-generation-webui", "openai",
    "http://localhost:5000/v1", "TEXTGEN_API_KEY",
    "https://github.com/oobabooga/text-generation-webui",
    "https://github.com/oobabooga/text-generation-webui", "local-model", "L",
    notes="Oobabooga UI with OpenAI-compatible API"))

_reg(ProviderDef("jan", "Jan.ai", "openai",
    "http://localhost:1337/v1", "JAN_API_KEY",
    "https://jan.ai/docs/", "https://jan.ai",
    "local-model", "L",
    notes="Desktop app with built-in API server & model browser"))

_reg(ProviderDef("petals", "Petals", "openai",
    "http://localhost:31337/v1", "PETALS_API_KEY",
    "https://petals.dev/", "https://petals.dev",
    "meta-llama/Llama-3.1-8B-Instruct", "L",
    notes="Decentralized inference over BitTorrent-like network"))

_reg(ProviderDef("cortex", "Cortex", "openai",
    "http://localhost:39281/v1", "CORTEX_API_KEY",
    "https://cortex.so/docs/", "https://cortex.so",
    "local-model", "L",
    notes="Desktop local AI runner with API, GPU-accelerated"))

_reg(ProviderDef("koboldai", "KoboldAI Unified", "openai",
    "http://localhost:5000/v1", "KOBOLDAI_API_KEY",
    "https://github.com/henk717/KoboldAI", "https://koboldai.org",
    "local-model", "L",
    notes="KoboldAI story-telling focused interface with API"))


# ── Tier Z: Regional (Asia) ─────────────────────────────────────────────────

_reg(ProviderDef("zhipu/glm", "Zhipu AI (GLM)", "openai",
    "https://open.bigmodel.cn/api/paas/v4", "ZHIPU_API_KEY",
    "https://open.bigmodel.cn/usercenter/api-keys", "https://zhipu.ai",
    "glm-4-plus", "Z", notes="GLM-4 series, strong Chinese NLP"))

_reg(ProviderDef("moonshot/kimi", "Moonshot AI (Kimi)", "openai",
    "https://api.moonshot.cn/v1", "MOONSHOT_API_KEY",
    "https://platform.moonshot.cn/console/api-keys", "https://moonshot.cn",
    "kimi-k2", "Z", notes="Kimi long-context models, popular in China"))

_reg(ProviderDef("baichuan", "Baichuan AI", "openai",
    "https://api.baichuan-ai.com/v1", "BAICHUAN_API_KEY",
    "https://platform.baichuan-ai.com/api-key", "https://baichuan-ai.com",
    "Baichuan4-Turbo", "Z", notes="Baichuan models, strong Chinese gen"))

_reg(ProviderDef("minimax", "MiniMax", "openai",
    "https://api.minimax.chat/v1", "MINIMAX_API_KEY",
    "https://platform.minimaxi.com/user-center/api-key", "https://minimaxi.com",
    "MiniMax-Text-01", "Z", notes="MiniMax text & speech models"))

_reg(ProviderDef("qwen/alibaba", "Alibaba Qwen", "openai",
    "https://dashscope.aliyuncs.com/compatible-mode/v1", "QWEN_API_KEY",
    "https://bailian.console.aliyun.com/", "https://tongyi.aliyun.com/qianwen",
    "qwen-plus", "Z", notes="Qwen2.5 series, strong bilingual"))

_reg(ProviderDef("stepfun", "StepFun (Step)", "openai",
    "https://api.stepfun.com/v1", "STEPFUN_API_KEY",
    "https://platform.stepfun.com/api-key", "https://stepfun.com",
    "step-2-16k", "Z", notes="Step series models, competitive benchmarks"))

_reg(ProviderDef("lingyiwanwu", "Lingyiwanwu (Yi)", "openai",
    "https://api.lingyiwanwu.com/v1", "LINGYI_API_KEY",
    "https://platform.lingyiwanwu.com/", "https://lingyiwanwu.com",
    "yi-large-0619", "Z", notes="Yi series by 01.AI"))

_reg(ProviderDef("baidu/ernie", "Baidu ERNIE", "openai",
    "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat",
    "BAIDU_API_KEY",
    "https://console.bce.baidu.com/ai/#/ai/wenxin/overview", "https://yiyan.baidu.com",
    "ernie-4.0", "Z", notes="ERNIE 4.0, Baidu's flagship model"))

_reg(ProviderDef("tencent/hunyuan", "Tencent Hunyuan", "openai",
    "https://api.hunyuan.cloud.tencent.com/v1", "HUNYUAN_API_KEY",
    "https://console.cloud.tencent.com/hunyuan", "https://hunyuan.tencent.com",
    "hunyuan-lite", "Z", notes="Hunyuan series by Tencent"))

_reg(ProviderDef("bytedance/doubao", "ByteDance Doubao", "openai",
    "https://ark.cn-beijing.volces.com/api/v3", "DOUBAO_API_KEY",
    "https://console.volcengine.com/ark/", "https://www.doubao.com",
    "doubao-pro-32k", "Z", notes="ByteDance's Doubao via Volcano Engine"))

_reg(ProviderDef("iflytek/spark", "iFlytek Spark", "openai",
    "https://spark-api.xf-yun.com/v3.5/chat", "SPARK_API_KEY",
    "https://xinghuo.xfyun.cn/", "https://www.iflytek.com",
    "spark-3.5", "Z", notes="iFlytek Spark, strong Chinese speech & NLP"))


# Validate provider count — always runs, even with -O flag
if len(PROVIDER_REGISTRY) < 60:
    raise RuntimeError(f"Provider registry has only {len(PROVIDER_REGISTRY)} providers, expected >= 60")


# ── Base Provider ────────────────────────────────────────────────────────────

class BaseProvider(ABC):
    """Abstract base class for all LLM providers."""

    def __init__(self, defn: ProviderDef, api_key: str, model: Optional[str] = None, base_url: Optional[str] = None):
        self.defn = defn
        self.api_key = api_key
        self.model = model or defn.default_model
        self.custom_base_url = base_url
        self.base_url = self._resolve_base_url()

    def _resolve_base_url(self) -> str:
        return self.custom_base_url or self.defn.base_url

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> dict[str, Any]:
        ...

    @abstractmethod
    def stream_chat(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        ...

    @abstractmethod
    def list_models(self) -> list[dict[str, Any]]:
        ...


class OpenAICompatibleProvider(BaseProvider):
    """Provider using OpenAI-compatible API format."""

    def _request(self, method: str, endpoint: str, json_body: Optional[dict] = None) -> Any:
        import httpx
        base = self.base_url.rstrip('/')
        end = endpoint.lstrip('/')
        if base.endswith('/v1') and end.startswith('v1/'):
            end = end[3:]
        url = f"{base}/{end}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if json_body:
            json_body.setdefault("model", self.model)
        resp = httpx.request(method, url, headers=headers, json=json_body, timeout=60)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = f": {resp.text[:300]}"
            except Exception:
                pass
            raise RuntimeError(f"{self.defn.display} API error {resp.status_code}{detail}") from e
        return resp.json()

    def chat(self, messages: list[dict], **kwargs) -> dict[str, Any]:
        body = {"model": self.model, "messages": messages, **kwargs}
        return self._request("POST", "/v1/chat/completions", json_body=body)

    def stream_chat(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        import httpx
        import json
        
        body = {"model": self.model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}, **kwargs}
        base = self.base_url.rstrip('/')
        url = f"{base}/chat/completions"
        if base.endswith('/v1'):
            pass
        else:
            url = f"{base}/v1/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.stream("POST", url, headers=headers, json=body, timeout=60) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                    except Exception:
                        pass

    def list_models(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/models")
        return data.get("data", [])


class AnthropicProvider(BaseProvider):
    """Provider using Anthropic /v1/messages format."""

    def _request(self, json_body: dict) -> dict[str, Any]:
        import httpx
        url = f"{self.base_url.rstrip('/')}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        json_body.setdefault("model", self.model)
        resp = httpx.post(url, headers=headers, json=json_body, timeout=120)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = f": {resp.text[:300]}"
            except Exception:
                pass
            raise RuntimeError(f"Anthropic API error {resp.status_code}{detail}") from e
        return resp.json()

    def chat(self, messages: list[dict], **kwargs) -> dict[str, Any]:
        system_msg = None
        anthropic_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                anthropic_messages.append({"role": m["role"], "content": m["content"]})
        body = {"model": self.model, "messages": anthropic_messages, "max_tokens": 4096, **kwargs}
        if system_msg:
            body["system"] = system_msg
        body.pop("max_completion_tokens", None)
        return self._request(body)

    def stream_chat(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        import httpx
        import json
        
        system_msg = None
        anthropic_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
                continue
            anthropic_messages.append({"role": m["role"], "content": m["content"]})
        
        body = {"model": self.model, "messages": anthropic_messages, "max_tokens": 4096, "stream": True, **kwargs}
        if system_msg:
            body["system"] = system_msg
        body.pop("max_completion_tokens", None)
        
        url = f"{self.base_url.rstrip('/')}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        with httpx.stream("POST", url, headers=headers, json=body, timeout=120) as resp:
            resp.raise_for_status()
            event_name = None
            for line in resp.iter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("event: "):
                    event_name = line[7:].strip()
                elif line.startswith("data: "):
                    data_str = line[6:].strip()
                    try:
                        data = json.loads(data_str)
                        if event_name == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield text
                    except Exception:
                        pass

    def list_models(self) -> list[dict[str, Any]]:
        import httpx
        url = f"{self.base_url.rstrip('/')}/models"
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        resp = httpx.get(url, headers=headers, timeout=30)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Anthropic API error {resp.status_code}") from e
        return resp.json().get("data", [])


class GeminiProvider(BaseProvider):
    """Provider using Google Gemini API format."""

    def _resolve_base_url(self) -> str:
        base = self.custom_base_url or self.defn.base_url
        return base.rstrip("/")

    def _request(self, endpoint: str, json_body: dict) -> dict[str, Any]:
        import httpx
        url = f"{self.base_url}/{endpoint}"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        resp = httpx.post(url, headers=headers, json=json_body, timeout=120)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = f": {resp.text[:300]}"
            except Exception:
                pass
            raise RuntimeError(f"Gemini API error {resp.status_code}{detail}") from e
        return resp.json()

    def _stream_request(self, endpoint: str, json_body: dict) -> Generator[str, None, None]:
        import httpx
        import json
        import re
        
        url = f"{self.base_url}/{endpoint}"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        with httpx.stream("POST", url, headers=headers, json=json_body, timeout=120) as resp:
            resp.raise_for_status()
            buffer = ""
            for chunk in resp.iter_text():
                buffer += chunk
                matches = list(re.finditer(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', buffer))
                if matches:
                    for match in matches:
                        text_val = match.group(1)
                        try:
                            text_val = json.loads(f'"{text_val}"')
                        except Exception:
                            pass
                        yield text_val
                    buffer = buffer[matches[-1].end():]

    def chat(self, messages: list[dict], **kwargs) -> dict[str, Any]:
        gemini_contents = []
        system_text = None
        for m in messages:
            if m["role"] == "system":
                system_text = m["content"]
                continue
            role = "user" if m["role"] == "user" else "model"
            gemini_contents.append({"role": role, "parts": [{"text": m["content"]}]})
        body = {"contents": gemini_contents, **kwargs}
        if system_text:
            body["system_instruction"] = {"parts": [{"text": system_text}]}
        model_name = self.model
        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"
        return self._request(f"{model_name}:generateContent", body)

    def stream_chat(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        gemini_contents = []
        system_text = None
        for m in messages:
            if m["role"] == "system":
                system_text = m["content"]
                continue
            role = "user" if m["role"] == "user" else "model"
            gemini_contents.append({"role": role, "parts": [{"text": m["content"]}]})
        body = {"contents": gemini_contents, **kwargs}
        if system_text:
            body["system_instruction"] = {"parts": [{"text": system_text}]}
        model_name = self.model
        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"
        yield from self._stream_request(f"{model_name}:streamGenerateContent", body)

    def list_models(self) -> list[dict[str, Any]]:
        import httpx
        url = f"{self.base_url}/models"
        headers = {"x-goog-api-key": self.api_key}
        resp = httpx.get(url, headers=headers, timeout=30)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Gemini API error {resp.status_code}") from e
        return resp.json().get("models", [])


class ProviderFactory:
    """Factory to create provider instances by name."""

    API_TYPE_MAP = {
        "openai": OpenAICompatibleProvider,
        "anthropic": AnthropicProvider,
        "google": GeminiProvider,
    }

    @classmethod
    def create(
        cls,
        name: str,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> BaseProvider:
        defn = PROVIDER_REGISTRY.get(name)
        if defn is None:
            raise ValueError(
                f"Unknown provider '{name}'. "
                f"Use 'sudo provider list' to see available providers."
            )
        resolved_key = api_key or os.environ.get(defn.env_key)
        if not resolved_key:
            raise ValueError(
                f"No API key for '{name}'. "
                f"Set {defn.env_key} env var or use 'sudo provider key <key>'. "
                f"Get a key at: {defn.docs_url}"
            )
        resolved_base = base_url or defn.base_url
        provider_cls = cls.API_TYPE_MAP.get(defn.api_type, OpenAICompatibleProvider)
        return provider_cls(defn, resolved_key, model=model, base_url=resolved_base)
