from langchain_community.llms import Ollama
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq

class LLMs:
    def __init__(self, temperature=0.7):
        self.llm_map = {
            "GPT-3.5 Turbo": {
                "llm": lambda: ChatOpenAI(model_name="gpt-3.5-turbo", temperature=temperature),
                "max_context_length": 4096,
                "is_local": False
            },
            "GPT-4": {
                "llm": lambda: ChatOpenAI(model_name="gpt-4", temperature=temperature),
                "max_context_length": 32768,
                "is_local": False
            },
            "GPT-4o": {
                "llm": lambda: ChatOpenAI(model_name="gpt-4o", temperature=temperature),
                "max_context_length": 32768,
                "is_local": False
            },
            "Claude3 Opus": {
                "llm": lambda: ChatAnthropic(model_name="claude-3-opus-20240229", temperature=temperature),
                "max_context_length": 100000,
                "is_local": False
            },
            "llama3 Groq": {
                "llm": lambda: ChatGroq(model="llama3-70b-8192", temperature=temperature),
                "max_context_length": 8192,
                "is_local": False
            },
            "llama3": {
                "llm": lambda: Ollama(model="llama3", temperature=temperature),
                "max_context_length": 4096,
                "is_local": True
            },
            "openhermes": {
                "llm": lambda: Ollama(model="openhermes", temperature=temperature),
                "max_context_length": 4096,
                "is_local": True
            },
            "mistral": {
                "llm": lambda: Ollama(model="mistral", temperature=temperature),
                "max_context_length": 4096,
                "is_local": True
            },
            "mixtral": {
                "llm": lambda: Ollama(model="mixtral", temperature=temperature),
                "max_context_length": 4096,
                "is_local": True
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
