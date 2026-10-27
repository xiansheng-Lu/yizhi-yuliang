"""离线解析样例与候选提示词；加载不调用模型或平台。"""

from .registry import PromptBundle, load_bundle

__all__ = ["PromptBundle", "load_bundle"]
