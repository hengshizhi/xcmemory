import asyncio, sys
sys.path.insert(0, 'chat')
from llm_client import LLMClient
l = LLMClient('https://api.deepseek.com', 'sk-0f600adc8bf145668ba89b51527f6e14', 'deepseek-v4-flash')
async def t():
    r = await l.complete([{"role":"user","content":"say hi in one word"}])
    print(f"len={len(r)} ok={'yes' if r else 'no'}")
asyncio.run(t())
