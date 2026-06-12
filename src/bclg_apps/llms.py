from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq

class LLMs:
    def __init__(self, temperature=0.4):
        self.llm_map = {
            "Llama 3.3": {
                "llm": lambda: ChatOllama(model="llama3.3", temperature=temperature),
                "max_context_length": 131072,
                "is_local": True
            },
            "Qwen 2.5 Coder": {
                "llm": lambda: ChatOllama(model="qwen2.5-coder", temperature=temperature),
                "max_context_length": 32768,
                "is_local": True
            },
            "Codestral": {
                "llm": lambda: ChatOllama(model="codestral", temperature=temperature),
                "max_context_length": 32768,
                "is_local": True
            },
            "Mistral": {
                "llm": lambda: ChatOllama(model="mistral", temperature=temperature),
                "max_context_length": 32768,
                "is_local": True
            },
            "GPT-5": {
                "llm": lambda: ChatOpenAI(model="gpt-5", temperature=temperature),
                "max_context_length": 272000,
                "is_local": False
            },
            "GPT-4.1": {
                "llm": lambda: ChatOpenAI(model="gpt-4.1", temperature=temperature),
                "max_context_length": 1000000,
                "is_local": False
            },
            "GPT-4o": {
                "llm": lambda: ChatOpenAI(model="gpt-4o", temperature=temperature),
                "max_context_length": 128000,
                "is_local": False
            },
            "Claude Opus 4.8": {
                "llm": lambda: ChatAnthropic(model="claude-opus-4-8", max_tokens=16000),
                "max_context_length": 1000000,
                "is_local": False
            },
            "Claude Sonnet 4.6": {
                "llm": lambda: ChatAnthropic(model="claude-sonnet-4-6", max_tokens=16000),
                "max_context_length": 1000000,
                "is_local": False
            },
            "Claude Haiku 4.5": {
                "llm": lambda: ChatAnthropic(model="claude-haiku-4-5", max_tokens=16000),
                "max_context_length": 200000,
                "is_local": False
            },
            "Llama 3.3 70B (Groq)": {
                "llm": lambda: ChatGroq(model="llama-3.3-70b-versatile", temperature=temperature),
                "max_context_length": 131072,
                "is_local": False
            }
        }

    def get_llm(self, llm_name):
        return self.llm_map.get(llm_name)["llm"]()

    def get_max_context_length(self, llm_name):
        return self.llm_map.get(llm_name)["max_context_length"]

    def is_local_model(self, llm_name):
        return self.llm_map.get(llm_name)["is_local"]

    def get_available_llms(self, model_type=None):
        if model_type == "local":
            return [name for name, info in self.llm_map.items() if info["is_local"]]
        elif model_type == "remote":
            return [name for name, info in self.llm_map.items() if not info["is_local"]]
        else:
            return list(self.llm_map.keys())
