import os

# 測試不呼叫真的模型;要測 AI 的測試自己注入假的服務
os.environ["FACTORYMIND_LLM"] = "off"
