import os
import ollama
from openai import OpenAI

class LLMService:
    LEVELS = {"debug": 10, "info": 20, "error": 40, "none": 100}

    def __init__(self, log_level: str = "error"):
        self.log_level = log_level if log_level in self.LEVELS else "error"
        self.available_models = []
        self.discover_models()

    def set_log_level(self, log_level: str):
        if log_level in self.LEVELS:
            self.log_level = log_level

    def _log(self, level: str, message: str):
        if self.LEVELS.get(level, 40) >= self.LEVELS.get(self.log_level, 40):
            print(message)

    def discover_models(self):
        self._log("info", "正在发现可用的大模型服务...")
        self.discover_ollama_models()
        self.discover_lm_studio_models()

    def discover_ollama_models(self):
        try:
            response = ollama.list()
            self._log("debug", f"Ollama 原始响应: {response}")
            if 'models' in response:
                model_list = response['models']
                discovered_names = []
                for model_info in model_list:
                    model_name = None
                    if isinstance(model_info, dict):
                        # It's a dictionary
                        model_name = model_info.get('name')
                        if not model_name:
                            model_name = model_info.get('model')
                    else:
                        # It's an object
                        if hasattr(model_info, 'name'):
                            model_name = getattr(model_info, 'name')
                        elif hasattr(model_info, 'model'): # fallback based on repr
                            model_name = getattr(model_info, 'model')

                    if model_name:
                        self.available_models.append(f"ollama:{model_name}")
                        discovered_names.append(model_name)

                if discovered_names:
                    self._log("info", f"发现Ollama模型: {discovered_names}")
                else:
                    self._log("debug", "在Ollama响应中找到了模型列表，但无法提取模型名称。")
            else:
                self._log("debug", "Ollama 响应中没有 'models' 键。")
        except Exception as e:
            self._log("error", f"无法连接到Ollama服务或解析响应时出错: {e}")

    def discover_lm_studio_models(self):
        try:
            client = OpenAI(base_url="http://localhost:1234/v1", api_key="not-needed")
            models = client.models.list()
            for model in models.data:
                self.available_models.append(f"lm_studio:{model.id}")
            self._log("info", f"发现LM Studio模型: {[model.id for model in models.data]}")
        except Exception as e:
            self._log("error", f"无法连接到LM Studio服务: {e}")

    def get_best_model(self):
        # 在这里根据需要选择最佳模型
        if self.available_models:
            return self.available_models[0]
        return None

    def query(self, model, prompt):
        self._log("debug", f"正在向 {model} 发送查询: {prompt}")
        try:
            service, model_name = model.split(':', 1)
            if service == 'ollama':
                response = ollama.chat(model=model_name, messages=[{'role': 'user', 'content': prompt}])
                return response['message']['content']
            elif service == 'lm_studio':
                client = OpenAI(base_url="http://localhost:1234/v1", api_key="not-needed")
                completion = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt}
                    ]
                )
                return completion.choices[0].message.content
        except Exception as e:
            self._log("error", f"查询模型 {model} 失败: {e}")
            return "抱歉，我无法回答您的问题。"

if __name__ == "__main__":
    llm_service = LLMService()
    print(f"可用模型: {llm_service.available_models}")
    best_model = llm_service.get_best_model()
    if best_model:
        response = llm_service.query(best_model, "你好！")
        print(f"模型响应: {response}")